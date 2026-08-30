"""Refuse to run twice at once.

Two bridges are worse than none: each writes the whole 44-byte report and the
whole receiver state, so they overwrite each other and the panel shows a mixture
of both - buttons appear to do nothing or to select the wrong mode.

Implemented with a named Windows mutex, which the kernel releases even if the
process is killed, so there is no stale lock file to clean up.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MUTEX_NAME = "Local\\qs1r-tmate-bridge"
_ERROR_ALREADY_EXISTS = 183

_handle = None


def acquire() -> bool:
    """True if this process now owns the lock, False if another one holds it."""
    global _handle
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover - not Windows
        return True

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                      wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True  # cannot tell; better to run than to refuse
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _handle = handle
    return True


def release() -> None:
    global _handle
    if _handle is None:
        return
    import ctypes

    ctypes.windll.kernel32.CloseHandle(_handle)
    _handle = None
