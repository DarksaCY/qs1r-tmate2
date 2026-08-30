"""Command line entry point: ``python -m qs1r_tmate``."""

from __future__ import annotations

import argparse
import logging
import sys

from .bridge import TUNE_SIGN, Bridge, State, describe_bindings
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
                        help="seed the VFO with this frequency in Hz instead of the "
                             "last saved one")
    parser.add_argument("--reverse-tuning", action="store_true",
                        help="flip the main knob's tuning direction")
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
    if args.freq is not None:
        state.frequency = args.freq

    radio = SdrMax(args.host, args.port)
    if not args.dry_run:
        try:
            radio.connect()
            logging.info("SDRMAX V server pid %d, receiver %s",
                         radio.server_pid(),
                         "running" if radio.is_running() else "stopped")
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
        Bridge(
            radio, controller, state,
            dry_run=args.dry_run,
            tune_sign=-TUNE_SIGN if args.reverse_tuning else TUNE_SIGN,
        ).run(duration=args.duration)
    finally:
        controller.close()
        radio.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
