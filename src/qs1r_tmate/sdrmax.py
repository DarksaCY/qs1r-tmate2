"""Client for the undocumented SDRMAX V command server.

SDRMAX V (Software Radio Laboratory LLC, 2013) hosts a QS1RServer object that
listens on four TCP ports.  Its own GUI is just a network client of that server,
so anything the GUI can do is reachable over the wire.

Protocol (reverse engineered, see docs/sdrmax-v-protocol.md):

    request   >command arg\n     ->  OK\n
    query     ?name\n            ->  name=value\n

Requests may be pipelined: several lines in one write produce one response line
each, in order.
"""

from __future__ import annotations

import logging
import socket
import threading

log = logging.getLogger(__name__)

#: RX1 command channel.  The SDRMAX GUI holds this one; a second client is
#: accepted but never answered, so we do not use it.
PORT_RX1_CMD = 43065
#: RX2 command channel.  Unused by the GUI and fully functional - our channel.
PORT_RX2_CMD = 43067
#: GUI update channels for RX1 / RX2.
PORT_RX1_GUI = 43069
PORT_RX2_GUI = 43071

#: Mode argument for ``>mode``, read out of SDRMAX itself: setting each index in
#: turn and asking ``?status``, which answers with the mode by name.  It is not
#: the order of the mode buttons in the GUI, and it is not the order the mode
#: strings appear in the binary - guessing from either gets it wrong.
#:
#: FMW has no button in the GUI at all, so selecting it looks like nothing
#: happened.  9 and above answer ``???``.
MODES = {
    "AM": 0,
    "SAM": 1,
    "FMN": 2,
    "FMW": 3,
    "DSB": 4,
    "LSB": 5,
    "USB": 6,
    "CW": 7,
    "DIG": 8,
}
MODE_NAMES = {v: k for k, v in MODES.items()}


class SdrMaxError(RuntimeError):
    pass


class UnknownQuery(SdrMaxError):
    """The server does not recognise this query name (it answered ``?``)."""


class NotReadable(SdrMaxError):
    """The name is known but cannot be read back (the server answered ``NAK``)."""


class SdrMax:
    """A line-oriented connection to the SDRMAX V command server.

    The connection is re-established transparently if it drops, so the bridge
    keeps working across an SDRMAX restart.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = PORT_RX2_CMD,
                 timeout: float = 2.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""
        self._lock = threading.Lock()

    # -- connection ---------------------------------------------------------

    def connect(self) -> None:
        self.close()
        sock = socket.create_connection((self.host, self.port), self.timeout)
        sock.settimeout(self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._buf = b""
        log.info("connected to SDRMAX V at %s:%d", self.host, self.port)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def __enter__(self) -> "SdrMax":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- raw transport ------------------------------------------------------

    def _readline(self) -> str:
        assert self._sock is not None
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise SdrMaxError("server closed the connection")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode("ascii", "replace").strip()

    def _roundtrip(self, line: str) -> str:
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        self._sock.sendall((line + "\n").encode("ascii"))
        return self._readline()

    def _send(self, line: str, retry: bool = True) -> str:
        with self._lock:
            try:
                return self._roundtrip(line)
            except (OSError, SdrMaxError):
                self.close()
                if not retry:
                    raise
                self.connect()
                return self._roundtrip(line)

    # -- protocol -----------------------------------------------------------

    def command(self, cmd: str, *args) -> str:
        """Send ``>cmd args`` and return the reply (normally ``OK``)."""
        line = ">" + cmd
        if args:
            line += " " + " ".join(str(a) for a in args)
        reply = self._send(line)
        if reply != "OK":
            log.debug("command %r answered %r", line, reply)
        return reply

    def query(self, name: str) -> str:
        """Send ``?name`` and return the value part of the ``name=value`` reply.

        The server answers an unknown name with ``?`` and a known but
        unreadable one with ``NAK``; both are reported as errors.
        """
        reply = self._send("?" + name)
        if reply == "?":
            raise UnknownQuery(name)
        if reply == "NAK":
            raise NotReadable(name)
        key, sep, value = reply.partition("=")
        if not sep:
            raise SdrMaxError(f"malformed reply to ?{name}: {reply!r}")
        return value

    # -- convenience --------------------------------------------------------

    def server_pid(self) -> int:
        return int(self.query("serverpid"))

    def is_running(self) -> bool:
        return self.query("start") == "1"

    def set_frequency(self, hz: int) -> None:
        self.command("fhz", int(hz))

    def set_mode(self, mode: str | int) -> None:
        value = MODES[mode.upper()] if isinstance(mode, str) else int(mode)
        self.command("mode", value)

    def set_filter(self, low: int, high: int) -> None:
        self.command("fl", int(low))
        self.command("fh", int(high))

    def set_volume(self, value: int) -> None:
        self.command("vol", int(value))

    def set_agc_threshold(self, dbm: int) -> None:
        self.command("agcthreshold", int(dbm))

    def get_agc_threshold(self) -> int:
        return int(float(self.query("agcthreshold")))

    def set_mute(self, muted: bool) -> None:
        self.command("mute", 1 if muted else 0)

    def start(self) -> None:
        self.command("start")

    def stop(self) -> None:
        self.command("stop")

    # -- read-back ----------------------------------------------------------
    #
    # Almost every setter has a matching getter, even though the query names do
    # not appear as literals in the binary.  ``?mute`` is the notable exception:
    # it answers NAK.

    def get_frequency(self) -> int:
        return int(self.query("fhz"))

    def get_mode(self) -> str:
        return MODE_NAMES[int(self.query("mode"))]

    def get_filter(self) -> tuple[int, int]:
        return int(self.query("fl")), int(self.query("fh"))

    def get_volume(self) -> int:
        return int(self.query("vol"))

    def get_smeter(self) -> float:
        """Signal level in dBm.

        The query name is absent from the binary and answers to any casing.  It
        is the only route to the signal level: nothing else in the protocol
        reports it.
        """
        return float(self.query("SmeterValue"))

    def get_status(self) -> dict:
        """Mode, centre frequency and offset tune in one round trip.

        ``?status`` answers ``AM,9685030,-44800;``.  The third field is the
        offset of the spectrum cursor from the centre frequency, in Hz, and it
        is the only place the protocol exposes it: clicking the SDRMAX spectrum
        moves the cursor and changes nothing else that can be queried, so the
        frequency actually being received is ``frequency + offset``.
        """
        raw = self.query("status").rstrip(";")
        mode, frequency, offset = raw.split(",")
        return {
            "mode": mode.strip(),
            "frequency": int(frequency),
            "offset": int(offset),
        }

    def get_offset(self) -> int:
        return self.get_status()["offset"]

    def get_samplerate(self) -> int:
        return int(self.query("samplerate"))

    def get_version(self) -> str:
        return self.query("version")

    def snapshot(self) -> dict:
        """Read the settings the bridge cares about in one round trip each."""
        low, high = self.get_filter()
        status = self.get_status()
        return {
            "frequency": status["frequency"],
            "offset": status["offset"],
            "mode": self.get_mode(),
            "filter_low": low,
            "filter_high": high,
            "agc_threshold": self.get_agc_threshold(),
        }
