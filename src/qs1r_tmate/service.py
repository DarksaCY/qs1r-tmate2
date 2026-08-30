"""Connect, run the bridge, and keep it running.

Shared by the console entry point and the tray application.  On autostart the
bridge may well come up before SDRMAX does, or before the controller is plugged
in, so the default behaviour is to wait and retry rather than exit.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .bridge import Bridge, State
from .config import Config
from .display import Display
from .sdrmax import SdrMax, SdrMaxError
from .tmate2 import Tmate2, Tmate2NotFound

log = logging.getLogger(__name__)

RETRY_SECONDS = 5.0


@dataclass
class Options:
    dry_run: bool = False
    duration: float = 0.0
    use_display: bool = True
    retry: bool = True


class Status:
    """What the tray shows.  Plain attributes, read from another thread."""

    def __init__(self) -> None:
        self.connected = False
        self.detail = "starting"

    def set(self, connected: bool, detail: str) -> None:
        self.connected = connected
        self.detail = detail
        log.debug("status: %s (%s)", detail, "connected" if connected else "waiting")


def run_once(config: Config, options: Options, should_stop=None,
             status: Status | None = None) -> None:
    """One full session.  Raises if the receiver or controller is unavailable."""
    status = status or Status()
    radio = SdrMax(config.host, config.port)
    controller = Tmate2()
    try:
        if not options.dry_run:
            radio.connect()
            log.info("SDRMAX V %s, server pid %d, receiver %s",
                     radio.get_version(), radio.server_pid(),
                     "running" if radio.is_running() else "stopped")
        controller.open()
        display = Display() if (options.use_display and config.display_enabled) else None
        status.set(True, "connected")
        Bridge(radio, controller, config, State.load(), display,
               dry_run=options.dry_run).run(
                   duration=options.duration, should_stop=should_stop)
    finally:
        status.set(False, "disconnected")
        controller.close()
        radio.close()


def run(config: Config, options: Options, should_stop=None,
        status: Status | None = None) -> int:
    """Run the bridge, optionally waiting for the hardware to appear."""
    status = status or Status()
    while True:
        try:
            run_once(config, options, should_stop, status)
            return 0
        except Tmate2NotFound as exc:
            problem = "%s - is the Tmate 2 plugged in?" % exc
        except (OSError, SdrMaxError) as exc:
            problem = ("cannot reach SDRMAX V on %s:%d - is it running? (%s)"
                       % (config.host, config.port, exc))

        if not options.retry:
            log.error("%s", problem)
            return 1
        status.set(False, problem)
        log.warning("%s; retrying in %.0f s", problem, RETRY_SECONDS)

        waited = 0.0
        while waited < RETRY_SECONDS:
            if should_stop is not None and should_stop():
                return 0
            time.sleep(0.25)
            waited += 0.25
