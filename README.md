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
  layout, encoder semantics, button bitmap

## Requirements

* Windows, Python 3.10+
* SDRMAX V running, with the QS1R connected
* A Tmate 2 on the stock `HidUsb` driver (no vendor DLL required)

```bash
pip install -r requirements.txt
```

## Usage

```bash
python -m qs1r_tmate
```

Useful flags: `--dry-run` decodes the controller without touching the receiver,
`--freq` seeds the VFO, `--port` selects a different command channel,
`--reverse-tuning` flips the knob direction.

On startup the bridge sends only frequency and mode. Volume and filter are left
exactly as SDRMAX has them and are touched only when a knob asks for a change —
there is no way to read them back, so overwriting them would silently discard
your settings.

### Default bindings

| Control | Action |
|---|---|
| Main knob | tune the VFO by the current step |
| Push main knob | cycle step: 1, 10, 50, 100, 500, 1000, 5000, 10000 Hz |
| F1 … F6 | LSB, USB, CW, AM, SAM, DIG |
| E1 knob / push | volume / mute |
| E2 knob / push | filter width / reset filter |

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
  playing. The bridge never touches volume unless a knob asks it to.
* **Filter edges can be nonsense.** `?fl`/`?fh` have returned
  `-314169 .. -307519` — with the correct width. SDRMAX's own display shows the
  same, so it is not a protocol error. The bridge validates them and falls back
  to a per-mode default.
* **No S-meter.** The signal level is not exposed by any query.

## Roadmap

1. ~~**Vertical slice** — knob tunes, buttons switch mode.~~ done
2. ~~**Bidirectional sync** — SDRMAX and the controller track each other.~~ done
3. **Tmate 2 display** — decode the 64-byte output report and put frequency and
   mode on the controller's LCD. The S-meter will need a source other than the
   command protocol.
4. Packaging: tray application, autostart, configurable bindings.
