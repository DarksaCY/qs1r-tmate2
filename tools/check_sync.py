"""Live check that the bridge notices changes made in SDRMAX itself.

Starts the bridge, injects a frequency change on a second connection exactly the
way the SDRMAX GUI would, and verifies the bridge adopts it. Restores the
original frequency afterwards. Requires the real hardware and a running SDRMAX.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PORT = 43067
ROOT = Path(__file__).resolve().parent.parent


def talk(*lines: str) -> list[str]:
    s = socket.create_connection(("127.0.0.1", PORT), 3)
    s.settimeout(3)
    s.sendall("".join(l + "\n" for l in lines).encode())
    buf = b""
    while buf.count(b"\n") < len(lines):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return buf.decode("ascii", "replace").split("\n")


def main() -> int:
    original = int(talk("?fhz")[0].split("=")[1])
    target = original + 2000
    print(f"receiver is at {original} Hz; will inject {target} Hz")

    log = Path(tempfile.gettempdir()) / "qs1r_tmate_sync_check.log"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "qs1r_tmate", "--duration", "12"],
            cwd=ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(4.0)          # let the bridge connect and adopt
            talk(f">fhz {target}")   # an external change, as the GUI would make
            time.sleep(2.5)          # let the sync poll notice
        finally:
            proc.wait(timeout=20)

    text = log.read_text(encoding="utf-8")
    talk(f">fhz {original}")
    print(f"restored {original} Hz")

    marker = f"SDRMAX moved the VFO to {target}"
    ok = marker in text
    print("\n".join("  " + l for l in text.splitlines() if "SDRMAX moved" in l
                    or "adopted" in l))
    print("\nRESULT:", "PASS" if ok else "FAIL - bridge did not adopt the change")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
