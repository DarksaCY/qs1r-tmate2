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

**No Report ID is declared**, so the bytes below are raw payload — the leading
`0x01` is data, not a HID report ID.

## Input report

Only the first 9 bytes carry information; bytes 9..63 are uninitialised buffer
contents and change constantly, so they must be ignored.

| Offset | Size | Meaning |
|---|---|---|
| 0 | 1 | marker, normally `0x01` |
| 1..2 | int16 LE | main encoder position, absolute |
| 3..4 | int16 LE | E1 encoder position, absolute |
| 5..6 | int16 LE | E2 encoder position, absolute |
| 7..8 | 9 bits | button bitmap, **0 = pressed** |

The encoders report an **absolute signed position**, not a delta: one detent
changes the counter by ±1, and the value persists across reports and across
reopening the device. Motion is the difference between successive reports, taken
modulo 2^16 so that wrap-around is handled.

Because the position carries over, the first report after opening the device
must be used as a baseline only — replaying it as motion would fling the VFO.

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
| 7 | push on the E1 encoder |
| 8 | push on the E2 encoder |

All released reads `0x01FF`.

### Open question

While a button is held, byte 0 alternates between `0x01` and `0x00` at roughly
50 Hz with the rest of the payload unchanged. It is probably a key-repeat or
sequence flag. The bridge currently ignores byte 0.

## Output report

64 bytes, not yet decoded. Per the surviving community notes it drives 170+
individual LCD segments (one bit each), the green and red LEDs, the RGB
backlight, contrast, and the encoder step / acceleration settings. Mapping this
is the prerequisite for showing frequency and mode on the controller's own
display.
