# Tmate 2 USB HID protocol

WoodBox Radio Tmate 2, later sold as the Elad TM-2. WoodBox Radio is defunct and
the SDK was never properly published, so this is what the device itself reports,
verified on HW1.1 / FW1.3.

## Identification

```
VID:PID          0x1721:0x0614
manufacturer     Elad
product          TMATE 2
serial_number    "HW1.1 FW1.3 "     (firmware revision, not a serial)
usage page       0xFF00 (vendor defined), usage 0x0001
driver           HidUsb (stock Windows) - no vendor DLL needed
```

## Report descriptor

47 bytes, as read from the device:

```
06 00 ff        Usage Page (Vendor Defined 0xFF00)
09 01           Usage (0x01)
a1 01           Collection (Application)
09 01             Usage (0x01)
15 00             Logical Minimum (0)
26 ff 00          Logical Maximum (255)
75 08             Report Size (8)
95 40             Report Count (64)
81 02             Input (Data, Var, Abs)      -> 64-byte input report
09 01             Usage (0x01)
15 00 26 ff 00 75 08 95 40
91 02             Output (Data, Var, Abs)     -> 64-byte output report
09 01             Usage (0x01)
15 00 26 ff 00 75 08 95 01
b1 02             Feature (Data, Var, Abs)    -> 1-byte feature report
c0              End Collection
```

**No Report ID is declared.** On input the bytes below are raw payload; on output
hidapi still requires a leading report-number byte, which is always zero.

## Input report

Only the first 9 bytes carry information; bytes 9..63 are uninitialised buffer
contents and change constantly, so they must be ignored.

| Offset | Size | Meaning |
|---|---|---|
| 0 | 1 | marker, normally `0x01` |
| 1..2 | int16 LE | main encoder position, absolute |
| 3..4 | int16 LE | **E2** encoder position, absolute |
| 5..6 | int16 LE | **E1** encoder position, absolute |
| 7..8 | 9 bits | button bitmap, **0 = pressed** |

The encoders report an **absolute signed position**, not a delta: one detent
changes the counter by ±1, and the value persists across reports and across
reopening the device. Motion is the difference between successive reports, taken
modulo 2^16 so that wrap-around is handled.

Because the position carries over, the first report after opening the device
must be used as a baseline only - replaying it as motion would fling the VFO.

The main encoder counts **up when turned anticlockwise**, opposite to the two
small encoders.

### Button bitmap

| Bit | Button |
|---|---|
| 0 | F1 |
| 1 | F2 |
| 2 | F3 |
| 3 | F4 |
| 4 | F5 |
| 5 | F6 |
| 6 | push on the main tuning knob |
| 7 | push on the **E2** encoder |
| 8 | push on the **E1** encoder |

All released reads `0x01FF`.

The two small encoders appear in **reverse order** in both the encoder words and
the button bits: the second word is E2 and the third is E1. The Tmate2_C readme
numbers the buttons the other way round, but its own struct has the reversed
order, and the panel agrees with the struct - assuming the obvious order swaps
the two knobs.

### Open question

While a button is held, byte 0 alternates between `0x01` and `0x00` at roughly
50 Hz with the rest of the payload unchanged. It is probably a key-repeat or
sequence flag. The bridge ignores byte 0.

## Output report

44 bytes are sent (hidapi zero-pads the rest of the 64). Byte 0 is the report
number and is always zero, so the payload occupies bytes 1..43.

The bit map below comes from the interface published by the
[Tmate2_C](https://github.com/microenh/Tmate2_C) project, whose field names come
in turn from the vendor `Tmate2LcdSegment.pdf`. It was re-derived into byte and
bit offsets here and confirmed against the real panel.

**Every write replaces the entire device state**, including the encoder speed
and acceleration settings. A report that leaves bytes 39..43 at zero will
reconfigure the encoders, so those must be included in every write.

### Main display

Nine seven-segment digits, position 1 rightmost. Each digit spans two bytes:

```
left  byte = 22 - 2 * position      low nibble, segments d e g f (bit 3 -> d)
right byte = 23 - 2 * position      low 3 bits, segments c b a   (bit 2 -> c)
                                    bit 3 of the right byte is that digit's underline
```

Note that the vendor names the left nibble `defg`, but the actual bit order is
**d, e, g, f** - confirmed against the digits 0, 2, 3, 5, 6 and 9.

Two decimal points: `dot2` (byte 16, bit 4) sits after digit 3 and `dot1`
(byte 10, bit 4) after digit 6, which groups a frequency in Hz as `14.223.500`.

### S-meter

Three digits, position 1 rightmost, in a different segment order again -
`f, g, e` and `a, b, c, d`:

```
right byte = 22 + 2 * position      bits 4..7, segments a b c d (bit 7 -> a)
left  byte = 23 + 2 * position      bits 5..7, segments f g e   (bit 7 -> f)
```

Fifteen bar segments, left to right: 1..3 are byte 2 bits 1..3; 4..7 are byte 32
bits 7..4; 8..11 are byte 31 bits 4..7; 12..15 are byte 30 bits 7..4.

### Annunciators

Mode indicators live in bytes 22 and 23. Every bit of both was swept one at a
time and read off the panel, so this table is observed rather than inherited:

| Bit | Byte 22 | Byte 23 |
|---|---|---|
| 0 | the dot of the CW marker | `DRM` |
| 1 | the upper minus | `DIG` |
| 2 | the plus | `STEREO` |
| 3 | the lower minus | nothing |
| 4 | `DSB` | `dBm` |
| 5 | `FM` | `CW` |
| 6 | `USB` | `LSB` |
| 7 | `SAM` | `AM` |

Units are `hz` (24.0), `k` (26.0), `mw_w` (28.0) and `mw_m` (29.0). The full
table is in [`display.py`](../src/qs1r_tmate/display.py).

### Panel

| Byte | Meaning |
|---|---|
| 33 | bit 0 `usb_led`, bit 1 `lock_led`, bit 2 `click` |
| 34, 35, 36 | backlight red, green, blue |
| 37 | contrast |
| 38 | refresh |
| 39..41 | main encoder step size at three speeds |
| 42, 43 | speed transition thresholds |

`click` is a **toggle**, not a level: the device beeps whenever the bit changes
value.

### Verified on hardware

* Each colour byte drives its own channel correctly on its own, but
  **255,255,255 shows as purple** - the green LED is far weaker than red and
  blue. White is approximately **32, 255, 32**.
* Bytes 37 and 38 produced no visible change in a single-byte sweep.
* The panel holds its contents while the HID handle is open and blanks when the
  process closes the device, so a bridge simply keeps it open and refreshes.

`tools/probe_bytes.py` drives one byte at a time while showing that byte's index
on the display, which is how the colour channels above were pinned down.
