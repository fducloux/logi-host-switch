# logi-host-switch

Switch a Logitech MX Keys / MX Master (or any Easy-Switch capable Logitech
peripheral paired to the same Unifying/Bolt-style USB receiver) between its 3
paired hosts using global hotkeys, without Logi Options+.

`Ctrl+Shift+Cmd+1/2/3` on macOS, `Ctrl+Shift+Alt+1/2/3` on Windows (there's
no Cmd key there) -> switch every paired device on the receiver to host
1 / 2 / 3.

Runs entirely in user space: no admin/root install or launch required on
either macOS or Windows. On macOS, the OS will prompt you once to grant
"Input Monitoring" (System Settings -> Privacy & Security) to whatever
process runs this, so it can see the global hotkey -- that's a one-time user
consent dialog, not an admin install.

## How it works

Logitech's Unifying/Bolt receivers pass through a HID++ protocol to whatever
is paired to them. Modern Easy-Switch peripherals (MX Keys, MX Master 3/3S,
etc.) implement HID++ 2.0 feature `0x1814` ("Change Host"), the same command
their physical Easy-Switch button sends. This app talks that protocol
directly over the receiver's vendor HID interface via `hidapi` -- no Logi
software needed.

Devices must be awake (recently typed on / clicked) to answer a switch
request; a device that's been idle for a while may take a couple of retries
to respond after you press the hotkey (the keyboard itself is normally awake
right when you press the hotkey since you just used it; the mouse may lag by
up to ~1-2s).

## Setup (from source)

```bash
python3 -m venv venv
```

macOS:
```bash
venv/bin/pip install -r requirements.txt
```

Windows (PowerShell):
```powershell
venv\Scripts\pip install -r requirements.txt
```

Run:

macOS:
```bash
venv/bin/python -m logi_switch.app
```

Windows:
```powershell
venv\Scripts\python -m logi_switch.app
```

A small tray icon appears; the app listens for the hotkeys in the
background. `--no-tray` runs headless (console only).

## macOS: native hidapi library

The `hid` PyPI package needs `libhidapi` available on the system; it isn't
bundled in the pip wheel on macOS. Install it once via Homebrew:

```bash
brew install hidapi
```

If you see `Unable to load any of the following libraries...` at startup,
either the above wasn't run, or the library isn't on the default search
path -- run with:

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib venv/bin/python -m logi_switch.app
```

(A packaged build bundles this dylib next to the executable so end users
never see this -- see Packaging below.)

## Packaging into a standalone app (no Python install needed to run)

Both platforms use PyInstaller, producing a single executable users can just
double-click / run -- no `pip install`, no admin rights.

macOS (run on a Mac):
```bash
venv/bin/pip install pyinstaller
venv/bin/pyinstaller --onefile --windowed --name "Logi Host Switch" \
  --add-binary "/opt/homebrew/lib/libhidapi.dylib:." \
  logi_switch/app.py
```
Output: `dist/Logi Host Switch.app`.

Windows (must be run on a Windows machine -- PyInstaller doesn't cross-compile):
```powershell
venv\Scripts\pip install pyinstaller
venv\Scripts\pyinstaller --onefile --windowed --name "LogiHostSwitch" logi_switch\app.py
```
Output: `dist\LogiHostSwitch.exe`. The `hid` wheel on Windows bundles its own
`hidapi.dll`, so no extra step is needed there.

Neither build requires an installer or elevated privileges -- the resulting
file runs directly from wherever you put it (Desktop, `%LOCALAPPDATA%`,
`~/Applications`, a USB stick, etc).

## Customizing

- Hotkeys: edit `REQUIRED_MODIFIERS` / `MODIFIER_KEYS` / `DIGIT_VKS` near the
  top of `logi_switch/app.py`.
- Receiver/device discovery is automatic -- it enumerates whatever Logitech
  receiver is plugged in and switches every paired device that responds, so
  no per-machine device IDs need to be hardcoded.

## Limitations

- Only affects devices connected through the USB receiver's Easy-Switch
  channel management -- a device paired directly over Bluetooth (no
  receiver) isn't reachable by this tool the same way it would be by the
  device's own Bluetooth Easy-Switch button.
- Requires the target peripheral to actually support HID++ 2.0 feature
  `0x1814`. Confirmed present on MX Keys and MX Master 3; most modern
  MX-series devices have it, cheaper Logitech peripherals often don't.
