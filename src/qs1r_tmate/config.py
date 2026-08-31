"""User configuration: what the knobs and buttons do.

Lives in ``%APPDATA%\\qs1r-tmate\\config.toml`` and is written with comments the
first time the bridge runs, so it can be edited by hand without reading the
source.  Runtime state - which tuning step was last selected - stays separate,
in ``%LOCALAPPDATA%``.

Saving regenerates the whole file from the current values, so hand-written
comments of your own do not survive a ``--invert`` or ``--backlight``; the
values themselves always do.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

try:
    import tomllib  # Python 3.11 and later
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_STEPS = [1, 10, 50, 100, 500, 1000, 5000, 10000]


@dataclass
class KnobTarget:
    """A single numeric receiver setting that a small knob can drive."""

    name: str          #: protocol name, used for both ``>set`` and ``?query``
    label: str         #: shown on the panel while it is being changed
    low: int
    high: int
    step: int
    readable: bool = True


#: What a small knob may be bound to.  "filter" is special-cased in the bridge
#: because it moves two values at once.
KNOB_TARGETS = {
    "agc": KnobTarget("agcthreshold", "AGC", -140, 0, 1),
    "squelch": KnobTarget("squelchthreshold", "SQL", -140, 0, 1),
    "nb1": KnobTarget("anbthreshold", "NB1", 0, 100, 1),
    "nb2": KnobTarget("bnbthreshold", "NB2", 0, 100, 1),
    # Volume can be set but never read back, so the bridge cannot know where it
    # starts; it is offered, but AGC is the better knob.
    "volume": KnobTarget("vol", "VOL", 0, 100, 2, readable=False),
}

#: Buttons may toggle any of these.  The value is the protocol switch name, or
#: None when the setting can be written but not read.
TOGGLE_ACTIONS = {
    "mute": None,
    "nb1": "anbswitch",
    "nb2": "bnbswitch",
    "nr": "NoiseReductionSwitch",
    "anf": "AutoNotchSwitch",
    "squelch": "squelchswitch",
    "binaural": "BinauralSwitch",
    "record": "WavRecord",
}

#: Actions that are not a simple toggle.
SPECIAL_ACTIONS = ("step", "filter_reset", "none")

MODES = ("AM", "SAM", "LSB", "USB", "DSB", "CW", "FMN", "DIG")

DEFAULTS = {
    "connection": {"host": "127.0.0.1", "port": 43067},
    "tuning": {"steps": list(DEFAULT_STEPS), "default_step": 50},
    "encoders": {
        "main": -1, "e1": -1, "e2": 1,
        "e1_action": "agc", "e2_action": "filter",
    },
    "buttons": {
        "f1": "mode:LSB", "f2": "mode:SAM", "f3": "mode:CW",
        "f4": "mode:DSB", "f5": "mode:USB", "f6": "mode:AM",
        "main": "step", "e1": "mute", "e2": "filter_reset",
    },
    "display": {"enabled": True, "backlight": [255, 160, 0]},
}

_TEMPLATE = """# qs1r-tmate configuration
#
# Edit and restart the bridge.  Delete this file to get the defaults back.

[connection]
host = "{host}"
# 43067 is the RX2 command channel.  Do not use 43065: the SDRMAX GUI holds it,
# and a second client there is accepted but never answered.
port = {port}

[tuning]
# Cycled by whichever button is bound to "step".
steps = {steps}
default_step = {default_step}

[encoders]
# Direction: 1 or -1.  Clockwise should mean "more" - note that a higher AGC
# threshold is quieter, which is why e1 is -1 by default.
main = {main}
e1 = {e1}
e2 = {e2}
# What the small knobs adjust: {knob_choices}
# "volume" works but cannot be read back, so its position is guesswork.
e1_action = "{e1_action}"
e2_action = "{e2_action}"

[buttons]
# Actions: "mode:AM" (or SAM LSB USB DSB CW FMN DIG),
#          toggles - {toggle_choices},
#          "step" cycles the tuning step, "filter_reset" restores the passband,
#          "none" does nothing.
f1 = "{f1}"
f2 = "{f2}"
f3 = "{f3}"
f4 = "{f4}"
f5 = "{f5}"
f6 = "{f6}"
main = "{main_button}"
e1 = "{e1_button}"
e2 = "{e2_button}"

