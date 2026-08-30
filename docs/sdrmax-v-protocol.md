# SDRMAX V command protocol (reverse engineered)

SDRMAX V 5.0.1.1 (Software Radio Laboratory LLC / Phil Covington N8VB, 2013) is
a Qt 5 application that contains both the `QS1RServer` object and the GUI.  The
GUI is an ordinary TCP client of that server — so every control the GUI offers
is reachable over the network.  None of this is documented by the vendor; it was
recovered from the strings and symbols of `SDRMAXV.exe` and then verified
against live hardware (QS1R s/n 20110211).

## Ports

| Port  | Symbol                        | Notes |
|-------|-------------------------------|-------|
| 43065 | `newRx1TCPCmdConnection`      | RX1 commands. **Held by the SDRMAX GUI.** A second client connects successfully but is never answered. |
| 43067 | `newRx2TCPCmdConnection`      | RX2 commands. Unused by the GUI, fully functional, and it drives the receiver you can see. **Use this one.** |
| 43069 | `newRx1GuiTCPCmdConnection`   | RX1 GUI update channel, held by the GUI. |
| 43071 | `newRx2GuiTCPCmdConnection`   | RX2 GUI update channel. |

Two UDP sockets (43072, 43076) carry the `QsCatConnection.exe` OmniRig bridge.

## Framing

ASCII, one message per line, terminated by `\n`. `\r\n` is accepted; a message
without a terminator is ignored, so the newline is mandatory.

```
>command arg        ->  OK
?name               ->  name=value
```

Several lines may be written in a single packet; the server answers each in
order.

```
$ printf '>fhz 14200000\n>mode 3\n' | ...
OK
OK
```

## Queries

The complete set — there are only five.

| Query            | Reply                | Meaning |
|------------------|----------------------|---------|
| `?serverpid`     | `serverpid=8688`     | PID of the SDRMAX process |
| `?start`         | `start=1`            | 1 while the receiver is running |
| `?tonefrequency` | `tonefrequency=41950`| test tone generator |
| `?fltx`          | `fltx=10`            | TX filter low cut |
| `?fhtx`          | `fhtx=3000`          | TX filter high cut |

**There is no frequency read-back.** `>fhz` sets the VFO, but no query returns
it, and the server does not push it. A controller therefore has to own the
frequency itself, or read it through the CAT rig-script channel instead.

## Commands

Receiver

| Command | Argument | Notes |
|---|---|---|
| `>fhz N` | Hz | set RX frequency. Verified: moves the VFO and recentres the spectrum |
| `>UpdateRxFreq` | — | re-apply the current frequency |
| `>mode N` | 0..7 | see the mode table below |
| `>fl N` / `>fh N` | Hz | filter low / high cut; negative values allowed |
| `>filtertaps N` | count | filter length |
| `>vol N` | level | audio volume. **Does not move the GUI slider** — the GUI keeps its own value and will overwrite yours the next time it sends one |
| `>mute 0\|1` | | |
| `>squelchswitch 0\|1`, `>squelchthreshold N` | | |
| `>samplerate N` | Hz | |
| `>start`, `>stop` | | run / halt the receiver |

AGC

`>agcdecayspeed N`, `>agcthreshold N`, `>agcfixedgain N`, `>agcslope N`,
`>agchangtime N`, `>agchangtimeswitch 0|1`

Noise processing

`>anbswitch 0|1`, `>anbthreshold N` (noise blanker 1),
`>bnbswitch 0|1`, `>bnbthreshold N` (noise blanker 2),
`>NoiseReductionSwitch 0|1`, `>noisereductionrate N`,
`>AutoNotchSwitch 0|1`, `>autonotchrate N`,
`>BinauralSwitch 0|1`

QS1R front end

`>PgaSwitch 0|1`, `>RandSwitch 0|1`, `>DitherSwitch 0|1`,
`>EncodeClockCorrection N`

Display and correction

`>DisplayFreqOffset N`, `>OffsetGenFreq N`, `>PsCorrection N`,
`>PostPsCorrection N`, `>WBCorrection N`, `>SmeterCorrection N`, `>tf N`

Recording and playback

`>WavRecord 0|1`, `>WavContinuous 0|1`, `>WavPreBuffering 0|1`,
`WavPreBufferTime N`, `>StartWavInput`, `>Stop`, `>WavInfo a, b, c`,
`>WavInLoop 0|1`

CAT and window

`>CATOn p1, ... p10`, `>CATOff p1`, `>show`, `>hide`, `>exit`,
`>__gui_update_ip_adddress__`

TX (QS1E companion)

`>fltx N` / `>fhtx N`

## Mode table

`>mode` takes an index. It is **not** the left-to-right order of the mode
buttons in the GUI — USB and DSB are swapped relative to the button row. Values
0, 3 and 4 were confirmed on hardware; the rest follow the order of the mode
strings inside the binary.

| Value | Mode | Default filter BW |
|---|---|---|
| 0 | AM  | 4000 Hz |
| 1 | SAM | 4000 Hz |
| 2 | LSB | 3000 Hz |
| 3 | USB | 3000 Hz |
| 4 | DSB | 3000 Hz |
| 5 | CW  | 500 Hz |
| 6 | FMN | |
| 7 | DIG | |

## Server-to-client messages

The server pushes these to a connected GUI channel: `start=`, `serverpid=`,
`tonefrequency=`, `fltx=`, `fhtx=`, `filter=`, `mode=`, `mflh=`, `mute=`.
The protocol identifies itself as `version=4.0`.

## Other control surfaces in SDRMAX V

Worth knowing about, because they cover what the TCP channel does not:

* **Rig-script engine.** `RigScripts\*.rs` are QtScript (JavaScript) files with a
  `server` object exposing `rigMode()`, `setRigMode(mode)`,
  `rigRxFrequency(encoding, digits, mult, offset)` and
  `setRigRxFrequency(value, encoding)`. Combined with `>CATOn` and a virtual COM
  pair this gives **bidirectional** frequency and mode sync — the missing
  read-back.
* **Griffin PowerMate.** SDRMAX already contains native HID support for the
  PowerMate knob (`openGriffinDevice`, `knobRotation`, `onGriffinButtonDown`,
  `setGriffinLEDBrightness`, settings `PowerMateStepSize` / `PowerMateStepsVfo`).
* **Keyboard.** Arrows step the VFO, Shift+arrows shift the filter, PageUp /
  PageDown change volume, Ctrl+arrows change sample rate, digits start direct
  frequency entry.
* **Local console.** The server window takes text commands of its own:
  `set freq_hz N`, `get freq_hz`, `set step_size N`, `u` / `d` (±500 Hz),
  `start`, `stop`, `gui`, `hide gui`, `reinit hardware`, `dump info`, `help`.
  Note this console *does* have `get freq_hz` even though TCP does not.
