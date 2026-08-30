"""Command line entry point: ``python -m qs1r_tmate``."""

from __future__ import annotations

import argparse
import logging
import sys

from .bridge import TUNE_SIGN, Bridge, State, describe_bindings
from .display import Display
from .sdrmax import PORT_RX2_CMD, SdrMax, SdrMaxError
from .tmate2 import Tmate2, Tmate2NotFound


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qs1r_tmate",
        description="Drive SDRMAX V (QS1R) from a WoodBox Radio / Elad Tmate 2.",
        epilog=describe_bindings(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="SDRMAX V host (default: %(default)s)")
    parser.add_argument("--port", type=int, default=PORT_RX2_CMD,
                        help="command port (default: %(default)s, the free RX2 channel)")
    parser.add_argument("--freq", type=int, default=None,
                        help="tune the receiver to this frequency in Hz at startup")
    parser.add_argument("--no-display", action="store_true",
                        help="leave the controller LCD alone")
    parser.add_argument("--backlight", metavar="R,G,B",
                        help="backlight colour, e.g. 255,160,0; remembered for "
                             "future runs")
    parser.add_argument("--invert", metavar="LIST",
                        help="flip the direction of these knobs, e.g. MAIN,E1; "
                             "remembered for future runs")
    parser.add_argument("--reverse-tuning", action="store_true",
                        help="flip the main knob direction (same as --invert MAIN)")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="stop after this many seconds (0 = run until Ctrl-C)")
    parser.add_argument("--dry-run", action="store_true",
                        help="decode the controller but send nothing to SDRMAX")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log every report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    state = State.load()
    if args.invert:
        for name in (n.strip().upper() for n in args.invert.split(",")):
            state.encoder_signs[name] = -state.encoder_signs.get(name, 1)
        state.save()
    if args.backlight:
        state.backlight = [int(v) for v in args.backlight.split(",")]
        state.save()

    radio = SdrMax(args.host, args.port)
    if not args.dry_run:
        try:
            radio.connect()
            logging.info("SDRMAX V %s, server pid %d, receiver %s",
                         radio.get_version(), radio.server_pid(),
                         "running" if radio.is_running() else "stopped")
            if args.freq is not None:
                radio.set_frequency(args.freq)
        except (OSError, SdrMaxError) as exc:
            logging.error("cannot reach SDRMAX V on %s:%d - is it running? (%s)",
                          args.host, args.port, exc)
            return 1

    controller = Tmate2()
    try:
        controller.open()
    except Tmate2NotFound as exc:
        logging.error("%s - is the Tmate 2 plugged in?", exc)
        radio.close()
        return 1

    try:
        display = None if args.no_display else Display()
        bridge = Bridge(
            radio, controller, state,
            dry_run=args.dry_run,
            tune_sign=-state.encoder_signs["MAIN"] if args.reverse_tuning else None,
            display=display,
        )
        bridge.run(duration=args.duration)
    finally:
        controller.close()
        radio.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