[display]
enabled = {enabled}
# The green LED is much weaker than red and blue, so these do not behave like
# sRGB: 255,255,255 looks purple, white is near 32,255,32, amber is 255,160,0.
backlight = {backlight}
"""


def config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "qs1r-tmate" / "config.toml"


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 43067
    steps: list = field(default_factory=lambda: list(DEFAULT_STEPS))
    default_step: int = 50
    encoder_signs: dict = field(
        default_factory=lambda: {"MAIN": -1, "E1": -1, "E2": 1})
    knob_actions: dict = field(
        default_factory=lambda: {"E1": "agc", "E2": "filter"})
    #: The panel keys do not reach the software in their printed order: panel
    #: F1..F6 arrive as F6, F2, F1, F4, F5, F3.  These defaults are arranged so
    #: that the panel reads AM, SAM, LSB, DSB, USB, CW from left to right.
    buttons: dict = field(default_factory=lambda: {
        "F1": "mode:LSB", "F2": "mode:SAM", "F3": "mode:CW",
        "F4": "mode:DSB", "F5": "mode:USB", "F6": "mode:AM",
        "MAIN": "step", "E1": "mute", "E2": "filter_reset",
    })
    display_enabled: bool = True
    backlight: list = field(default_factory=lambda: [255, 160, 0])
    path: Path = field(default_factory=config_path, repr=False)

    # -- loading ------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_path()
        config = cls(path=path)
        if not path.exists():
            config.save()
            log.info("wrote a default configuration to %s", path)
            return config
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            log.warning("cannot read %s (%s); using defaults", path, exc)
            return config

        conn = raw.get("connection", {})
        config.host = conn.get("host", config.host)
        config.port = int(conn.get("port", config.port))

        tuning = raw.get("tuning", {})
        steps = tuning.get("steps")
        if steps:
            config.steps = [int(s) for s in steps]
        config.default_step = int(tuning.get("default_step", config.default_step))

        enc = raw.get("encoders", {})
        for key, name in (("main", "MAIN"), ("e1", "E1"), ("e2", "E2")):
            if key in enc:
                config.encoder_signs[name] = 1 if int(enc[key]) >= 0 else -1
        for key, name in (("e1_action", "E1"), ("e2_action", "E2")):
            if key in enc:
                config.knob_actions[name] = str(enc[key])

        for key, value in raw.get("buttons", {}).items():
            config.buttons[key.upper()] = str(value)

        display = raw.get("display", {})
        config.display_enabled = bool(display.get("enabled", config.display_enabled))
        backlight = display.get("backlight")
        if backlight and len(backlight) == 3:
            config.backlight = [max(0, min(255, int(v))) for v in backlight]

        config.validate()
        return config

    def validate(self) -> None:
        """Warn about unusable settings rather than failing to start."""
        for name, action in self.knob_actions.items():
            if action != "filter" and action not in KNOB_TARGETS:
                log.warning("unknown knob action %r on %s; falling back to agc",
                            action, name)
                self.knob_actions[name] = "agc"
        for button, action in self.buttons.items():
            if action.startswith("mode:"):
                if action.split(":", 1)[1].upper() in MODES:
                    continue
            elif action in TOGGLE_ACTIONS or action in SPECIAL_ACTIONS:
                continue
            log.warning("unknown action %r on %s; ignoring it", action, button)
            self.buttons[button] = "none"
        if self.default_step not in self.steps:
            self.steps = sorted(set(self.steps + [self.default_step]))

    # -- saving -------------------------------------------------------------

    def save(self) -> None:
        text = _TEMPLATE.format(
            host=self.host, port=self.port,
            steps=self.steps, default_step=self.default_step,
            main=self.encoder_signs["MAIN"],
            e1=self.encoder_signs["E1"], e2=self.encoder_signs["E2"],
            e1_action=self.knob_actions["E1"], e2_action=self.knob_actions["E2"],
            knob_choices=", ".join(sorted(KNOB_TARGETS) + ["filter"]),
            toggle_choices=", ".join(sorted(TOGGLE_ACTIONS)),
            f1=self.buttons["F1"], f2=self.buttons["F2"], f3=self.buttons["F3"],
            f4=self.buttons["F4"], f5=self.buttons["F5"], f6=self.buttons["F6"],
            main_button=self.buttons["MAIN"], e1_button=self.buttons["E1"],
            e2_button=self.buttons["E2"],
            enabled=str(self.display_enabled).lower(),
            backlight=self.backlight,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(text, encoding="utf-8")
        except OSError as exc:
            log.warning("could not save the configuration: %s", exc)
