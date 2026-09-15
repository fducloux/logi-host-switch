"""Background app: switches every paired Logitech device (keyboard + mouse)
on the receiver to host 1/2/3 via a hotkey -- Ctrl+Shift+Cmd+1/2/3 on macOS,
Ctrl+Shift+Alt+1/2/3 on Windows (Cmd doesn't exist there).

Runs entirely in user space -- no admin/root needed on either platform.
On macOS you'll be asked once to grant "Input Monitoring" to whatever runs this
(Terminal, or the built app) so it can see the global hotkey.
"""
from __future__ import annotations

import logging
import sys
import threading

from pynput import keyboard
from pynput.keyboard import Key

from .hidpp import Receiver, find_receiver_path

log = logging.getLogger("logi_switch")

MODIFIER_KEYS = {
    Key.ctrl_l: "ctrl", Key.ctrl_r: "ctrl",
    Key.shift_l: "shift", Key.shift_r: "shift",
    Key.alt_l: "alt", Key.alt_r: "alt",
    Key.cmd_l: "cmd", Key.cmd_r: "cmd",
}
REQUIRED_MODIFIERS = {"ctrl", "shift", "cmd"} if sys.platform == "darwin" else {"ctrl", "shift", "alt"}

# Matching digits by raw hardware key code (not character) because on macOS
# holding Option/Alt remaps what character the number row produces (e.g.
# Option+2 types "™", not "2"), which silently breaks pynput's normal
# string-based "<ctrl>+<alt>+2" hotkey matching for exactly this modifier
# combo. vk is the OS keycode and is unaffected by modifier state.
if sys.platform == "darwin":
    DIGIT_VKS = {18: 0, 19: 1, 20: 2}  # keys 1, 2, 3
else:
    DIGIT_VKS = {0x31: 0, 0x32: 1, 0x33: 2}  # Windows VK_1..VK_3


class Switcher:
    """Keeps one Receiver connection (and its feature-index cache) open across
    switches, and resolves all devices concurrently, so repeat hotkey presses
    are near-instant instead of paying the device-discovery cost every time."""

    def __init__(self):
        self._lock = threading.Lock()
        self._recv: Receiver | None = None
        self._devices: list | None = None

    def _ensure_receiver(self) -> Receiver | None:
        if self._recv is not None:
            return self._recv
        path = find_receiver_path()
        if path is None:
            return None
        self._recv = Receiver(path)
        return self._recv

    def _drop_receiver(self):
        if self._recv is not None:
            try:
                self._recv.close()
            except Exception:
                pass
        self._recv = None
        self._devices = None

    def switch_to(self, host_index: int):
        with self._lock:
            for attempt in (1, 2):
                recv = self._ensure_receiver()
                if recv is None:
                    log.warning("no receiver found; is it plugged in?")
                    return
                try:
                    if self._devices is None:
                        self._devices = recv.paired_devices()
                    if not self._devices:
                        log.warning("receiver found but no paired devices responded")
                        self._devices = None  # allow retry on next press
                        return
                    results = recv.switch_all([d.slot for d in self._devices], host_index)
                    for dev in self._devices:
                        status = "sent" if results.get(dev.slot) else "no reply (device asleep?)"
                        log.info("host %d -> %s (slot %d): %s", host_index + 1, dev.name, dev.slot, status)
                    return
                except Exception as e:
                    # USB path went stale (receiver unplugged/re-enumerated, sleep/wake, etc).
                    # Drop the cached handle and reconnect once before giving up.
                    log.warning("receiver connection lost (%s); reconnecting", e)
                    self._drop_receiver()
            log.warning("still unreachable after reconnect attempt")


class HotkeyListener:
    """Ctrl+Shift+Alt+1/2/3, matched on held modifiers + raw digit keycode.

    (Not pynput's GlobalHotKeys: that matches digits by character, which
    breaks under Alt on macOS -- see the DIGIT_VKS comment above.)
    """

    def __init__(self, on_switch):
        self._on_switch = on_switch
        self._held = set()

    def on_press(self, key):
        mod = MODIFIER_KEYS.get(key)
        if mod:
            self._held.add(mod)
            return
        vk = getattr(key, "vk", None)
        if vk in DIGIT_VKS and REQUIRED_MODIFIERS <= self._held:
            self._on_switch(DIGIT_VKS[vk])

    def on_release(self, key):
        mod = MODIFIER_KEYS.get(key)
        if mod:
            self._held.discard(mod)


HOTKEY_LABEL = "Ctrl+Shift+Cmd" if sys.platform == "darwin" else "Ctrl+Shift+Alt"


def run(use_tray: bool = True):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    switcher = Switcher()

    def on_switch(host_index):
        log.info("hotkey: switch to host %d", host_index + 1)
        threading.Thread(target=switcher.switch_to, args=(host_index,), daemon=True).start()

    hotkeys = HotkeyListener(on_switch)
    listener = keyboard.Listener(on_press=hotkeys.on_press, on_release=hotkeys.on_release)
    listener.start()
    log.info("listening for %s+1 / +2 / +3", HOTKEY_LABEL)

    if use_tray:
        try:
            _run_tray(listener)
            return
        except Exception as e:  # pragma: no cover - environment dependent
            log.warning("tray icon unavailable (%s); running headless", e)

    try:
        listener.join()
    except KeyboardInterrupt:
        listener.stop()


def _run_tray(listener):
    import pystray
    from PIL import Image, ImageDraw

    def make_icon():
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 56, 56), fill=(0, 122, 255, 255))
        d.text((22, 20), "L", fill=(255, 255, 255, 255))
        return img

    def on_quit(icon, item):
        listener.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Logi Host Switch", None, enabled=False),
        pystray.MenuItem(f"{HOTKEY_LABEL}+1/2/3 to switch host", None, enabled=False),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("logi_switch", make_icon(), "Logi Host Switch", menu)
    icon.run()


if __name__ == "__main__":
    use_tray = "--no-tray" not in sys.argv
    run(use_tray=use_tray)
