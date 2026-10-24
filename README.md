# Superlight Battery

A tiny Windows tray app that shows the battery percentage of a **Logitech G Pro X Superlight** (1 and 2) — no G HUB required.

- Battery % drawn right on the tray icon, colour-coded (green > 50%, yellow > 20%, red below)
- Hover for details, including charging status
- Windows notification when the battery drops below 15%
- Works over the Lightspeed receiver or a USB cable
- Talks to the mouse directly over Logitech's HID++ 2.0 protocol

## Download

Grab `SuperlightBattery.exe` from the [latest release](../../releases/latest) and run it. No install needed.

To start it with Windows: press `Win + R`, type `shell:startup`, and drop a shortcut to the exe in that folder.

> Windows SmartScreen may warn about an unsigned app on first run — click **More info → Run anyway**.

## Run from source

```
pip install -r requirements.txt
python superlight_battery.py          # tray mode
python superlight_battery.py --once   # print battery once and exit
```

## How it works

The app finds Logitech's vendor-specific HID interface (usage page `0xFF00`), then sends HID++ 2.0 long reports to the mouse. It asks the root feature (`0x0000`) which battery feature the device supports and uses the first one available:

| Feature | ID | Used by | Reading |
|---|---|---|---|
| Unified Battery | `0x1004` | Superlight 2 | Exact percentage |
| Battery Voltage | `0x1001` | Superlight (original) | Millivolts → % via a Li-ion discharge curve |
| Battery Status | `0x1000` | Older devices | Percentage |

Device index `0x01` is tried first (paired through the receiver), then `0xFF` (wired).

## Troubleshooting

- **Icon shows `?`** — the mouse is asleep (move it), or G HUB is holding the device. Fully quit G HUB from the tray and click *Refresh now*.
- **Original Superlight reading jumps a few %** — it only reports voltage, so the percentage is an estimate (±5–10%).

## Building the exe

The exe is built automatically by GitHub Actions on a Windows runner (`.github/workflows/build.yml`). Pushing a tag like `v1.0.0` builds it and attaches it to a GitHub Release.

To build locally on Windows:

```
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --noconsole --name SuperlightBattery --hidden-import pystray._win32 superlight_battery.py
```

## License

MIT
