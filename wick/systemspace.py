"""The space a drive's file browser doesn't explain: what's inside Home's "Everything else".

Windows: the hibernation file, the page and swap files, restore points, and the virtual disks of WSL and Docker.
Linux: the system journal, package managers' download caches, Flatpak runtimes, old Snap versions and the swap
file. Each comes with what it is and, where it's safe, what can be done about it. Nothing here changes anything
by itself: actions are offered, and those needing administrator rights say so.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .cleanup.common import folder_size

IS_WINDOWS = os.name == "nt"
MIN_SIZE = 200 * 1024 ** 2          # smaller items aren't worth explaining
_NOWINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# What each action does (the app's buttons): "hibernation" turns it off, "restore_points" opens Windows'
# System Protection, "virtual_disks" opens Clean Up's Developer tool, "command" copies a command to run.
HIBERNATION, RESTORE_POINTS, VIRTUAL_DISKS, COMMAND = "hibernation", "restore_points", "virtual_disks", "command"
SWAP_FILES = (Path("/swapfile"), Path("/swap.img"))


@dataclass
class Hidden:
    key: str
    title: str
    size: int                       # bytes; 0 when Windows doesn't say (restore points without admin rights)
    about: str
    action: str = ""
    action_label: str = ""
    command: str = ""               # for COMMAND: what to run


def _root_files(drive: Path) -> dict[str, int]:
    """Sizes of the hidden system files in a drive's root (read from the folder listing: they can't be opened)."""
    sizes = {}
    try:
        with os.scandir(drive) as entries:
            for e in entries:
                if e.name.lower() in ("hiberfil.sys", "pagefile.sys", "swapfile.sys"):
                    try:
                        sizes[e.name.lower()] = e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
    except OSError:
        pass
    return sizes


def _restore_points_size() -> int:
    """Space restore points use on all drives (0 when Windows won't say without administrator rights)."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "(Get-CimInstance Win32_ShadowStorage -ErrorAction Stop | "
                              "Measure-Object UsedSpace -Sum).Sum"],
                             capture_output=True, text=True, timeout=30, creationflags=_NOWINDOW)
        return int(out.stdout.strip() or 0) if out.returncode == 0 else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def _windows(drive: Path) -> list[Hidden]:
    system = Path((os.environ.get("SystemDrive") or "C:") + "\\")
    found: list[Hidden] = []
    if os.path.normcase(str(drive)) == os.path.normcase(str(system)):
        files = _root_files(drive)
        if files.get("hiberfil.sys", 0):
            found.append(Hidden(
                "hibernation", "Hibernation file", files["hiberfil.sys"],
                "Windows saves your open work here when the PC hibernates, and uses it to start up faster. If you "
                "never hibernate, turning it off frees all of this. Fast startup then turns off too.",
                HIBERNATION, "Turn Off Hibernation…"))
        page = files.get("pagefile.sys", 0) + files.get("swapfile.sys", 0)
        if page:
            found.append(Hidden(
                "pagefile", "Page file", page,
                "Extra memory on disk that Windows manages by itself. It's best left alone: Windows shrinks it "
                "when it can, and programs can crash without it."))
        found.append(Hidden(
            "restore_points", "Restore points", _restore_points_size(),
            "Snapshots of system files that let Windows undo a bad update or driver. You can limit how much space "
            "they may use, or delete older ones, in System Protection.",
            RESTORE_POINTS, "Open System Protection"))
    try:
        from .cleanup import vdisk

        disks = [p for _, p in vdisk.distros()] + vdisk.docker_disks()
        on_drive = [p for p in disks if os.path.normcase(p.anchor) == os.path.normcase(drive.anchor)]
        size = sum(p.stat().st_size for p in on_drive if p.exists())
    except Exception:  # noqa: BLE001 - WSL isn't there, or can't be asked
        from .log import ignored
        ignored("systemspace._windows")
        size = 0
    if size:
        found.append(Hidden(
            "virtual_disks", "Linux (WSL) and Docker disks", size,
            "Each Linux distro and Docker Desktop keeps its files in one big disk file. It grows as you use it but "
            "never shrinks by itself, even after you delete files inside it. Compacting gives that space back.",
            VIRTUAL_DISKS, "Compact on Clean Up"))
    return found


