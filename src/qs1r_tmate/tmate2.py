"""Reader for the Elad / WoodBox Radio Tmate 2 USB controller.

The Tmate 2 is a vendor-defined HID device (usage page 0xFF00) that sits on the
stock Windows ``HidUsb`` driver, so no vendor DLL is involved.  Its report
descriptor declares a 64-byte input report, a 64-byte output report and a
1-byte feature report, and it declares *no* report IDs - the bytes below are
raw payload.

Input report layout (verified on HW1.1 / FW1.3, see docs/tmate2-hid.md):

    byte  0      marker, normally 0x01
    bytes 1..2   main encoder position, int16 LE, absolute
    bytes 3..4   E1 encoder position,   int16 LE, absolute
    bytes 5..6   E2 encoder position,   int16 LE, absolute
    bytes 7..8   button bitmap, 9 bits, 0 = pressed
    bytes 9..63  padding, not meaningful

The encoders report an absolute position that survives between reports, so
motion is the difference between successive reports.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

import hid

log = logging.getLogger(__name__)

VENDOR_ID = 0x1721
PRODUCT_ID = 0x0614

REPORT_SIZE = 64
PAYLOAD_SIZE = 9

#: Button bit positions inside the 9-bit bitmap.  A bit reads 0 while pressed.
BUTTONS = {
    0: "F1",
    1: "F2",
    2: "F3",
    3: "F4",
    4: "F5",
    5: "F6",
    6: "MAIN",  # push on the main tuning knob
    7: "E1",    # push on the left small encoder
    8: "E2",    # push on the right small encoder
}

ENCODERS = ("MAIN", "E1", "E2")

_INT16_SPAN = 1 << 16
_INT16_HALF = 1 << 15


@dataclass(frozen=True)
class EncoderEvent:
    """Movement of one encoder since the previous report."""

    encoder: str
    delta: int
    position: int


@dataclass(frozen=True)
class ButtonEvent:
    """A button changing state."""

    button: str
    pressed: bool


Event = EncoderEvent | ButtonEvent


def _wrapped_delta(new: int, old: int) -> int:
    """Difference between two int16 counters, tolerating wrap-around."""
    delta = (new - old) % _INT16_SPAN
    if delta >= _INT16_HALF:
        delta -= _INT16_SPAN
    return delta


class Tmate2NotFound(RuntimeError):
    pass


class Tmate2:
    """Polls the controller and turns raw reports into events."""

    def __init__(self, path: bytes | None = None) -> None:
        self._path = path
        self._dev: hid.device | None = None
        self._positions: dict[str, int] | None = None
        self._buttons: int | None = None
        self.info: dict = {}

    # -- lifecycle ----------------------------------------------------------

    def open(self) -> None:
        path = self._path
        if path is None:
            devices = hid.enumerate(VENDOR_ID, PRODUCT_ID)
            if not devices:
                raise Tmate2NotFound(
                    f"no HID device {VENDOR_ID:04x}:{PRODUCT_ID:04x} present"
                )
            self.info = devices[0]
            path = self.info["path"]
        dev = hid.device()
        dev.open_path(path)
        dev.set_nonblocking(1)
        self._dev = dev
        log.info(
            "opened %s %s (%s)",
            self.info.get("manufacturer_string", "?"),
            self.info.get("product_string", "?"),
            (self.info.get("serial_number") or "").strip(),
        )

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:  # noqa: BLE001 - hidapi raises bare exceptions
                pass
            self._dev = None

    def __enter__(self) -> "Tmate2":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- reading ------------------------------------------------------------

    def _decode(self, report: bytes) -> tuple[dict[str, int], int]:
        main, e1, e2, buttons = struct.unpack_from("<4h", report, 1)
        return {"MAIN": main, "E1": e1, "E2": e2}, buttons & 0x1FF

    def poll(self, timeout_ms: int = 20) -> list[Event]:
        """Read pending reports and return the events they imply.

        The first report after opening only establishes a baseline, so that a
        knob left in any position does not produce a jump on startup.
        """
        if self._dev is None:
            raise RuntimeError("device is not open")

        events: list[Event] = []
        while True:
            data = self._dev.read(REPORT_SIZE, timeout_ms=timeout_ms)
            if not data:
                break
            report = bytes(data)
            if len(report) < PAYLOAD_SIZE:
                continue

            positions, buttons = self._decode(report)

            if self._positions is None:
                # Baseline only - absolute counters carry over from whatever the
                # user last did, and replaying that would fling the VFO.
                self._positions = positions
                self._buttons = buttons
                timeout_ms = 0
                continue

            for name in ENCODERS:
                delta = _wrapped_delta(positions[name], self._positions[name])
                if delta:
                    events.append(EncoderEvent(name, delta, positions[name]))

            if self._buttons is not None and buttons != self._buttons:
                changed = buttons ^ self._buttons
                for bit, name in BUTTONS.items():
                    if changed & (1 << bit):
                        events.append(
                            ButtonEvent(name, pressed=not (buttons & (1 << bit)))
                        )

            self._positions = positions
            self._buttons = buttons
            timeout_ms = 0  # drain the rest of the queue without waiting

        return events
