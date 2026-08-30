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

## Known limitation

SDRMAX V's TCP protocol can *set* the frequency but never reports it back, so
the bridge owns the VFO and persists it between runs. Tuning SDRMAX with the
mouse while the bridge is running desynchronises the two. Closing that gap needs
the CAT rig-script channel — see the roadmap.

## Roadmap

1. **Vertical slice** — knob tunes, buttons switch mode. *(current)*
2. **Bidirectional frequency** — virtual COM pair plus a custom `.rs` rig script,
   so SDRMAX and the controller stay in sync whichever one is touched.
3. **Tmate 2 display** — decode the 64-byte output report and put frequency,
   mode and S-meter on the controller's LCD.
4. Packaging: tray application, autostart, configurable bindings.
