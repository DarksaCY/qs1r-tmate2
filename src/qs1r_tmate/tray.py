"""System-tray front end.

Runs the bridge on a worker thread so it survives SDRMAX restarts, and offers
just what is useful from the tray: see whether it is connected, open the
configuration file, apply it, and quit.
"""

from __future__ import annotations

import logging
import os
import threading

from . import autostart, service
from .config import Config, config_path

log = logging.getLogger(__name__)

ICON_SIZE = 64


def _icon_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    amber = (255, 160, 0, 255)
    # a tuning knob: ring plus a pointer at one o'clock
    draw.ellipse((4, 4, ICON_SIZE - 5, ICON_SIZE - 5), outline=amber, width=6)
    draw.line((ICON_SIZE // 2, ICON_SIZE // 2, ICON_SIZE - 16, 16),
              fill=amber, width=6)
    return image


class TrayApp:
    def __init__(self, options: service.Options) -> None:
        self.options = options
        self.status = service.Status()
        self._restart = threading.Event()
        self._quit = threading.Event()

    # -- worker -------------------------------------------------------------

    def _should_stop(self) -> bool:
        return self._restart.is_set() or self._quit.is_set()

    def _worker(self) -> None:
        while not self._quit.is_set():
            config = Config.load()
            try:
                service.run(config, self.options,
                            should_stop=self._should_stop, status=self.status)
            except Exception:  # noqa: BLE001 - the tray must not die with it
                log.exception("bridge stopped unexpectedly")
            self._restart.clear()

    # -- menu ---------------------------------------------------------------

    def _label(self, _item=None) -> str:
        return ("Connected" if self.status.connected
                else "Waiting: %s" % self.status.detail)

    def _open_config(self, *_args) -> None:
        path = config_path()
        if not path.exists():
            Config.load()  # writes the commented default
        try:
            os.startfile(path)  # noqa: S606 - opening the user's own config
        except OSError as exc:
            log.error("could not open %s: %s", path, exc)

    def _apply_config(self, *_args) -> None:
        log.info("reloading the configuration")
        self._restart.set()

    def _autostart_enabled(self, _item=None) -> bool:
        return autostart.enabled()

    def _toggle_autostart(self, *_args) -> None:
        state = autostart.toggle()
        log.info("start at login: %s", "on" if state else "off")

    def _exit(self, icon, *_args) -> None:
        self._quit.set()
        icon.stop()

    def run(self) -> int:
        import pystray

        worker = threading.Thread(target=self._worker, daemon=True)
        worker.start()

        menu = pystray.Menu(
            pystray.MenuItem(self._label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open configuration", self._open_config),
            pystray.MenuItem("Apply configuration", self._apply_config),
            pystray.MenuItem("Start at login", self._toggle_autostart,
                             checked=self._autostart_enabled),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._exit),
        )
        icon = pystray.Icon("qs1r-tmate", _icon_image(), "QS1R / Tmate 2", menu)
        icon.run()

        self._quit.set()
        worker.join(timeout=5.0)
        return 0


def main(options: service.Options) -> int:
    return TrayApp(options).run()
