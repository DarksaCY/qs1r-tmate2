"""Probe what individual bytes of the Tmate 2 output report actually do.

Sets one byte of the report at a time while showing that byte's index on the
main display, so the panel says which byte is being driven. Used to pin down
fields that the published map gets wrong.

    python tools/probe_bytes.py 34 39          # bytes 34..39, one at a time
    python tools/probe_bytes.py 34 39 --hold 3 # slower
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from qs1r_tmate.display import Display, REPORT_SIZE
from qs1r_tmate.tmate2 import Tmate2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=int)
    parser.add_argument("last", type=int)
    parser.add_argument("--value", type=lambda s: int(s, 0), default=0xFF)
    parser.add_argument("--hold", type=float, default=2.5)
    args = parser.parse_args()

    with Tmate2() as controller:
        for index in range(args.first, min(args.last, REPORT_SIZE - 1) + 1):
            panel = Display()
            panel.set_encoder_profile(1, 1, 1, 32, 64)
            panel.set_main_text(f"B {index}")
            report = bytearray(panel.render())
            report[index] = args.value
            controller.write(bytes(report))
            print(f"byte {index} = 0x{args.value:02x}")
            sys.stdout.flush()
            time.sleep(args.hold)

        panel = Display()
        panel.set_encoder_profile(1, 1, 1, 32, 64)
        panel.set_main_text("DONE")
        controller.write(panel.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
