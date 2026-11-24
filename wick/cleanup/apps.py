"""Installed apps (when each was last opened, how much space it takes), uninstalling one with its leftovers, and
folders that apps you've removed left behind.

Windows keeps the list of installed programs in its uninstall registry, and records which programs you start
(UserAssist, per account). Uninstalling runs the app's own uninstaller, the same one Settings › Apps uses; once
it's gone, folders it left behind go to the Recycle Bin, so History can bring them back.

Leftovers of apps removed earlier are only suggestions: a folder in AppData or ProgramData that no installed app,
running program or Start menu shortcut accounts for, and that hasn't changed for months. They're never selected
for you.
"""

from __future__ import annotations

import codecs
import os
import re
import shlex
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import plural
from .common import Candidate, ProgressFn, ScanResult, cancelled, folder_size

IS_WINDOWS = os.name == "nt"
UNUSED_DAYS = 180                 # "not opened in months"
LEFTOVER_QUIET_DAYS = 90          # a leftover folder hasn't changed for this long
_UNINSTALL_KEYS = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                   r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
_USERASSIST = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
# UserAssist writes well-known folders as GUIDs.
_KNOWN_FOLDERS = {
    "{6D809377-6AF0-444B-8957-A3773F02200E}": "ProgramW6432",       # Program Files
    "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}": "ProgramFiles(x86)",
    "{F38BF404-1D43-42F2-9305-67DE0B28FC23}": "SystemRoot",
    "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}": "SystemRoot",          # System32 (resolved below)
}
# Folders in AppData and ProgramData that are Windows' own, shared, or too generic to judge by name.
_NEVER_LEFTOVER = {
    "microsoft", "packages", "temp", "programs", "crashdumps", "connecteddevicesplatform", "comms", "d3dscache",
    "fontcache", "history", "inetcache", "virtualstore", "publisher", "windows", "microsoft_corporation",
    "package cache", "ssh", "regid.1991-06.com.microsoft", "usoshared", "usoprivate", "softwaredistribution",
    "desktop", "documents", "start menu", "templates", "application data", "nvidia", "nvidia corporation",
    "intel", "amd", "realtek", "dell", "hp", "lenovo", "asus", "packagemanagement", "oracle", "java", "python",
    "pip", "npm", "npm-cache", "nuget", "yarn", "pnpm", "jetbrains", "code", "mozilla", "google", "chocolatey",
    "scoop", "wick", "lanntern", "regid", "sun", "adobe", "apple", "apple computer", "common files", "tencent",
    "low", "locallow", "roaming", "local", "cache", "caches", "logs", "log", "data", "config", "settings", "tools",
}


@dataclass
class App:
    name: str
    publisher: str = ""
    version: str = ""
    location: Path | None = None
    size: int = 0                           # bytes (Windows' estimate, or measured)
    uninstall: str = ""                     # the command Settings › Apps runs
    key: str = ""                           # registry path, to tell when it's gone
    hive: str = ""                          # "HKLM" or "HKCU"
    last_used: float | None = None          # unix time, from UserAssist
    exes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------ reading what's there


def _norm(name: str) -> str:
    """A name without version numbers, architecture or punctuation, for matching folders to apps."""
    name = re.sub(r"\(.*?\)|\b(x64|x86|64-bit|32-bit|amd64|arm64)\b", " ", name.lower())
    name = re.sub(r"\bv?\d+(\.\d+)*\b", " ", name)
    return re.sub(r"[^a-z0-9]+", "", name)


