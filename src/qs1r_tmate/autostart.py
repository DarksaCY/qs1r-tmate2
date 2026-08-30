"""Start the bridge when Windows logs in.

Uses the per-user Run key, which needs no administrator rights and is easy to
inspect or remove by hand:

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\qs1r-tmate
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "qs1r-tmate"


def _pythonw() -> str:
    """The windowed interpreter, so autostart does not open a console."""
    executable = Path(sys.executable)
    windowed = executable.with_name("pythonw.exe")
    return str(windowed if windowed.exists() else executable)


def command_line() -> str:
    """What to register, whether running from source or from a built exe."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)
        # The tray build needs no argument; the console build has to be told.
        if "tray" in executable.stem.lower():
            return f'"{executable}"'
        return f'"{executable}" --tray'
    return f'"{_pythonw()}" -m qs1r_tmate --tray'


def enabled() -> bool:
    return current() is not None


def toggle() -> bool:
    """Turn autostart on or off.  Returns the new state."""
    if enabled():
        remove()
        return False
    install()
    return True


def install() -> str:
    import winreg

    command = command_line()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    log.info("autostart installed: %s", command)
    return command


def remove() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return False
    log.info("autostart removed")
    return True


def current() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return value
    except FileNotFoundError:
        return None
