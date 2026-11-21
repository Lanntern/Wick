"""Is a file on a spinning hard drive? Then reading several files at once makes the drive head jump between
them, which is far slower than one after another; on SSDs reading in parallel is faster.

Windows asks the volume whether it has a seek penalty (no administrator rights needed). Linux reads the block
device's ``queue/rotational``. Unknown counts as not spinning.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _anchor(path: Path) -> str:
    return os.path.splitdrive(os.path.abspath(path))[0].upper() if os.name == "nt" else str(path)


@lru_cache(maxsize=64)
def _windows_spinning(drive: str) -> bool:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    class Query(ctypes.Structure):
        _fields_ = [("PropertyId", ctypes.c_int), ("QueryType", ctypes.c_int), ("Extra", ctypes.c_ubyte * 1)]

    class SeekPenalty(ctypes.Structure):
        _fields_ = [("Version", wintypes.DWORD), ("Size", wintypes.DWORD), ("IncursSeekPenalty", ctypes.c_ubyte)]

    handle = k32.CreateFileW(f"\\\\.\\{drive}", 0, 0x3, None, 3, 0, None)   # no access needed; share read/write
    if not handle or handle == wintypes.HANDLE(-1).value:
        return False
    try:
        query = Query(7, 0)                       # StorageDeviceSeekPenaltyProperty, PropertyStandardQuery
        out = SeekPenalty()
        got = wintypes.DWORD()
        ok = k32.DeviceIoControl(handle, 0x2D1400, ctypes.byref(query), ctypes.sizeof(query), ctypes.byref(out),
                                 ctypes.sizeof(out), ctypes.byref(got), None)      # IOCTL_STORAGE_QUERY_PROPERTY
        return bool(ok and out.IncursSeekPenalty)
    finally:
        k32.CloseHandle(handle)


@lru_cache(maxsize=64)
def _linux_spinning(dev: int) -> bool:
    block = Path(f"/sys/dev/block/{os.major(dev)}:{os.minor(dev)}")
    try:
        node = block.resolve()
    except OSError:
        return False
    for candidate in (node, node.parent):          # (a partition's queue settings are its disk's)
        flag = candidate / "queue" / "rotational"
        try:
            return flag.read_text().strip() == "1"
        except OSError:
            continue
    return False


def spinning(path: Path) -> bool:
    """True when ``path`` is on a drive with moving parts (a hard drive), as far as the system says."""
    try:
        if os.name == "nt":
            drive = _anchor(path)
            return bool(drive) and _windows_spinning(drive)
        return _linux_spinning(os.stat(path).st_dev)
    except Exception:  # noqa: BLE001 - unknown: treat it as an SSD
        from .log import ignored
        ignored("drives.spinning")
        return False


def readers(paths: list[Path], parallel: int) -> int:
    """How many files to read at once: one if any of them is on a spinning drive, else ``parallel``."""
    seen: set[str] = set()
    for p in paths:
        try:
            dev = os.stat(p).st_dev                 # (a file that's gone says nothing about its drive)
        except OSError:
            continue
        key = _anchor(p) if os.name == "nt" else str(dev)
        if key in seen:
            continue
        seen.add(key)
        if spinning(Path(p)):
            return 1
    return parallel


__all__ = ["spinning", "readers"]
