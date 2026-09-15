"""Minimal Logitech HID++ client: enumerate paired devices on a Unifying/Bolt-style
USB receiver and switch their active host (Easy-Switch / HID++2.0 feature 0x1814).

Protocol framing is derived from Solaar (github.com/pwr-Solaar/Solaar), reimplemented
standalone here (no GTK / Linux-only dependencies) so it runs on plain hidapi on both
macOS and Windows.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass

import hid

LOGITECH_VID = 0x046D

# USB PIDs for Logitech's various pass-through receivers (Unifying, Unifying v2, Bolt).
# All speak the same HID++ framing over the vendor-specific (usage_page 0xFF00) interface.
RECEIVER_PIDS = {0xC52B, 0xC52F, 0xC531, 0xC539, 0xC53A, 0xC53F, 0xC541, 0xC548}

CHANGE_HOST_FEATURE = 0x1814

SHORT_REPORT_ID = 0x10
LONG_REPORT_ID = 0x11

ERROR_NAMES = {
    0x01: "UNKNOWN",
    0x02: "INVALID_ARGUMENT",
    0x03: "OUT_OF_RANGE",
    0x04: "HARDWARE_ERROR",
    0x05: "LOGITECH_ERROR",
    0x06: "INVALID_FEATURE_INDEX",
    0x07: "INVALID_FUNCTION",
    0x08: "BUSY",
    0x09: "UNSUPPORTED",
}


class HidppError(Exception):
    def __init__(self, code: int):
        self.code = code
        super().__init__(ERROR_NAMES.get(code, f"error 0x{code:02X}"))


@dataclass
class PairedDevice:
    slot: int  # 1-based receiver pairing slot == HID++ device index
    name: str


def find_receiver_path() -> bytes | None:
    """Return the hidapi path for the receiver's HID++ (vendor usage page) interface."""
    for d in hid.enumerate(LOGITECH_VID, 0):
        if d["product_id"] in RECEIVER_PIDS and d.get("usage_page") == 0xFF00:
            return d["path"]
    return None


