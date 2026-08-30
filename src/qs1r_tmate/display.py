"""Tmate 2 LCD: the 64-byte HID output report.

The display is driven by one output report in which almost every bit is a single
LCD segment.  The bit map was derived from the hardware interface published by
the Tmate2_C project (https://github.com/microenh/Tmate2_C), whose field names
come in turn from the vendor Tmate2LcdSegment.pdf.  The segment tables here are
generated from the standard seven-segment shapes rather than transcribed, and
were cross-checked digit by digit against that map.

Layout, with byte 0 being the HID report-number prefix (always zero, since the
device declares no report IDs):

* bytes 4..21   nine main digits, position 1 rightmost
* bytes 24..29  three S-meter digits
* bytes 30..32  twelve of the S-meter bar segments; the first three are in byte 2
* bytes 22..23  mode annunciators
* byte 33       the two LEDs and the click toggle
* bytes 34..37  backlight red, green, blue, and contrast
* bytes 39..43  main-encoder step sizes and acceleration thresholds

A main digit is split across two bytes: a low nibble holding segments d, e, g, f
and, in the next byte, three bits holding c, b, a.  The S-meter digits use a
different order again: f, g, e and a, b, c, d.
"""

from __future__ import annotations

#: Bytes actually sent.  hidapi zero-pads the rest of the 64-byte report.
REPORT_SIZE = 44

#: Standard seven-segment shapes as sets of segment letters.
_SHAPES = {
    " ": "", "-": "g", "_": "d", "=": "dg",
    "0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg",
    "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "A": "abcefg", "B": "cdefg", "C": "adef", "D": "bcdeg", "E": "adefg",
    "F": "aefg", "G": "acdefg", "H": "bcefg", "I": "bc", "J": "bcde",
    "L": "def", "N": "abcef", "O": "abcdef", "P": "abefg", "R": "efg",
    "S": "acdfg", "T": "defg", "U": "bcdef", "Y": "bcdfg",
}


def _pack(shape: str, order: str) -> int:
    """Pack segment letters into bits, the first letter of ``order`` most significant."""
    value = 0
    for bit, segment in enumerate(reversed(order)):
        if segment in shape:
            value |= 1 << bit
    return value


#: character -> (low nibble "degf", high triad "cba")
MAIN_SEGMENTS = {
    ch: (_pack(shape, "degf"), _pack(shape, "cba")) for ch, shape in _SHAPES.items()
}
#: character -> (triad "fge", nibble "abcd")
SMETER_SEGMENTS = {
    ch: (_pack(shape, "fge"), _pack(shape, "abcd")) for ch, shape in _SHAPES.items()
}

#: Single-bit annunciators: name -> (byte, bit).
FLAGS = {
    "lp": (1, 1), "rx": (1, 2), "tx": (1, 3), "s": (1, 4), "vfo": (1, 5),
    "nr": (1, 6), "e1": (1, 7),
    "att": (2, 0), "smeter_s1": (2, 4), "a": (2, 5), "nb": (2, 6), "vol": (2, 7),
    "smeter_line": (3, 0), "smeter_s9": (3, 1), "smeter_s7": (3, 2),
    "smeter_s5": (3, 3), "smeter_s3": (3, 4), "b": (3, 5), "an": (3, 6),
    "rfg": (3, 7),
    "sql": (4, 4), "drv": (5, 4), "nr2": (6, 4), "nb2": (7, 4), "an2": (8, 4),
    "e2": (9, 4), "smeter_10": (9, 5),
    "dot1": (10, 4), "smeter_plus20": (10, 5),
    "high": (11, 4), "smeter_20": (11, 5),
    "low": (12, 4), "shift": (13, 4), "rit": (14, 4), "xit": (15, 4),
    "dot2": (16, 4), "smeter_plus40": (16, 5), "smeter_40": (17, 5),
    "smeter_plus60": (19, 5), "err": (20, 4), "smeter_60": (20, 5),
    "w_fm": (21, 4), "w": (21, 5),
    "cw_plus": (22, 0), "cw_minus": (22, 1), "dig_plus": (22, 2),
    "dig_minus": (22, 3), "dsb": (22, 4), "fm": (22, 5), "usb": (22, 6),
    "sam": (22, 7),
    "drm": (23, 0), "dig": (23, 1), "stereo": (23, 2), "dbm": (23, 4),
    "cw": (23, 5), "lsb": (23, 6), "am": (23, 7),
    "hz": (24, 0), "k": (26, 0), "mw_w": (28, 0),
    "mw_m": (29, 0), "smeter_db_minus": (29, 4),
    "pre_1": (31, 0), "pre_2": (31, 1),
    "att_2": (32, 0), "pre": (32, 1), "att_1": (32, 2),
    "usb_led": (33, 0), "lock_led": (33, 1), "click": (33, 2),
}

