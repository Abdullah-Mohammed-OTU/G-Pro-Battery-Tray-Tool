"""
Superlight Battery - a tiny Windows tray app that shows the battery % of a
Logitech G Pro X Superlight (1 or 2), wireless via the Lightspeed receiver or
wired over USB. Talks HID++ 2.0 directly, so G HUB is not required.

Usage:
    python superlight_battery.py          # tray icon, refreshes every 60 s
    python superlight_battery.py --once   # print battery once and exit
"""
import sys
import threading
import time

import hid  # pip install hidapi   (NOT the "hid" package)

LOGI_VID = 0x046D
# Lightspeed receivers and wired-mode PIDs for Superlight models; tried first.
KNOWN_PIDS = {0xC539, 0xC547, 0xC54D, 0xC094, 0xC09B}

VENDOR_USAGE_PAGE = 0xFF00
LONG_USAGE = 0x0002          # Windows exposes HID++ long reports on this collection
HIDPP_LONG = 0x11
LONG_LEN = 20
SW_ID = 0x0A

DEVICE_INDEXES = (0x01, 0xFF)  # 0x01 = paired via receiver, 0xFF = wired

FEAT_ROOT = 0x0000
FEAT_BATTERY_STATUS = 0x1000
FEAT_BATTERY_VOLTAGE = 0x1001   # original Superlight
FEAT_UNIFIED_BATTERY = 0x1004   # Superlight 2

POLL_SECONDS = 60
LOW_BATTERY = 15

# Approximate Li-ion discharge curve (mV -> %), interpolated between points.
VOLTAGE_CURVE = [
    (4186, 100), (4067, 90), (3989, 80), (3922, 70), (3859, 60), (3811, 50),
    (3778, 40), (3751, 30), (3717, 20), (3671, 10), (3646, 5), (3579, 2), (3500, 0),
]


class HidppError(Exception):
    pass


def voltage_to_percent(mv):
    if mv >= VOLTAGE_CURVE[0][0]:
        return 100
    for (v_hi, p_hi), (v_lo, p_lo) in zip(VOLTAGE_CURVE, VOLTAGE_CURVE[1:]):
        if mv >= v_lo:
            return round(p_lo + (p_hi - p_lo) * (mv - v_lo) / (v_hi - v_lo))
    return 0


class Hidpp:
    def __init__(self, path, dev_index):
        self.dev = hid.device()
        self.dev.open_path(path)
        self.idx = dev_index

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass

    def request(self, feat_index, function, params=(), timeout=1.0):
        func_sw = (function << 4) | SW_ID
        msg = bytes([HIDPP_LONG, self.idx, feat_index, func_sw]) + bytes(params)
        self.dev.write(list(msg.ljust(LONG_LEN, b"\x00")))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self.dev.read(64, 100)
            if not r or len(r) < 5 or r[0] != HIDPP_LONG or r[1] != self.idx:
                continue
            if r[2] == 0xFF and r[3] == feat_index and r[4] == func_sw:
                raise HidppError(f"HID++ error 0x{r[5]:02X}")
            if r[2] == feat_index and r[3] == func_sw:
                return bytes(r[4:])
        raise TimeoutError("no response (mouse asleep, off, or wrong device)")

    def feature_index(self, feature_id):
        p = self.request(0x00, 0, (feature_id >> 8, feature_id & 0xFF))
        return p[0] or None


def read_battery(h):
    """Return (percent, charging, source) using whichever battery feature exists."""
    idx = h.feature_index(FEAT_UNIFIED_BATTERY)
    if idx:
        p = h.request(idx, 1)                     # get_status
        return p[0], p[2] in (1, 2), "unified"

    idx = h.feature_index(FEAT_BATTERY_VOLTAGE)
    if idx:
        p = h.request(idx, 0)                     # get_battery_info
        mv = (p[0] << 8) | p[1]
        return voltage_to_percent(mv), bool(p[2] & 0x80), f"{mv} mV"

    idx = h.feature_index(FEAT_BATTERY_STATUS)
    if idx:
        p = h.request(idx, 0)                     # get_battery_level_status
        return p[0], p[2] in (1, 2, 3), "status"

    raise HidppError("device has no battery feature")


def candidate_paths():
    devs = [
        d for d in hid.enumerate(LOGI_VID, 0)
        if d["usage_page"] == VENDOR_USAGE_PAGE and d["usage"] == LONG_USAGE
    ]
    devs.sort(key=lambda d: d["product_id"] not in KNOWN_PIDS)
    return [d["path"] for d in devs]


def get_battery():
    """Return dict(percent, charging, source) or raise the last error seen."""
    last_err = RuntimeError("no Logitech HID++ device found")
    for path in candidate_paths():
        for dev_index in DEVICE_INDEXES:
            try:
                h = Hidpp(path, dev_index)
            except OSError as e:
                last_err = e
                break
            try:
                pct, charging, source = read_battery(h)
                return {"percent": pct, "charging": charging, "source": source}
            except (HidppError, TimeoutError, OSError) as e:
                last_err = e
            finally:
                h.close()
    raise last_err


# ---------------------------------------------------------------- tray UI ---

def make_icon(text, color):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 63, 63), radius=12, fill=color)
    size = 30 if len(text) >= 3 else 40
    try:
        font = ImageFont.truetype("arialbd.ttf", size)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((64 - w) / 2 - box[0], (64 - h) / 2 - box[1]), text,
           font=font, fill=(0, 0, 0, 255))
    return img


def color_for(pct):
    if pct is None:
        return (150, 150, 150, 255)
    if pct > 50:
        return (80, 200, 120, 255)
    if pct > 20:
        return (240, 200, 60, 255)
    return (230, 80, 70, 255)


def run_tray():
    import pystray

    wake = threading.Event()
    state = {"warned": False, "running": True}

    def refresh(icon):
        try:
            b = get_battery()
            pct = b["percent"]
            bolt = " ⚡ charging" if b["charging"] else ""
            icon.icon = make_icon(str(pct), color_for(pct))
            icon.title = f"Superlight: {pct}%{bolt}"
            if pct <= LOW_BATTERY and not b["charging"] and not state["warned"]:
                icon.notify(f"Mouse battery at {pct}%", "Superlight Battery")
                state["warned"] = True
            elif pct > LOW_BATTERY + 5 or b["charging"]:
                state["warned"] = False
        except Exception as e:
            icon.icon = make_icon("?", color_for(None))
            icon.title = f"Superlight: unavailable ({e})"[:127]

    def loop(icon):
        icon.visible = True
        while state["running"]:
            refresh(icon)
            wake.wait(POLL_SECONDS)
            wake.clear()

    def on_refresh(icon, item):
        wake.set()

    def on_quit(icon, item):
        state["running"] = False
        wake.set()
        icon.stop()

    icon = pystray.Icon(
        "superlight_battery",
        make_icon("…", color_for(None)),
        "Superlight: checking…",
        menu=pystray.Menu(
            pystray.MenuItem("Refresh now", on_refresh, default=True),
            pystray.MenuItem("Quit", on_quit),
        ),
    )
    icon.run(setup=loop)


if __name__ == "__main__":
    if "--once" in sys.argv:
        try:
            b = get_battery()
            print(f"{b['percent']}%{' (charging)' if b['charging'] else ''}  [{b['source']}]")
        except Exception as e:
            print(f"Could not read battery: {e}")
            sys.exit(1)
    else:
        run_tray()
