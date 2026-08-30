"""Command line entry point: ``qs1r-tmate`` or ``python -m qs1r_tmate``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import autostart, service, single_instance
from .bridge import describe_bindings
from .config import Config, config_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qs1r-tmate",
        description="Drive SDRMAX V (QS1R) from a WoodBox Radio / Elad Tmate 2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="What the controls do is set in the configuration file; run\n"
               "--show-config to see where it lives.",
    )
    parser.add_argument("--config", type=Path, default=None,
                        help="use this configuration file instead of the default")
    parser.add_argument("--host", help="override the SDRMAX host")
    parser.add_argument("--port", type=int, help="override the command port")

    parser.add_argument("--tray", action="store_true",
                        help="run in the system tray instead of the console")
    parser.add_argument("--no-display", action="store_true",
                        help="leave the controller LCD alone")
    parser.add_argument("--no-retry", action="store_true",
                        help="exit instead of waiting for SDRMAX or the controller")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="stop after this many seconds (0 = until Ctrl-C)")
    parser.add_argument("--dry-run", action="store_true",
                        help="decode the controller but send nothing to SDRMAX")

    parser.add_argument("--backlight", metavar="R,G,B",
                        help="set the backlight colour in the configuration")
    parser.add_argument("--invert", metavar="LIST",
                        help="flip the direction of these knobs in the "
                             "configuration, e.g. MAIN,E1")

    parser.add_argument("--show-config", action="store_true",
                        help="print the configuration and where it is stored")
    parser.add_argument("--install-autostart", action="store_true",
                        help="start the tray application when Windows logs in")
    parser.add_argument("--remove-autostart", action="store_true",
                        help="undo --install-autostart")

    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _edit_config(config: Config, args) -> bool:
    """Apply the settings flags.  True if the configuration was written."""
    written = False
    if args.invert:
        for name in (n.strip().upper() for n in args.invert.split(",")):
            if name not in config.encoder_signs:
                logging.error("unknown knob %r; expected MAIN, E1 or E2", name)
                continue
            config.encoder_signs[name] = -config.encoder_signs[name]
            logging.info("%s direction is now %+d", name, config.encoder_signs[name])
        written = True
    if args.backlight:
        try:
            red, green, blue = (int(v) for v in args.backlight.split(","))
        except ValueError:
            logging.error("--backlight wants three numbers, e.g. 255,160,0")
        else:
            config.backlight = [red, green, blue]
            logging.info("backlight set to %d,%d,%d", red, green, blue)
            written = True
    if written:
        config.save()
    return written


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.install_autostart:
        print("autostart installed:", autostart.install())
        return 0
    if args.remove_autostart:
        print("autostart removed" if autostart.remove() else "autostart was not set")
        return 0

    config = Config.load(args.config)
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    settings_changed = _edit_config(config, args)

    if args.show_config:
        print("configuration file:", args.config or config_path())
        print("autostart:", autostart.current() or "not installed")
        print()
        print(describe_bindings(config))
        return 0
    if settings_changed and args.duration == 0.0 and not args.tray:
        # A bare settings change should not also start the bridge.
        print("saved to", args.config or config_path())
        return 0

    if not single_instance.acquire():
        logging.error("another qs1r-tmate is already running; two bridges "
                      "fight over the panel, so this one will exit")
        return 1

    options = service.Options(
        dry_run=args.dry_run,
        duration=args.duration,
        use_display=not args.no_display,
        retry=not args.no_retry and args.duration == 0.0,
    )
    try:
        if args.tray:
            from . import tray
            return tray.main(options)
        return service.run(config, options)
    finally:
        single_instance.release()


if __name__ == "__main__":
    sys.exit(main())