def installed() -> list[App]:
    """Programs in the uninstall registry, as Settings › Apps lists them (no updates or system components)."""
    if not IS_WINDOWS:
        return []
    import winreg

    apps: dict[str, App] = {}
    for hive, hive_name in ((winreg.HKEY_LOCAL_MACHINE, "HKLM"), (winreg.HKEY_CURRENT_USER, "HKCU")):
        for base in _UNINSTALL_KEYS:
            try:
                root = winreg.OpenKey(hive, base)
            except OSError:
                continue
            with root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        sub_name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, sub_name) as sub:
                            values = {}
                            for j in range(winreg.QueryInfoKey(sub)[1]):
                                n, v, _t = winreg.EnumValue(sub, j)
                                values[n] = v
                    except OSError:
                        continue
                    name = str(values.get("DisplayName") or "").strip()
                    uninstall = str(values.get("UninstallString") or "").strip()
                    if not name or not uninstall or values.get("SystemComponent") == 1 \
                            or values.get("ParentKeyName") or values.get("ReleaseType") in ("Update", "Hotfix"):
                        continue
                    location = str(values.get("InstallLocation") or "").strip().strip('"')
                    size = values.get("EstimatedSize")
                    app = App(name=name, publisher=str(values.get("Publisher") or "").strip(),
                              version=str(values.get("DisplayVersion") or "").strip(),
                              location=Path(location) if location and Path(location).is_dir() else None,
                              size=int(size) * 1024 if isinstance(size, int) else 0, uninstall=uninstall,
                              key=f"{base}\\{sub_name}", hive=hive_name)
                    apps.setdefault(name.lower(), app)      # (the same app in both registry views: once)
    return sorted(apps.values(), key=lambda a: a.name.lower())


def _filetime(raw: bytes) -> float | None:
    """Last-run time from a UserAssist entry (Windows 7 and later: 72 bytes, FILETIME at offset 60)."""
    if len(raw) < 68:
        return None
    ft = struct.unpack_from("<Q", raw, 60)[0]
    if ft <= 0:
        return None
    return ft / 10_000_000 - 11_644_473_600


def last_run_times() -> dict[str, float]:
    """Program path (lower case) -> when this account last started it, from Windows' UserAssist records."""
    if not IS_WINDOWS:
        return {}
    import winreg

    out: dict[str, float] = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _USERASSIST)
    except OSError:
        return out
    with root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                count = winreg.OpenKey(root, winreg.EnumKey(root, i) + r"\Count")
            except OSError:
                continue
            with count:
                for j in range(winreg.QueryInfoKey(count)[1]):
                    try:
                        name, data, _t = winreg.EnumValue(count, j)
                    except OSError:
                        continue
                    if not isinstance(data, bytes):
                        continue
                    when = _filetime(data)
                    if when is None:
                        continue
                    path = codecs.decode(name, "rot13")
                    for guid, env in _KNOWN_FOLDERS.items():
                        if path.upper().startswith(guid):
                            base = os.environ.get(env, "")
                            if guid == "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}":
                                base = os.path.join(base, "System32")
                            path = base + path[len(guid):]
                            break
                    key = path.lower()
                    out[key] = max(when, out.get(key, 0.0))
    return out


def attach_last_used(apps: list[App], runs: dict[str, float]) -> None:
    """Each app's latest start: programs inside its folder, or a Start menu shortcut named like it."""
    for app in apps:
        best = None
        loc = str(app.location).lower().rstrip("\\/") + os.sep if app.location else None
        norm = _norm(app.name)
        for path, when in runs.items():
            hit = (loc is not None and path.startswith(loc)) or \
                  (path.endswith(".lnk") and norm and len(norm) >= 4 and norm in _norm(Path(path).stem))
            if hit and (best is None or when > best):
                best = when
        app.last_used = best


