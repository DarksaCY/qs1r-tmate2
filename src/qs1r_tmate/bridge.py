"""The bridge: Tmate 2 controller events <-> SDRMAX V.

The receiver is the single source of truth.  Almost every SDRMAX setter has a
matching, undocumented getter (``?fhz``, ``?mode``, ``?fl`` ...), so the bridge
adopts the receiver's real state on startup and keeps watching it: tuning
SDRMAX with the mouse moves the bridge's idea of the VFO too, and turning the
knob moves the receiver.  Neither side owns the frequency.

To avoid fighting its own commands, the bridge only reads back after a short
quiet period with no outgoing command.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .display import Display
from .sdrmax import SdrMax, SdrMaxError
from .tmate2 import ButtonEvent, EncoderEvent, Tmate2

log = logging.getLogger(__name__)

#: Tuning steps in Hz, cycled with a push on the main knob.
STEP_SIZES = (1, 10, 50, 100, 500, 1000, 5000, 10000)

#: Mode assigned to each function button.
BUTTON_MODES = {
    "F1": "LSB",
    "F2": "USB",
    "F3": "CW",
    "F4": "AM",
    "F5": "SAM",
    "F6": "DIG",
}

#: Direction of each encoder, measured on the panel: turned clockwise the main
#: knob reports negative deltas and the two small ones positive.  The signs here
#: make clockwise mean "more" on all three: frequency up, AGC louder, filter
#: wider.
#:
#: E1 is inverted because raising the AGC threshold makes the audio *quieter* -
#: established by ear with an A/B test at -120 and 0 dBm, not by reasoning about
#: what a threshold ought to do.
ENCODER_SIGNS = {"MAIN": -1, "E1": -1, "E2": 1}

#: Kept for the --reverse-tuning flag.
TUNE_SIGN = ENCODER_SIGNS["MAIN"]

#: Backlight colour, remembered per user in the state file.  The green LED is
#: far weaker than red and blue, so values do not behave like sRGB: 255,255,255
#: reads as purple and white is nearer 32,255,32.  Calibrated against the panel.
BACKLIGHT = (255, 160, 0)

#: Rewrite the panel at least this often, in seconds.  The LCD holds its content
#: while the HID handle is open, but a steady refresh keeps it certain.
DISPLAY_REFRESH = 1.0

#: How often to read the signal level, in seconds.  Unlike the other read-backs
#: this one is never suppressed after a command: the meter should stay live
#: while the knob is moving.
SMETER_INTERVAL = 0.2
#: Redraw the meter only once it has moved by this much, to keep USB writes down.
SMETER_HYSTERESIS_DB = 1.0

#: How often to look for changes made in SDRMAX itself, in seconds.
SYNC_INTERVAL = 0.25
#: Ignore read-back for this long after sending a command, so that a fast spin
#: of the knob is not "corrected" by a value that is already stale.
SYNC_QUIET_PERIOD = 0.4

FREQ_MIN = 10_000
FREQ_MAX = 62_500_000

#: AGC threshold, in dBm.  Readable and writable, so it stays in step with the
#: SDRMAX GUI - unlike volume, which this protocol can set but never report.
AGC_MIN = -140
AGC_MAX = 0
AGC_STEP = 1

#: How long a transient label stays on the main display, in seconds.
OVERLAY_SECONDS = 1.5

FILTER_MIN_WIDTH = 100
FILTER_MAX_WIDTH = 20_000
FILTER_STEP = 50
#: SDRMAX sometimes holds filter edges far outside the audio range (values like
#: -314169..-307519 have been observed on its own display, with a correct width).
#: Anything beyond this is treated as unusable rather than adopted.
FILTER_SANE_LIMIT = 20_000

#: Passband to fall back on per mode when SDRMAX's own filter is unusable.
DEFAULT_FILTERS = {
    "AM": (-3325, 3325),
    "SAM": (-3325, 3325),
    "DSB": (-3000, 3000),
    "USB": (100, 2900),
    "LSB": (-2900, -100),
    "CW": (-250, 250),
    "FMN": (-6000, 6000),
    "DIG": (100, 2900),
}


def filter_is_usable(low: int, high: int) -> bool:
    return -FILTER_SANE_LIMIT <= low < high <= FILTER_SANE_LIMIT


def _state_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "qs1r-tmate" / "state.json"


@dataclass
class State:
    """Everything the bridge believes about the receiver."""

    frequency: int = 14_223_500
    mode: str = "USB"
    agc_threshold: int = -90
    muted: bool = False
    encoder_signs: dict = field(default_factory=lambda: dict(ENCODER_SIGNS))
    step_index: int = 2
    filter_low: int = -5072
    filter_high: int = 5072
    backlight: list = field(default_factory=lambda: list(BACKLIGHT))
    _path: Path = field(default_factory=_state_path, repr=False, compare=False)

    @property
    def step(self) -> int:
        return STEP_SIZES[self.step_index % len(STEP_SIZES)]

    @classmethod
    def load(cls) -> "State":
        path = _state_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        fields = {f for f in cls.__dataclass_fields__ if not f.startswith("_")}
        return cls(**{k: v for k, v in raw.items() if k in fields})

    def save(self) -> None:
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("could not save state: %s", exc)


class Bridge:
    def __init__(self, radio: SdrMax, controller: Tmate2, state: State | None = None,
                 dry_run: bool = False, tune_sign: int | None = None,
                 display: Display | None = None) -> None:
        self.radio = radio
        self.controller = controller
        self.state = state or State.load()
        self.dry_run = dry_run
        if tune_sign is not None:
            self.state.encoder_signs["MAIN"] = tune_sign
        self._last_command_at = 0.0
        self._last_sync_at = 0.0
        self._last_display_at = 0.0
        self._last_smeter_at = 0.0
        self._overlay: str | None = None
        self._overlay_until = 0.0
        self._smeter_dbm: float | None = None
        self.display = display
        if self.display is not None:
            self.display.set_rgb(*self.state.backlight)
            # Every report replaces the whole device state, so the encoder
            # profile has to ride along on all of them.  All three speeds are 1:
            # no hardware acceleration, one count per detent.
            self.display.set_encoder_profile(1, 1, 1, 32, 64)

    # -- actions ------------------------------------------------------------

    def _touch(self) -> None:
        self._last_command_at = time.monotonic()

    def _apply_frequency(self) -> None:
        log.info("VFO %10d Hz  (step %d Hz)", self.state.frequency, self.state.step)
        self._touch()
        if not self.dry_run:
            self.radio.set_frequency(self.state.frequency)

    def _apply_mode(self) -> None:
        log.info("mode %s", self.state.mode)
        self._touch()
        if not self.dry_run:
            self.radio.set_mode(self.state.mode)

    def _apply_agc(self) -> None:
        log.info("AGC threshold %d dBm", self.state.agc_threshold)
        self._touch()
        self._show("AGC %d" % self.state.agc_threshold)
        if not self.dry_run:
            self.radio.set_agc_threshold(self.state.agc_threshold)

    def _show(self, text: str) -> None:
        """Put a transient label on the main display."""
        self._overlay = text
        self._overlay_until = time.monotonic() + OVERLAY_SECONDS

    def _apply_filter(self) -> None:
        log.info("filter %d .. %d Hz", self.state.filter_low, self.state.filter_high)
        self._show("FIL %d" % (self.state.filter_high - self.state.filter_low))
        self._touch()
        if not self.dry_run:
            self.radio.set_filter(self.state.filter_low, self.state.filter_high)

    def _apply_mute(self) -> None:
        log.info("mute %s", "on" if self.state.muted else "off")
        self._touch()
        if not self.dry_run:
            self.radio.set_mute(self.state.muted)

    def adopt_from_radio(self) -> None:
        """Take the receiver's current settings as the starting state."""
        if self.dry_run:
            return
        snapshot = self.radio.snapshot()
        self.state.frequency = snapshot["frequency"]
        self.state.mode = snapshot["mode"]
        self.state.agc_threshold = snapshot["agc_threshold"]
        low, high = snapshot["filter_low"], snapshot["filter_high"]
        if filter_is_usable(low, high):
            self.state.filter_low, self.state.filter_high = low, high
        else:
            log.warning("SDRMAX reports an unusable filter (%d..%d Hz); the "
                        "%s default will be used if the filter knob is turned",
                        low, high, self.state.mode)
            self.state.filter_low, self.state.filter_high = self._default_filter()
        log.info("adopted from SDRMAX: %d Hz, %s, filter %d..%d Hz, AGC %d dBm",
                 self.state.frequency, self.state.mode,
                 self.state.filter_low, self.state.filter_high,
                 self.state.agc_threshold)

    def _default_filter(self) -> tuple[int, int]:
        return DEFAULT_FILTERS.get(self.state.mode, (-3000, 3000))

    def sync_from_radio(self) -> bool:
        """Adopt changes made in SDRMAX itself.  Returns True if anything moved."""
        if self.dry_run:
            return False
        changed = False
        try:
            frequency = self.radio.get_frequency()
            mode = self.radio.get_mode()
            agc = self.radio.get_agc_threshold()
        except (OSError, SdrMaxError) as exc:
            log.debug("read-back failed: %s", exc)
            return False

        if frequency != self.state.frequency:
            log.info("SDRMAX moved the VFO to %d Hz", frequency)
            self.state.frequency = frequency
            changed = True
        if mode != self.state.mode:
            log.info("SDRMAX changed the mode to %s", mode)
            self.state.mode = mode
            changed = True
        if agc != self.state.agc_threshold:
            log.info("SDRMAX changed the AGC threshold to %d dBm", agc)
            self.state.agc_threshold = agc
            changed = True
        return changed

    # -- panel --------------------------------------------------------------

    def refresh_display(self) -> None:
        """Redraw frequency, mode and tuning step on the controller."""
        if self.display is None:
            return
        panel = self.display.clear()
        if self._overlay is not None and time.monotonic() < self._overlay_until:
            panel.set_main_text(self._overlay)
        else:
            self._overlay = None
            panel.set_frequency(self.state.frequency)
        panel.set_mode(self.state.mode)
        panel.set_flag("vfo")
        panel.set_flag("rx")
        # Underline the digit the tuning step moves: 1 Hz marks the units digit,
        # 10 and 50 the tens, and so on.
        panel.set_underline(len(str(self.state.step)))
        if self.state.muted:
            panel.set_flag("vol")
        if self._smeter_dbm is not None:
            panel.set_smeter_scale()
            panel.set_smeter(self._smeter_dbm)
        try:
            self.controller.write(panel.render())
        except OSError as exc:
            log.debug("display write failed: %s", exc)
        self._last_display_at = time.monotonic()

    def read_smeter(self) -> bool:
        """Read the signal level.  Returns True if the panel should be redrawn."""
        if self.dry_run or self.display is None:
            return False
        try:
            dbm = self.radio.get_smeter()
        except (OSError, SdrMaxError) as exc:
            log.debug("S-meter read failed: %s", exc)
            return False
        moved = (self._smeter_dbm is None
                 or abs(dbm - self._smeter_dbm) >= SMETER_HYSTERESIS_DB)
        self._smeter_dbm = dbm
        return moved

    # -- event handling -----------------------------------------------------

    def _tune(self, detents: int) -> None:
        freq = self.state.frequency + detents * self.state.step
        self.state.frequency = max(FREQ_MIN, min(FREQ_MAX, freq))
        self._apply_frequency()

    def _adjust_agc(self, detents: int) -> None:
        value = self.state.agc_threshold + detents * AGC_STEP
        self.state.agc_threshold = max(AGC_MIN, min(AGC_MAX, value))
        self._apply_agc()

    def _adjust_filter(self, detents: int) -> None:
        widen = detents * FILTER_STEP
        low, high = self.state.filter_low, self.state.filter_high
        if not filter_is_usable(low, high):
            low, high = self._default_filter()
        if low < 0:  # symmetric passband - open both edges together
            low -= widen
            high += widen
            width = high - low
        else:  # single sideband - move the outer edge only
            high += widen
            width = high - low
        if not FILTER_MIN_WIDTH <= width <= FILTER_MAX_WIDTH:
            return
        self.state.filter_low, self.state.filter_high = low, high
        self._apply_filter()

    def _cycle_step(self) -> None:
        self.state.step_index = (self.state.step_index + 1) % len(STEP_SIZES)
        log.info("tuning step %d Hz", self.state.step)
        self._show("STEP %d" % self.state.step)

    def handle(self, events) -> bool:
        """Apply a batch of controller events.  Returns True if anything changed."""
        tune_detents = 0
        agc_detents = 0
        filter_detents = 0
        changed = False

        for event in events:
            if isinstance(event, EncoderEvent):
                detents = event.delta * self.state.encoder_signs.get(event.encoder, 1)
                if event.encoder == "MAIN":
                    tune_detents += detents
                elif event.encoder == "E1":
                    agc_detents += detents
                elif event.encoder == "E2":
                    filter_detents += detents
            elif isinstance(event, ButtonEvent) and event.pressed:
                changed |= self._handle_button(event.button)

        # Coalesce encoder motion: one command per poll, not one per detent.
        if tune_detents:
            self._tune(tune_detents)
            changed = True
        if agc_detents:
            self._adjust_agc(agc_detents)
            changed = True
        if filter_detents:
            self._adjust_filter(filter_detents)
            changed = True

        return changed

    def _handle_button(self, button: str) -> bool:
        if button in BUTTON_MODES:
            self.state.mode = BUTTON_MODES[button]
            self._apply_mode()
            return True
        if button == "MAIN":
            self._cycle_step()
            return True
        if button == "E1":
            self.state.muted = not self.state.muted
            self._apply_mute()
            return True
        if button == "E2":
            self.state.filter_low, self.state.filter_high = self._default_filter()
            self._apply_filter()
            return True
        return False

    # -- main loop ----------------------------------------------------------

    def run(self, poll_ms: int = 10, save_interval: float = 5.0,
            duration: float = 0.0) -> None:
        log.info(
            "bridge running - main knob tunes, F1-F6 set mode, "
            "push main knob cycles step, E1 AGC threshold, E2 filter"
        )
        self.adopt_from_radio()
        self.refresh_display()
        started = last_save = time.monotonic()
        dirty = False
        try:
            while True:
                if duration and time.monotonic() - started >= duration:
                    log.info("duration reached, stopping")
                    break
                events = self.controller.poll(timeout_ms=poll_ms)
                if events:
                    dirty |= self.handle(events)
                now = time.monotonic()
                if now - self._last_smeter_at >= SMETER_INTERVAL:
                    self._last_smeter_at = now
                    if self.read_smeter():
                        self.refresh_display()
                if (now - self._last_sync_at >= SYNC_INTERVAL
                        and now - self._last_command_at >= SYNC_QUIET_PERIOD):
                    self._last_sync_at = now
                    dirty |= self.sync_from_radio()
                if dirty or now - self._last_display_at >= DISPLAY_REFRESH:
                    self.refresh_display()
                if dirty and now - last_save >= save_interval:
                    self.state.save()
                    last_save = now
                    dirty = False
        except KeyboardInterrupt:
            log.info("stopping")
        finally:
            self.state.save()


def describe_bindings() -> str:
    lines = ["Tmate 2 bindings:", "  main knob        tune VFO by the current step"]
    lines.append("  push main knob   cycle step " +
                 ", ".join(f"{s}" for s in STEP_SIZES) + " Hz")
    for button, mode in BUTTON_MODES.items():
        lines.append(f"  {button}               mode {mode}")
    lines.append("  E1 knob          AGC threshold  |  push E1  mute on/off")
    lines.append("  E2 knob          filter width  |  push E2  reset filter")
    return "\n".join(lines)
