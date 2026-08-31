"""The bridge: Tmate 2 controller events <-> SDRMAX V.

The receiver is the single source of truth.  Almost every SDRMAX setter has a
matching, undocumented getter, so the bridge adopts the receiver's real state on
startup and keeps watching it: tuning SDRMAX with the mouse moves the
controller's idea of the frequency and turning the knob moves the receiver.

What each control does comes from the configuration file, not from this module -
see :mod:`qs1r_tmate.config`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import KNOB_TARGETS, TOGGLE_ACTIONS, Config
from .display import Display
from .sdrmax import SdrMax, SdrMaxError
from .tmate2 import ButtonEvent, EncoderEvent, Tmate2

log = logging.getLogger(__name__)

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

#: Rewrite the panel at least this often, in seconds.
DISPLAY_REFRESH = 1.0
#: How long a transient label stays on the main display.
OVERLAY_SECONDS = 1.5

FREQ_MIN = 10_000
FREQ_MAX = 62_500_000

FILTER_MIN_WIDTH = 100
FILTER_MAX_WIDTH = 20_000
FILTER_STEP = 50
#: SDRMAX sometimes holds filter edges far outside the audio range (values like
#: -314169..-307519 have been seen on its own display, with a correct width).
#: Anything beyond this is treated as unusable rather than adopted.
FILTER_SANE_LIMIT = 20_000

#: Which edge the filter knob moves.  A single sideband has to keep the edge
#: nearest the carrier where it is - opening a USB filter downwards turns it
#: into DSB and defeats the point of the mode - so only the outer edge moves.
#: +1 keeps the low edge and moves the high one, -1 the other way round, and a
#: mode that is not listed opens symmetrically.
SIDEBAND = {"USB": 1, "DIG": 1, "LSB": -1}

#: Passband to fall back on per mode when the filter in SDRMAX is unusable.
DEFAULT_FILTERS = {
    "AM": (-3325, 3325), "SAM": (-3325, 3325), "DSB": (-3000, 3000),
    "USB": (100, 2900), "LSB": (-2900, -100), "CW": (-250, 250),
    "FMN": (-6000, 6000), "DIG": (100, 2900),
}

#: Where an unreadable knob target starts from, since it cannot be adopted.
UNREADABLE_START = 20


def filter_is_usable(low: int, high: int) -> bool:
    return -FILTER_SANE_LIMIT <= low < high <= FILTER_SANE_LIMIT


def _state_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "qs1r-tmate" / "state.json"


@dataclass
class State:
    """The little that is worth remembering between runs.

    Everything else - frequency, mode, filter, the knob targets - is read back
    from the receiver on startup, so it can never be stale.
    """

    step_index: int = 2
    _path: Path = field(default_factory=_state_path, repr=False, compare=False)

    @classmethod
    def load(cls) -> "State":
        try:
            raw = json.loads(_state_path().read_text(encoding="utf-8"))
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
    def __init__(self, radio: SdrMax, controller: Tmate2, config: Config,
                 state: State | None = None, display: Display | None = None,
                 dry_run: bool = False) -> None:
        self.radio = radio
        self.controller = controller
        self.config = config
        self.state = state or State.load()
        self.display = display
        self.dry_run = dry_run

        # The receiver as last seen.
        self.frequency = 0
        self.offset = 0
        self.mode = "USB"
        self.filter_low, self.filter_high = -3000, 3000
        self.muted = False
        self.toggles: dict[str, bool] = {}
        self.knob_values: dict[str, int] = {}

        self._last_command_at = 0.0
        self._last_sync_at = 0.0
        self._last_display_at = 0.0
        self._last_smeter_at = 0.0
        self._smeter_dbm: float | None = None
        self._overlay: str | None = None
        self._overlay_until = 0.0

        if self.display is not None:
            self.display.set_rgb(*self.config.backlight)
            # Every report replaces the whole device state, so the encoder
            # profile has to ride along on all of them.  All three speeds are 1:
            # no hardware acceleration, one count per detent.
            self.display.set_encoder_profile(1, 1, 1, 32, 64)

    # -- derived ------------------------------------------------------------

    @property
    def tuned(self) -> int:
        """The frequency actually being received: centre plus cursor offset."""
        return self.frequency + self.offset

    @property
    def step(self) -> int:
        steps = self.config.steps
        return steps[self.state.step_index % len(steps)]

    def _default_filter(self) -> tuple[int, int]:
        return DEFAULT_FILTERS.get(self.mode, (-3000, 3000))

    # -- outgoing -----------------------------------------------------------

    def _touch(self) -> None:
        self._last_command_at = time.monotonic()

    def _show(self, text: str) -> None:
        """Put a transient label on the main display."""
        self._overlay = text
        self._overlay_until = time.monotonic() + OVERLAY_SECONDS

    def _apply_frequency(self) -> None:
        log.info("VFO %10d Hz  (step %d Hz)", self.tuned, self.step)
        self._touch()
        if not self.dry_run:
            self.radio.set_frequency(self.frequency)

    def _apply_mode(self) -> None:
        log.info("mode %s", self.mode)
        self._touch()
        if not self.dry_run:
            self.radio.set_mode(self.mode)

    def _apply_filter(self) -> None:
        log.info("filter %d .. %d Hz", self.filter_low, self.filter_high)
        self._touch()
        self._show("FIL %d" % (self.filter_high - self.filter_low))
        if not self.dry_run:
            self.radio.set_filter(self.filter_low, self.filter_high)

    def _apply_knob(self, action: str) -> None:
        target = KNOB_TARGETS[action]
        value = self.knob_values[action]
        log.info("%s %d", target.label, value)
        self._touch()
        self._show("%s %d" % (target.label, value))
        if not self.dry_run:
            self.radio.command(target.name, value)

    # -- incoming -----------------------------------------------------------

    def adopt_from_radio(self) -> None:
        """Take the receiver's current settings as the starting state."""
        if self.dry_run:
            return
        snapshot = self.radio.snapshot()
        self.frequency = snapshot["frequency"]
        self.offset = snapshot["offset"]
        self.mode = snapshot["mode"]

        low, high = snapshot["filter_low"], snapshot["filter_high"]
        if filter_is_usable(low, high):
            self.filter_low, self.filter_high = low, high
        else:
            log.warning("SDRMAX reports an unusable filter (%d..%d Hz); the %s "
                        "default will be used if the filter knob is turned",
                        low, high, self.mode)
            self.filter_low, self.filter_high = self._default_filter()

        for action in set(self.config.knob_actions.values()):
            if action == "filter":
                continue
            target = KNOB_TARGETS[action]
            if target.readable:
                try:
                    self.knob_values[action] = int(float(
                        self.radio.query(target.name)))
                    continue
                except (OSError, SdrMaxError) as exc:
                    log.debug("cannot read %s: %s", target.name, exc)
            self.knob_values[action] = UNREADABLE_START

        log.info("adopted from SDRMAX: %d Hz (offset %+d), %s, filter %d..%d Hz%s",
                 self.tuned, self.offset, self.mode,
                 self.filter_low, self.filter_high,
                 "".join(", %s %d" % (KNOB_TARGETS[a].label, v)
                         for a, v in sorted(self.knob_values.items())))

    def sync_from_radio(self) -> bool:
        """Adopt changes made in SDRMAX itself.  True if anything moved."""
        if self.dry_run:
            return False
        try:
            status = self.radio.get_status()
            mode = self.radio.get_mode()
        except (OSError, SdrMaxError) as exc:
            log.debug("read-back failed: %s", exc)
            return False

        changed = False
        if status["frequency"] != self.frequency:
            log.info("SDRMAX moved the VFO to %d Hz", status["frequency"])
            self.frequency = status["frequency"]
            changed = True
        if status["offset"] != self.offset:
            log.info("SDRMAX moved the offset tune to %+d Hz (receiving %d Hz)",
                     status["offset"], status["frequency"] + status["offset"])
            self.offset = status["offset"]
            changed = True
        if mode != self.mode:
            log.info("SDRMAX changed the mode to %s", mode)
            self.mode = mode
            changed = True

        for action, value in list(self.knob_values.items()):
            target = KNOB_TARGETS[action]
            if not target.readable:
                continue
            try:
                current = int(float(self.radio.query(target.name)))
            except (OSError, SdrMaxError):
                continue
            if current != value:
                log.info("SDRMAX changed %s to %d", target.label, current)
                self.knob_values[action] = current
                changed = True
        return changed

    def read_smeter(self) -> bool:
        """Read the signal level.  True if the panel should be redrawn."""
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

    # -- panel --------------------------------------------------------------

    def refresh_display(self) -> None:
        if self.display is None:
            return
        panel = self.display.clear()
        if self._overlay is not None and time.monotonic() < self._overlay_until:
            panel.set_main_text(self._overlay)
        else:
            self._overlay = None
            panel.set_frequency(self.tuned)
            panel.set_flag("shift", self.offset != 0)
            panel.set_underline(len(str(self.step)))
        panel.set_mode(self.mode)
        panel.set_flag("vfo")
        panel.set_flag("rx")
        if self.muted:
            panel.set_flag("vol")
        if self._smeter_dbm is not None:
            panel.set_smeter_scale()
            panel.set_smeter(self._smeter_dbm)
        try:
            self.controller.write(panel.render())
        except OSError as exc:
            log.debug("display write failed: %s", exc)
        self._last_display_at = time.monotonic()

    # -- controls -----------------------------------------------------------

    def _tune(self, detents: int) -> None:
        centre = self.frequency + detents * self.step
        self.frequency = max(FREQ_MIN, min(FREQ_MAX, centre))
        self._apply_frequency()

    def _adjust_knob(self, action: str, detents: int) -> None:
        if action == "filter":
            self._adjust_filter(detents)
            return
        target = KNOB_TARGETS[action]
        value = self.knob_values.get(action, UNREADABLE_START)
        value = max(target.low, min(target.high, value + detents * target.step))
        if value == self.knob_values.get(action):
            return
        self.knob_values[action] = value
        self._apply_knob(action)

    def _adjust_filter(self, detents: int) -> None:
        widen = detents * FILTER_STEP
        low, high = self.filter_low, self.filter_high
        side = SIDEBAND.get(self.mode)

        if not filter_is_usable(low, high) or not self._filter_suits_mode(low, high):
            # SDRMAX hands out symmetric passbands even in USB and LSB, and the
            # previous mode's passband survives a mode change.  Widening one of
            # those opens the wrong edge, or skews a symmetric mode off centre,
            # so start from the passband this mode should have.
            low, high = self._default_filter()

        if side is None:
            low -= widen
            high += widen
        elif side > 0:      # USB: the low edge stays at the carrier
            high += widen
        else:               # LSB: the high edge stays at the carrier
            low -= widen

        if not FILTER_MIN_WIDTH <= high - low <= FILTER_MAX_WIDTH:
            return
        self.filter_low, self.filter_high = low, high
        self._apply_filter()

    def _filter_suits_mode(self, low: int, high: int) -> bool:
        """Is this passband shaped the way the current mode wants it?"""
        side = SIDEBAND.get(self.mode)
        if side is None:
            return low < 0 < high   # symmetric modes straddle the carrier
        return low >= 0 if side > 0 else high <= 0

    def _cycle_step(self) -> None:
        self.state.step_index = (self.state.step_index + 1) % len(self.config.steps)
        log.info("tuning step %d Hz", self.step)
        self._show("STEP %d" % self.step)

    def _toggle(self, action: str) -> None:
        switch = TOGGLE_ACTIONS[action]
        if switch is None:  # write-only, so the state is tracked here
            self.muted = not self.muted
            state = self.muted
            switch = "mute"
        else:
            state = not self.toggles.get(action, False)
            self.toggles[action] = state
        log.info("%s %s", action, "on" if state else "off")
        self._show("%s %s" % (action.upper()[:5], "ON" if state else "OFF"))
        self._touch()
        if not self.dry_run:
            self.radio.command(switch, 1 if state else 0)

    def _press(self, button: str) -> bool:
        action = self.config.buttons.get(button, "none")
        if action == "none":
            return False
        if action.startswith("mode:"):
            self.mode = action.split(":", 1)[1].upper()
            self._apply_mode()
            return True
        if action == "step":
            self._cycle_step()
            return True
        if action == "filter_reset":
            self.filter_low, self.filter_high = self._default_filter()
            self._apply_filter()
            return True
        if action in TOGGLE_ACTIONS:
            self._toggle(action)
            return True
        return False

    def handle(self, events) -> bool:
        """Apply a batch of controller events.  True if anything changed."""
        detents: dict[str, int] = {}
        changed = False

        for event in events:
            if isinstance(event, EncoderEvent):
                sign = self.config.encoder_signs.get(event.encoder, 1)
                detents[event.encoder] = (detents.get(event.encoder, 0)
                                          + event.delta * sign)
            elif isinstance(event, ButtonEvent) and event.pressed:
                changed |= self._press(event.button)

        # Coalesce encoder motion: one command per poll, not one per detent.
        if detents.get("MAIN"):
            self._tune(detents["MAIN"])
            changed = True
        for name in ("E1", "E2"):
            if detents.get(name):
                self._adjust_knob(self.config.knob_actions[name], detents[name])
                changed = True
        return changed

    # -- main loop ----------------------------------------------------------

    def run(self, poll_ms: int = 10, save_interval: float = 5.0,
            duration: float = 0.0, should_stop=None) -> None:
        log.info("bridge running\n%s", describe_bindings(self.config))
        self.adopt_from_radio()
        self.refresh_display()
        started = last_save = time.monotonic()
        dirty = False
        try:
            while True:
                if duration and time.monotonic() - started >= duration:
                    log.info("duration reached, stopping")
                    break
                if should_stop is not None and should_stop():
                    log.info("stop requested")
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


def describe_bindings(config: Config) -> str:
    lines = ["  main knob        tune, step %s Hz"
             % "/".join(str(s) for s in config.steps)]
    for name in ("E1", "E2"):
        action = config.knob_actions[name]
        label = "filter width" if action == "filter" else KNOB_TARGETS[action].label
        lines.append("  %-16s %s" % (name + " knob", label))
    for button in ("F1", "F2", "F3", "F4", "F5", "F6", "MAIN", "E1", "E2"):
        lines.append("  %-16s %s" % ("press " + button, config.buttons[button]))
    return "\n".join(lines)