def _ago(when: float | None, now: float) -> str:
    if when is None:
        return "No record of being opened"
    days = int((now - when) // 86400)
    if days < 1:
        return "Opened today"
    if days < 60:
        return f"Last opened {plural(days, 'day')} ago"
    months = days // 30
    if months < 24:
        return f"Last opened {months} months ago"
    return f"Last opened {days // 365} years ago"


def find_apps(progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> ScanResult:
    """Installed apps, the ones not opened in months first. Nothing is selected for you."""
    result = ScanResult("apps")
    if not IS_WINDOWS:
        result.notes.append("Available on Windows.")
        return result
    if progress:
        progress("Reading installed apps", 0, 0)
    apps = installed()
    attach_last_used(apps, last_run_times())
    now = time.time()
    for i, app in enumerate(apps):
        if cancelled(cancel):
            result.cancelled = True
            break
        if not app.size and app.location is not None:
            app.size = folder_size(app.location, cancel)[0]
        if progress and i % 10 == 0:
            progress(f"Measuring {app.name}", i, len(apps))
        unused = app.last_used is not None and now - app.last_used > UNUSED_DAYS * 86400
        group = ("Not opened in 6 months" if unused else "Opened recently" if app.last_used is not None
                 else "No record of being opened")
        parts = [_ago(app.last_used, now)] + [p for p in (app.publisher, app.version) if p]
        result.items.append(Candidate(
            app.location or Path(app.name), app.size, title=app.name, detail=" · ".join(parts), selected=False,
            is_dir=True, group=group, extra={"app": app}))
    order = {"Not opened in 6 months": 0, "No record of being opened": 1, "Opened recently": 2}
    result.items.sort(key=lambda c: (order[c.group], -c.size))
    if not result.items:
        result.notes.append("No installed apps were found.")
    return result


# ------------------------------------------------------------------------------------------ uninstalling


def split_command(command: str) -> tuple[str, str]:
    """(program, arguments) from an UninstallString, which may be quoted or not."""
    command = command.strip()
    if command.startswith('"'):
        end = command.find('"', 1)
        if end > 0:
            return command[1:end], command[end + 1:].strip()
    low = command.lower()
    for ext in (".exe", ".cmd", ".bat"):
        i = low.find(ext)
        if i > 0:
            return command[:i + len(ext)], command[i + len(ext):].strip()
    parts = shlex.split(command, posix=False)
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def uninstall_command(app: App) -> tuple[str, str]:
    program, args = split_command(app.uninstall)
    if Path(program).name.lower() == "msiexec.exe":
        args = re.sub(r"(?i)/I\s*\{", "/X{", args)       # (some MSI entries say "install"; Settings › Apps removes)
    return program, args


def still_installed(app: App) -> bool:
    if not IS_WINDOWS:
        return False
    import winreg

    hive = winreg.HKEY_LOCAL_MACHINE if app.hive == "HKLM" else winreg.HKEY_CURRENT_USER
    try:
        winreg.CloseKey(winreg.OpenKey(hive, app.key))
        return True
    except OSError:
        return False


def _run_and_wait(program: str, args: str, cancel: threading.Event | None) -> None:
    """Start the uninstaller the way Explorer would (Windows asks for permission when it needs it) and wait for
    it to finish."""
    from .. import winapi

    try:
        winapi.run_and_wait(program, args, cancel=cancel)
    except OSError as e:
        raise OSError(e.errno, "The uninstaller didn't start"
                      + (" (permission was refused)" if e.errno == winapi.ERROR_CANCELLED else "")) from e


def uninstall(app: App, cancel: threading.Event | None = None, wait: float = 600.0) -> tuple[bool, str]:
    """Run the app's uninstaller; (removed, what happened). Many uninstallers hand over to a copy of themselves
    and exit at once, so this also waits (up to ``wait`` seconds) for the app to leave the uninstall list."""
    program, args = uninstall_command(app)
    if not program:
        return False, "It has no uninstaller"
    _run_and_wait(program, args, cancel)
    end = time.monotonic() + wait
    while still_installed(app):
        if cancelled(cancel) or time.monotonic() > end:
            return False, "The uninstaller closed without removing it"
        time.sleep(1.0)
    return True, "Uninstalled"


def _appdata_roots() -> list[Path]:
    roots = []
    for var in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        v = os.environ.get(var)
        if v:
            roots.append(Path(v))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local).parent / "LocalLow")
    return [r for r in roots if r.is_dir()]


