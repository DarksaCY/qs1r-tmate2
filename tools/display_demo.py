"""Put SDRMAX's current frequency and mode on the Tmate 2 LCD.

A first check of the display map against the real panel: it shows the frequency,
lights the mode annunciator, sweeps the S-meter bar, and sets a white backlight.
Run it and look at the controller.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from qs1r_tmate.display import BAR_COUNT, Display
from qs1r_tmate.sdrmax import SdrMax
from qs1r_tmate.tmate2 import Tmate2


def main() -> int:
    with SdrMax() as radio, Tmate2() as controller:
        frequency = radio.get_frequency()
        mode = radio.get_mode()
        print(f"showing {frequency} Hz, {mode}")

        panel = Display()
        panel.set_encoder_profile(1, 1, 1, 32, 64)   # no hardware acceleration
        panel.set_rgb(0xFF, 0xFF, 0xFF)
        panel.set_frequency(frequency)
        panel.set_mode(mode)
        panel.set_flag("vfo")
        panel.set_flag("rx")
        panel.set_smeter_text("59")
        panel.set_flag("s")
        controller.write(panel.render())

        print("sweeping the S-meter bar...")
        for count in list(range(BAR_COUNT + 1)) + list(range(BAR_COUNT, -1, -1)):
            panel.set_bars(count)
            controller.write(panel.render())
            time.sleep(0.06)

        panel.set_bars(9)
        controller.write(panel.render())
        print("done - the panel should now read the frequency above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