def _size(path: Path) -> int:
    return folder_size(path)[0] if path.is_dir() else 0


def _snap_old_versions() -> int:
    """Size of the snap packages kept from before their last update."""
    if not shutil.which("snap"):
        return 0
    try:
        out = subprocess.run(["snap", "list", "--all"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    total = 0
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and "disabled" in line:
            snap = Path("/var/lib/snapd/snaps") / f"{parts[0]}_{parts[2]}.snap"
            try:
                total += snap.stat().st_size
            except OSError:
                continue
    return total


def _linux() -> list[Hidden]:
    found: list[Hidden] = []
    journal = _size(Path("/var/log/journal"))
    if journal:
        found.append(Hidden(
            "journal", "System logs", journal,
            "The system journal keeps logs from every service. Logs older than a month are rarely needed.",
            COMMAND, "Copy Command", "sudo journalctl --vacuum-time=4weeks"))
    for path, label, command in ((Path("/var/cache/apt/archives"), "apt", "sudo apt clean"),
                                 (Path("/var/cache/dnf"), "dnf", "sudo dnf clean packages"),
                                 (Path("/var/cache/pacman/pkg"), "pacman", "sudo pacman -Sc")):
        size = _size(path)
        if size:
            found.append(Hidden(
                f"packages_{label}", "Downloaded system updates", size,
                f"Package files {label} downloaded to install updates. They're already installed, so the "
                "downloads aren't needed.", COMMAND, "Copy Command", command))
    flatpak = _size(Path.home() / ".local/share/flatpak") + _size(Path("/var/lib/flatpak"))
    if flatpak and shutil.which("flatpak"):
        found.append(Hidden(
            "flatpak", "Flatpak apps and runtimes", flatpak,
            "Flatpak apps and the runtimes they share. Runtimes no installed app needs any more can be removed.",
            COMMAND, "Copy Command", "flatpak uninstall --unused"))
    snaps = _snap_old_versions()
    if snaps:
        found.append(Hidden(
            "snap", "Older Snap versions", snaps,
            "Snap keeps the previous version of each app after it updates, so it can go back. Removing them "
            "keeps the current versions.", COMMAND, "Copy Command",
            "snap list --all | awk '/disabled/{print $1, $3}' | "
            "while read name rev; do sudo snap remove \"$name\" --revision=\"$rev\"; done"))
    for swap in SWAP_FILES:
        try:
            size = swap.stat().st_size
        except OSError:
            continue
        found.append(Hidden(
            "swap", "Swap file", size,
            "Extra memory on disk. It's best left alone: without it, programs can be stopped when memory runs out."))
    return found


def measure(drive: Path) -> list[Hidden]:
    """What takes space on ``drive`` that a file browser doesn't show, biggest first."""
    items = _windows(drive) if IS_WINDOWS else _linux()
    items = [h for h in items if h.size >= MIN_SIZE or h.key == "restore_points"]
    return sorted(items, key=lambda h: -h.size)


def run_elevated(program: str, args: str) -> bool:
    """Run a Windows command as administrator (Windows asks first) and wait. True if it ran and succeeded."""
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(Info)]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    info = Info(cbSize=ctypes.sizeof(Info), fMask=0x40, lpVerb="runas", lpFile=program, lpParameters=args,
                nShow=0)
    if not shell.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return False
    try:
        k32.WaitForSingleObject(info.hProcess, 120_000)
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value == 0
    finally:
        k32.CloseHandle(info.hProcess)


def turn_off_hibernation() -> bool:
    return run_elevated("powercfg.exe", "/hibernate off")


__all__ = ["Hidden", "measure", "turn_off_hibernation", "run_elevated", "HIBERNATION", "RESTORE_POINTS",
           "VIRTUAL_DISKS", "COMMAND"]