#: S-meter bar segment -> (byte, bit), left to right.
BAR_BITS = {
    1: (2, 1), 2: (2, 2), 3: (2, 3),
    4: (32, 7), 5: (32, 6), 6: (32, 5), 7: (32, 4),
    8: (31, 4), 9: (31, 5), 10: (31, 6), 11: (31, 7),
    12: (30, 7), 13: (30, 6), 14: (30, 5), 15: (30, 4),
}
BAR_COUNT = len(BAR_BITS)

MAIN_DIGITS = 9
SMETER_DIGITS = 3

#: S9 is -73 dBm on HF, and one S-unit is 6 dB.  The bar appears to be laid out
#: as one segment per S-unit up to S9, then 10 dB per segment for the +10..+60
#: part, which is exactly 15 segments.
S9_DBM = -73.0
DB_PER_S_UNIT = 6.0
DB_PER_BAR_OVER_S9 = 10.0
BARS_AT_S9 = 9

#: Scale legend around the bar: the S1..S9 ticks and the +10..+60 ticks.
SCALE_FLAGS = (
    "smeter_line", "smeter_s1", "smeter_s3", "smeter_s5", "smeter_s7",
    "smeter_s9", "smeter_10", "smeter_20", "smeter_40", "smeter_60",
    "s", "smeter_plus20", "smeter_plus40", "smeter_plus60",
)


def dbm_to_bars(dbm: float) -> int:
    """Map a level in dBm onto the fifteen bar segments."""
    if dbm <= S9_DBM:
        bars = round((dbm - S9_DBM) / DB_PER_S_UNIT) + BARS_AT_S9
    else:
        bars = BARS_AT_S9 + round((dbm - S9_DBM) / DB_PER_BAR_OVER_S9)
    return max(0, min(BAR_COUNT, int(bars)))

#: SDRMAX mode -> annunciator on the LCD.
MODE_FLAGS = {
    "AM": "am", "SAM": "sam", "LSB": "lsb", "USB": "usb",
    "DSB": "dsb", "CW": "cw", "FMN": "fm", "DIG": "dig",
}

_BYTE_RGB_RED, _BYTE_RGB_GREEN, _BYTE_RGB_BLUE = 34, 35, 36
_BYTE_CONTRAST = 37
_BYTE_ENCODER = 39  # speed1, speed2, speed3, trans12, trans23