def leftovers_of(app: App, others: list[App]) -> list[Path]:
    """Folders an uninstalled app left: its install folder, and folders in AppData and ProgramData named after
    it (or after its publisher, when that folder only held this app), unless another installed app shares the
    name."""
    found: list[Path] = []
    if app.location is not None and app.location.is_dir() and len(app.location.parts) > 2:
        found.append(app.location)
    target = _norm(app.name)
    taken = {_norm(o.name) for o in others} | {_norm(o.publisher) for o in others if o.publisher}
    if len(target) < 4 or target in taken:
        return found
    for root in _appdata_roots():
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False) or entry.name.lower() in _NEVER_LEFTOVER:
                continue
            path = Path(entry.path)
            if _norm(entry.name) == target:
                found.append(path)
            elif app.publisher and _norm(entry.name) == _norm(app.publisher) and _norm(app.publisher) not in taken:
                try:
                    siblings = [e for e in os.scandir(path) if e.is_dir()]
                except OSError:
                    continue
                inner = next((Path(e.path) for e in siblings if _norm(e.name) == target), None)
                if inner is not None:                # (the publisher's folder held only this app: all of it)
                    found.append(path if len(siblings) == 1 else inner)
    return list(dict.fromkeys(found))


# ------------------------------------------------------------------------------------------ leftovers of removed apps


def _newest_change(path: Path, limit: int = 2000) -> float:
    """The latest modified time in a folder (looking at up to ``limit`` entries: enough to tell a used folder)."""
    newest = path.stat().st_mtime
    stack, seen = [path], 0
    while stack and seen < limit:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for e in entries:
            seen += 1
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            newest = max(newest, st.st_mtime)
            if e.is_dir(follow_symlinks=False):
                stack.append(Path(e.path))
    return newest


def _running_folders() -> set[str]:
    """Folders of programs running now (lower case), from Windows' process list."""
    if not IS_WINDOWS:
        return set()
    import subprocess

    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "Get-Process | Where-Object Path | ForEach-Object { $_.Path }"],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {str(Path(line.strip()).parent).lower() for line in out.splitlines() if line.strip()}


def find_leftovers(progress: ProgressFn | None = None, cancel: threading.Event | None = None,
                   apps: list[App] | None = None) -> ScanResult:
    """Folders in AppData and ProgramData that no installed app accounts for and that haven't changed in months.
    Only suggestions: never selected for you."""
    result = ScanResult("leftovers")
    if not IS_WINDOWS:
        result.notes.append("Available on Windows.")
        return result
    apps = installed() if apps is None else apps
    names = {_norm(a.name) for a in apps} | {_norm(a.publisher) for a in apps if a.publisher}
    names = {n for n in names if len(n) >= 3}
    locations = {str(a.location).lower() for a in apps if a.location}
    running = _running_folders()
    now = time.time()
    roots = _appdata_roots()
    for r_i, root in enumerate(roots):
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for e_i, entry in enumerate(entries):
            if cancelled(cancel):
                result.cancelled = True
                return result
            if progress and e_i % 20 == 0:
                progress(f"Looking in {root.name}", r_i, len(roots))
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith(".") \
                    or entry.name.lower() in _NEVER_LEFTOVER or entry.name.startswith("{"):
                continue
            n = _norm(entry.name)
            if len(n) < 3 or any(n in other or other in n for other in names):
                continue
            path = Path(entry.path)
            low = str(path).lower()
            if any(loc.startswith(low) for loc in locations) or any(r.startswith(low) for r in running):
                continue
            try:
                quiet_days = (now - _newest_change(path)) / 86400
            except OSError:
                continue
            if quiet_days < LEFTOVER_QUIET_DAYS:
                continue
            size, count = folder_size(path, cancel)
            if size < 1024 * 1024:
                continue
            result.items.append(Candidate(
                path, size, title=entry.name, is_dir=True, selected=False, group=root.name,
                detail=f"Unchanged for {int(quiet_days // 30)} months · {count:,} files",
                warning="No installed app seems to use this, but Wick can't be sure. Check before removing it."))
    result.items.sort(key=lambda c: -c.size)
    if not result.items:
        result.notes.append("No leftovers of removed apps found.")
    return result


__all__ = ["App", "installed", "last_run_times", "attach_last_used", "find_apps", "uninstall", "leftovers_of",
           "find_leftovers", "split_command", "uninstall_command", "still_installed", "UNUSED_DAYS"]
