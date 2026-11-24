"""Is a file on a spinning hard drive? Then reading several files at once makes the drive head jump between
them, which is far slower than one after another; on SSDs reading in parallel is faster.

Windows asks the volume whether it has a seek penalty (no administrator rights needed). Linux reads the block
device's ``queue/rotational``. Unknown counts as not spinning.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


def _anchor(path: Path) -> str:
    return os.path.splitdrive(os.path.abspath(path))[0].upper() if sys.platform == "win32" else str(path)


@lru_cache(maxsize=64)
def _windows_spinning(drive: str) -> bool:
    if sys.platform != "win32":
        return False
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
    if sys.platform == "win32":
        return False
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
        if sys.platform == "win32":
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
        key = _anchor(p) if sys.platform == "win32" else str(dev)
        if key in seen:
            continue
        seen.add(key)
        if spinning(Path(p)):
            return 1
    return parallel


NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "sshfs", "9p", "afs", "fuse.rclone",
              "davfs", "fuse.davfs2", "ceph", "glusterfs", "afpfs", "webdav", "macfuse", "osxfuse"}


@lru_cache(maxsize=1)
def _mac_mounts() -> tuple[tuple[str, str], ...]:
    """(mount point, file system type) from macOS's ``mount``, longest mount point first."""

    try:
        out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    found = []
    for line in out.splitlines():           # "//me@nas/share on /Volumes/share (smbfs, nodev, nosuid, mounted by me)"
        m = re.match(r".* on (.+) \(([^,)]+)", line)
        if m:
            found.append((m.group(1), m.group(2).strip()))
    return tuple(sorted(found, key=lambda f: -len(f[0])))


def mac_mount_fs(path: Path) -> str:
    text = os.path.realpath(path)
    for point, fs in _mac_mounts():
        if text == point or text.startswith(point.rstrip("/") + "/"):
            return fs
    return ""


@lru_cache(maxsize=64)
def _linux_mount_fs(dev: int) -> str:
    if sys.platform == "win32":
        return ""
    """The file system type of the mount that device ``dev`` belongs to ("" if unknown)."""
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 10 or ":" not in parts[2]:
                    continue
                major, minor = parts[2].split(":", 1)
                if (int(major), int(minor)) == (os.major(dev), os.minor(dev)):
                    return parts[parts.index("-") + 1] if "-" in parts else ""
    except (OSError, ValueError):
        pass
    return ""


def network(path: Path) -> bool:
    """True when ``path`` is on a network share (a mapped drive, a UNC path, NFS/SMB/SSHFS mounts)."""
    try:
        text = os.path.abspath(path)
        if sys.platform == "win32":
            if text.startswith(("\\\\", "//")) and not text.startswith("\\\\?\\"):
                return True
            import ctypes

            drive = _anchor(path)
            return bool(drive) and ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4     # DRIVE_REMOTE
        if sys.platform == "darwin":
            return mac_mount_fs(path) in NETWORK_FS
        return _linux_mount_fs(os.stat(path).st_dev) in NETWORK_FS
    except Exception:  # noqa: BLE001 - unknown: treat it as local
        from .log import ignored
        ignored("drives.network")
        return False


SLOW_PARALLEL = 3          # files worked on at once when they're on a hard drive or a network share


def slow(paths: list[Path], limit: int = 200) -> str:
    """"network" or "hdd" if any of ``paths`` (the first ``limit`` drives' worth) is on one, else ""."""
    seen: set[str] = set()
    found = ""
    for p in paths[:limit]:
        try:
            dev = os.stat(p).st_dev
        except OSError:
            continue
        key = _anchor(p) if sys.platform == "win32" else str(dev)
        if key in seen:
            continue
        seen.add(key)
        if network(Path(p)):
            return "network"
        if spinning(Path(p)):
            found = "hdd"
    return found


__all__ = ["spinning", "readers", "network", "slow", "SLOW_PARALLEL"]
