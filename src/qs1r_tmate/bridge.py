"""The bridge: Tmate 2 controller events -> SDRMAX V commands.

SDRMAX V's command protocol has no way to read the current VFO frequency back
(there is ``>fhz`` but no ``?fhz``), so the bridge owns the frequency and
persists it between runs.  Tuning SDRMAX with the mouse while the bridge is
running will therefore desynchronise the two; closing that gap needs the CAT
rig-script channel, which is the next milestone.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .sdrmax import MODE_NAMES, MODES, SdrMax
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

#: The main encoder counts up when turned anticlockwise, so tuning is inverted
#: relative to the counter. Flip this to +1 if you prefer the raw direction.
TUNE_SIGN = -1

FREQ_MIN = 10_000
FREQ_MAX = 62_500_000

VOLUME_MIN = 0
VOLUME_MAX = 100
VOLUME_STEP = 2

FILTER_MIN_WIDTH = 100
FILTER_MAX_WIDTH = 20_000
FILTER_STEP = 50


def _state_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "qs1r-tmate" / "state.json"


@dataclass
class State:
    """Everything the bridge believes about the receiver."""

    frequency: int = 14_223_500
    mode: str = "USB"
    volume: int = 20
    muted: bool = False
    step_index: int = 2
    filter_low: int = -5072
    filter_high: int = 5072
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
                 dry_run: bool = False, tune_sign: int = TUNE_SIGN) -> None:
        self.radio = radio
        self.controller = controller
        self.state = state or State.load()
        self.dry_run = dry_run
        self.tune_sign = tune_sign

    # -- actions ------------------------------------------------------------

    def _apply_frequency(self) -> None:
        log.info("VFO %10d Hz  (step %d Hz)", self.state.frequency, self.state.step)
        if not self.dry_run:
            self.radio.set_frequency(self.state.frequency)

    def _apply_mode(self) -> None:
        log.info("mode %s", self.state.mode)
        if not self.dry_run:
            self.radio.set_mode(self.state.mode)

    def _apply_volume(self) -> None:
        log.info("volume %d", self.state.volume)
        if not self.dry_run:
            self.radio.set_volume(self.state.volume)

    def _apply_filter(self) -> None:
        log.info("filter %d .. %d Hz", self.state.filter_low, self.state.filter_high)
        if not self.dry_run:
            self.radio.set_filter(self.state.filter_low, self.state.filter_high)

    def _apply_mute(self) -> None:
        log.info("mute %s", "on" if self.state.muted else "off")
        if not self.dry_run:
            self.radio.set_mute(self.state.muted)

    def push_all(self) -> None:
        """Establish the state the bridge owns.

        Only frequency and mode are pushed. Volume and filter stay as the user
        left them in SDRMAX and are sent only once a knob actually asks for a
        change - the bridge has no way to read them back, so overwriting them on
        startup would silently discard the operator's settings.
        """
        self._apply_mode()
        self._apply_frequency()

    # -- event handling -----------------------------------------------------

    def _tune(self, detents: int) -> None:
        freq = self.state.frequency + self.tune_sign * detents * self.state.step
        self.state.frequency = max(FREQ_MIN, min(FREQ_MAX, freq))
        self._apply_frequency()

    def _adjust_volume(self, detents: int) -> None:
        volume = self.state.volume + detents * VOLUME_STEP
        self.state.volume = max(VOLUME_MIN, min(VOLUME_MAX, volume))
        self._apply_volume()

    def _adjust_filter(self, detents: int) -> None:
        widen = detents * FILTER_STEP
        low, high = self.state.filter_low, self.state.filter_high
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

    def handle(self, events) -> bool:
        """Apply a batch of controller events.  Returns True if anything changed."""
        tune_detents = 0
        volume_detents = 0
        filter_detents = 0
        changed = False

        for event in events:
            if isinstance(event, EncoderEvent):
                if event.encoder == "MAIN":
                    tune_detents += event.delta
                elif event.encoder == "E1":
                    volume_detents += event.delta
                elif event.encoder == "E2":
                    filter_detents += event.delta
            elif isinstance(event, ButtonEvent) and event.pressed:
                changed |= self._handle_button(event.button)

        # Coalesce encoder motion: one command per poll, not one per detent.
        if tune_detents:
            self._tune(tune_detents)
            changed = True
        if volume_detents:
            self._adjust_volume(volume_detents)
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
            self.state.filter_low, self.state.filter_high = -5072, 5072
            self._apply_filter()
            return True
        return False

    # -- main loop ----------------------------------------------------------

    def run(self, poll_ms: int = 10, save_interval: float = 5.0,
            duration: float = 0.0) -> None:
        log.info(
            "bridge running - main knob tunes, F1-F6 set mode, "
            "push main knob cycles step, E1 volume, E2 filter"
        )
        self.push_all()
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
    lines.append("  E1 knob          volume     |  push E1  mute on/off")
    lines.append("  E2 knob          filter width  |  push E2  reset filter")
    return "\n".join(lines)
