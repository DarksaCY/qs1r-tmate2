# qs1r-tmate2

Direct control of **SDRMAX V** (the QS1R direct-sampling receiver software) from
a **WoodBox Radio / Elad Tmate 2** USB controller, on Windows.

No such integration exists: SDRMAX V shipped in 2013, its author Phil Covington
(N8VB) has since died, WoodBox Radio is defunct, and neither side ever published
a protocol. Both halves here were reverse engineered and verified against real
hardware.

## How it works

SDRMAX V turns out to contain a `QS1RServer` that speaks a line-based ASCII
protocol over TCP, and its own GUI is just a client of it. That makes the whole
receiver controllable without touching the application window:

```
Tmate 2  --USB HID-->  bridge  --TCP 127.0.0.1:43067-->  SDRMAX V  --USB-->  QS1R
```

The two protocols are documented in full:

* [docs/sdrmax-v-protocol.md](docs/sdrmax-v-protocol.md) — ports, framing, ~60
  commands, the mode table, and the control surfaces beyond TCP
* [docs/tmate2-hid.md](docs/tmate2-hid.md) — report descriptor, input report
  layout, encoder semantics, button bitmap, and the output report that drives
  every LCD segment

## Download

Ready-to-run Windows builds are on the
[releases page](https://github.com/DarksaCY/qs1r-tmate2/releases): take
`qs1r-tmate-tray.exe` for the tray application, or `qs1r-tmate.exe` for the
console. Nothing to install. They are unsigned, so SmartScreen warns on first
run - *More info*, then *Run anyway*, or build from source below.

## Install from source

Windows, Python 3.10 or later, SDRMAX V running with the QS1R connected, and a
Tmate 2 on the stock `HidUsb` driver (no vendor DLL needed).

```bash
py -m venv .venv && .venv/Scripts/pip install -e ".[tray]"
```

Leave out `[tray]` for a console-only install.

## Run

```bash
qs1r-tmate
```

`qs1r-tmate --tray` puts it in the system tray instead, where the menu shows
whether it is connected and can open or re-apply the configuration.
`qs1r-tmate --install-autostart` starts the tray version at login through the
per-user `Run` key, and `--remove-autostart` undoes it.

Either way the bridge waits for SDRMAX and for the controller rather than
exiting, so it can start before they do.

Other flags: `--show-config` prints the settings and where they live,
`--dry-run` decodes the controller without touching the receiver, `--no-display`
leaves the LCD alone, `--invert MAIN,E1,E2` flips a knob direction, and
`--backlight R,G,B` sets the panel colour. The last two edit the configuration
and exit.

## Building the executables

```bash
.venv/Scripts/pip install pyinstaller
.venv/Scripts/python -m PyInstaller --onefile --console --name qs1r-tmate     --paths src --collect-submodules qs1r_tmate --collect-binaries hid     packaging/console_entry.py
.venv/Scripts/python -m PyInstaller --onefile --windowed --name qs1r-tmate-tray     --paths src --collect-submodules qs1r_tmate --collect-binaries hid     --collect-all pystray --collect-all PIL packaging/tray_entry.py
```

## Configuration

`%APPDATA%\qs1r-tmate\config.toml` is written with comments on first run.

| Control | Default |
|---|---|
| Main knob | tune the VFO by the current step |
| Push main knob | cycle step: 1, 10, 50, 100, 500, 1000, 5000, 10000 Hz |
| F1 … F6 | AM, SAM, LSB, DSB, USB, CW |
| E1 knob / push | AGC threshold / mute |
| E2 knob / push | filter width / reset filter |

Buttons take `mode:USB` and friends, the toggles `mute`, `nb1`, `nb2`, `nr`,
`anf`, `squelch`, `binaural`, `record`, or `step`, `filter_reset` and `none`.
The small knobs take `agc`, `squelch`, `nb1`, `nb2`, `volume` or `filter`.

Turned clockwise the main knob reports negative deltas and the two small ones
positive, so each is signed to make clockwise mean "more": frequency up, AGC
louder, filter wider. Raising the AGC threshold makes the audio quieter, which
was settled by an A/B test at -120 and 0 dBm rather than assumed.

The controller LCD shows the frequency grouped as `14.223.500`, lights the mode
annunciator, underlines the digit the current tuning step moves, and drives the
S-meter bar and its dBm readout live. Turning a small knob briefly replaces the
frequency with what it is changing - `AGC -90`, `FIL 3000`, `STEP 100`.

The backlight does not behave like sRGB, because the green LED is much weaker
than the other two: `255,255,255` looks purple, white is near `32,255,32`, and
amber is `255,160,0`.

## Bidirectional sync

The receiver is the single source of truth. Nearly every SDRMAX setter turns out
to have a matching getter (`?fhz`, `?mode`, `?fl` …) — these names exist but
appear nowhere in the binary as literals, so they have to be probed for. The
bridge adopts the receiver's real state on startup and then watches it, so
tuning with the mouse in SDRMAX moves the controller's idea of the VFO and vice
versa. To avoid fighting its own commands it only reads back after a short quiet
period.

`tools/check_sync.py` verifies this against the real hardware: it starts the
bridge, injects a frequency change the way the GUI would, and checks that the
bridge adopts it.

## Known quirks

Three things SDRMAX does that any client has to survive — all documented in
[docs/sdrmax-v-protocol.md](docs/sdrmax-v-protocol.md):

* **Volume is effectively write-only.** `>vol` changes the audio but does not
  move the GUI slider, and `?vol` read `0` while the receiver was audibly
  playing. Since the bridge cannot read it back, it would have to guess a
  starting point, so the E1 knob carries the AGC threshold instead - which is
  readable, stays in step with the GUI, and does much the same job by ear.
* **Filter edges can be nonsense.** `?fl`/`?fh` have returned
  `-314169 .. -307519` — with the correct width. SDRMAX's own display shows the
  same, so it is not a protocol error. The bridge validates them and falls back
  to a per-mode default.
* **The frequency is two numbers.** `?fhz` is only the centre; clicking the
  spectrum moves a separate offset tune that appears nowhere else but the third
  field of `?status`. The bridge shows `fhz + offset`, which is what you hear,
  and lights `SHIFT` when the cursor is off centre.
* **The S-meter is well hidden.** No query named after `smeter`, `level` or
  `dbm` works; the one that does is `?SmeterValue`, which appears in the binary
  only as a settings key.

## Roadmap

1. ~~**Vertical slice** — knob tunes, buttons switch mode.~~ done
2. ~~**Bidirectional sync** — SDRMAX and the controller track each other.~~ done
3. ~~**Tmate 2 display** — frequency, mode and tuning step on the LCD.~~ done
4. ~~**S-meter on the panel** — live level on the bar and in dBm.~~ done
5. Packaging: tray application, autostart, configurable bindings.