class Display:
    """Builds the output report.  Nothing reaches the device until :meth:`render`."""

    def __init__(self) -> None:
        self._buf = bytearray(REPORT_SIZE)
        self._click = False

    # -- primitives ---------------------------------------------------------

    def clear(self) -> "Display":
        """Blank the panel but keep backlight, contrast and encoder settings."""
        keep = bytes(self._buf[_BYTE_RGB_RED:])
        self._buf = bytearray(REPORT_SIZE)
        self._buf[_BYTE_RGB_RED:] = keep
        return self

    def set_flag(self, name: str, on: bool = True) -> "Display":
        byte, bit = FLAGS[name]
        if on:
            self._buf[byte] |= 1 << bit
        else:
            self._buf[byte] &= ~(1 << bit) & 0xFF
        return self

    def _set_field(self, byte: int, shift: int, width: int, value: int) -> None:
        mask = ((1 << width) - 1) << shift
        self._buf[byte] = (self._buf[byte] & ~mask & 0xFF) | ((value << shift) & mask)

    # -- main display -------------------------------------------------------

    def set_main_digit(self, position: int, char: str) -> "Display":
        """``position`` 1 is the rightmost digit, 9 the leftmost."""
        if not 1 <= position <= MAIN_DIGITS:
            raise ValueError(f"main digit {position} out of range")
        left, right = MAIN_SEGMENTS.get(char.upper(), MAIN_SEGMENTS[" "])
        self._set_field(22 - 2 * position, 0, 4, left)
        self._set_field(23 - 2 * position, 0, 3, right)
        return self

    def set_underline(self, position: int, on: bool = True) -> "Display":
        byte, bit = 23 - 2 * position, 3
        if on:
            self._buf[byte] |= 1 << bit
        else:
            self._buf[byte] &= ~(1 << bit) & 0xFF
        return self

    def set_main_text(self, text: str) -> "Display":
        """Right-align up to nine characters on the main display."""
        text = text[-MAIN_DIGITS:].rjust(MAIN_DIGITS)
        for position in range(1, MAIN_DIGITS + 1):
            self.set_main_digit(position, text[MAIN_DIGITS - position])
        return self

    def set_frequency(self, hz: int) -> "Display":
        """Show a frequency in Hz, grouped as 14.223.500 using the two dots."""
        digits = f"{max(0, int(hz)):d}"[-MAIN_DIGITS:]
        self.set_main_text(digits)
        # dot2 sits after digit 3, dot1 after digit 6
        self.set_flag("dot2", len(digits) > 3)
        self.set_flag("dot1", len(digits) > 6)
        self.set_flag("hz", True)
        return self

    # -- S-meter ------------------------------------------------------------

    def set_smeter_digit(self, position: int, char: str) -> "Display":
        """``position`` 1 is the rightmost of the three S-meter digits."""
        if not 1 <= position <= SMETER_DIGITS:
            raise ValueError(f"S-meter digit {position} out of range")
        left, right = SMETER_SEGMENTS.get(char.upper(), SMETER_SEGMENTS[" "])
        self._set_field(22 + 2 * position, 4, 4, right)
        self._set_field(23 + 2 * position, 5, 3, left)
        return self

    def set_smeter_text(self, text: str) -> "Display":
        text = text[-SMETER_DIGITS:].rjust(SMETER_DIGITS)
        for position in range(1, SMETER_DIGITS + 1):
            self.set_smeter_digit(position, text[SMETER_DIGITS - position])
        return self

    def set_smeter_scale(self, on: bool = True) -> "Display":
        """Light the tick legend printed around the bar."""
        for flag in SCALE_FLAGS:
            self.set_flag(flag, on)
        return self

    def set_smeter(self, dbm: float) -> "Display":
        """Show a level in dBm on the bar and the three S-meter digits."""
        self.set_bars(dbm_to_bars(dbm))
        value = int(round(dbm))
        self.set_smeter_text(str(abs(value)))
        self.set_flag("smeter_db_minus", value < 0)
        self.set_flag("dbm", True)
        return self

    def set_bars(self, count: int) -> "Display":
        """Light the first ``count`` of the fifteen bar segments."""
        for index, (byte, bit) in BAR_BITS.items():
            if index <= count:
                self._buf[byte] |= 1 << bit
            else:
                self._buf[byte] &= ~(1 << bit) & 0xFF
        return self

    # -- mode ---------------------------------------------------------------

    def set_mode(self, mode: str) -> "Display":
        for flag in MODE_FLAGS.values():
            self.set_flag(flag, False)
        flag = MODE_FLAGS.get(mode.upper())
        if flag:
            self.set_flag(flag, True)
        return self

    # -- panel --------------------------------------------------------------

    def set_rgb(self, red: int, green: int, blue: int) -> "Display":
        self._buf[_BYTE_RGB_RED] = red & 0xFF
        self._buf[_BYTE_RGB_GREEN] = green & 0xFF
        self._buf[_BYTE_RGB_BLUE] = blue & 0xFF
        return self

    def set_contrast(self, value: int) -> "Display":
        self._buf[_BYTE_CONTRAST] = max(0, min(255, value))
        return self

    def set_encoder_profile(self, speed1: int = 1, speed2: int = 2, speed3: int = 4,
                            trans12: int = 20, trans23: int = 40) -> "Display":
        """Step size at three speeds, and the rates at which the device shifts."""
        for offset, value in enumerate((speed1, speed2, speed3, trans12, trans23)):
            self._buf[_BYTE_ENCODER + offset] = max(0, min(255, value))
        return self

    def click(self) -> "Display":
        """Toggle the click bit; the device beeps whenever this bit changes."""
        self._click = not self._click
        return self.set_flag("click", self._click)

    def render(self) -> bytes:
        return bytes(self._buf)
