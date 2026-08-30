"""PyInstaller entry point for the tray build: defaults to --tray."""
import sys

from qs1r_tmate.__main__ import main

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--tray" not in argv:
        argv.append("--tray")
    sys.exit(main(argv))