class Receiver:
    def __init__(self, path: bytes | None = None, sw_id: int = 0x5):
        path = path or find_receiver_path()
        if path is None:
            raise RuntimeError("no Logitech receiver found (is it plugged in?)")
        self._dev = hid.Device(path=path)
        self._dev.nonblocking = True
        self._sw_id = sw_id & 0xF
        self._feature_index_cache: dict[tuple[int, int], int] = {}

    def close(self):
        self._dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- low level -----------------------------------------------------

    def _drain(self, timeout: float) -> list[bytes]:
        end = time.time() + timeout
        packets = []
        while time.time() < end:
            r = self._dev.read(20, timeout=100)
            if r:
                packets.append(bytes(r))
        return packets

    def _request(
        self,
        devnumber: int,
        sub_hi: int,
        function_nibble: int,
        params: bytes = b"",
        timeout: float = 0.6,
        expect_reply: bool = True,
    ) -> bytes | None:
        sub_lo = ((function_nibble & 0xF) << 4) | self._sw_id
        body = params.ljust(3, b"\x00")[:3]
        msg = bytes([SHORT_REPORT_ID, devnumber, sub_hi, sub_lo]) + body
        self._dev.write(msg)
        if not expect_reply:
            return None
        for p in self._drain(timeout):
            if len(p) < 4 or p[1] != devnumber:
                continue
            if p[2] == 0x8F and p[3] == sub_hi:
                # error frame: [report, dev, 0x8F, sub_hi, sub_lo, error_code, 0]
                raise HidppError(p[5] if len(p) > 5 else 0)
            if p[2] == sub_hi and p[3] == sub_lo:
                return p[4:]
        return None

    def _request_retry(self, devnumber, sub_hi, function_nibble, params=b"", tries=6, gap=0.15):
        last_err = None
        for _ in range(tries):
            try:
                reply = self._request(devnumber, sub_hi, function_nibble, params, timeout=0.25)
                if reply is not None:
                    return reply
            except HidppError as e:
                last_err = e
            time.sleep(gap)
        if last_err:
            raise last_err
        return None

    def _get_feature_index_multi(self, slots: list[int], feature_id: int, tries=6, gap=0.15) -> dict:
        """Resolve a feature index on several devices concurrently (one shared
        write-then-drain round per try, instead of retrying each device in turn --
        cuts worst-case latency from sum(devices) to roughly max(devices)."""
        params = struct.pack("!H", feature_id).ljust(3, b"\x00")
        pending = list(slots)
        results: dict[int, int] = {}
        for _ in range(tries):
            if not pending:
                break
            sub_lo = (0 << 4) | self._sw_id
            for slot in pending:
                msg = bytes([SHORT_REPORT_ID, slot, 0x00, sub_lo]) + params
                self._dev.write(msg)
            still_pending = set(pending)
            for p in self._drain(0.25):
                if len(p) < 5:
                    continue
                dev = p[1]
                if dev not in still_pending:
                    continue
                if p[2] == 0x8F and p[3] == 0x00:
                    continue  # error for this device this round; retry it
                if p[2] == 0x00 and p[3] == sub_lo:
                    idx = p[4]
                    if idx:
                        results[dev] = idx
                    still_pending.discard(dev)
            pending = [s for s in pending if s in still_pending]
            if pending:
                time.sleep(gap)
        return results

    # -- receiver-level (HID++1.0 register) queries ---------------------

    def paired_devices(self) -> list[PairedDevice]:
        devices = []
        for slot in range(1, 7):
            name = self._get_device_name(slot)
            if name:
                devices.append(PairedDevice(slot=slot, name=name))
        return devices

    def _get_device_name(self, slot: int) -> str | None:
        param = 0x40 + (slot - 1)  # InfoSubRegisters.DEVICE_NAME + slot - 1
        msg = bytes([SHORT_REPORT_ID, 0xFF, 0x83, 0xB5, param, 0x00, 0x00])
        self._dev.write(msg)
        for p in self._drain(0.5):
            if len(p) >= 6 and p[0] == LONG_REPORT_ID and p[2] == 0x83 and p[3] == 0xB5 and p[4] == param:
                name_len = p[5]
                return bytes(p[6 : 6 + name_len]).decode("ascii", errors="replace")
            if len(p) >= 4 and p[0] == SHORT_REPORT_ID and p[2] == 0x8F:
                return None
        return None

    # -- device-level (HID++2.0 feature) calls --------------------------

    def _get_feature_index(self, slot: int, feature_id: int) -> int | None:
        reply = self._request_retry(slot, 0x00, 0x0, struct.pack("!H", feature_id))
        if not reply:
            return None
        idx = reply[0]
        return idx or None

    def get_host_info(self, slot: int) -> tuple[int, int] | None:
        """Returns (num_hosts, current_host_0based) for the device at `slot`."""
        idx = self._get_feature_index(slot, CHANGE_HOST_FEATURE)
        if idx is None:
            return None
        reply = self._request_retry(slot, idx, 0x0)
        if not reply:
            return None
        return reply[0], reply[1]

    def set_host(self, slot: int, host_index_0based: int) -> bool:
        """Switch the device at `slot` to host `host_index_0based` (0, 1, 2, ...)."""
        idx = self._get_feature_index(slot, CHANGE_HOST_FEATURE)
        if idx is None:
            return False
        # write is fire-and-forget on real hardware (device jumps host immediately
        # and won't reply on this channel), so don't wait for a reply.
        self._request(slot, idx, 0x1, bytes([host_index_0based]), expect_reply=False)
        return True

    def switch_all(self, slots: list[int], host_index_0based: int) -> dict[int, bool]:
        """Switch several devices to the same host, resolving their Change Host
        feature index concurrently. Indices are cached on this Receiver instance
        (kept open across calls) so repeat presses skip discovery entirely."""
        cache_key_slots = [s for s in slots if (s, CHANGE_HOST_FEATURE) not in self._feature_index_cache]
        if cache_key_slots:
            found = self._get_feature_index_multi(cache_key_slots, CHANGE_HOST_FEATURE)
            for slot, idx in found.items():
                self._feature_index_cache[(slot, CHANGE_HOST_FEATURE)] = idx

        results = {}
        for slot in slots:
            idx = self._feature_index_cache.get((slot, CHANGE_HOST_FEATURE))
            if idx is None:
                results[slot] = False
                continue
            self._request(slot, idx, 0x1, bytes([host_index_0based]), expect_reply=False)
            results[slot] = True
        return results
