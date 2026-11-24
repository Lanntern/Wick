"""What a Mac collects over time, in your own account: Xcode's build data and old device support, caches, Mail's
copies of opened attachments, iPhone and iPad update files, old installers in Downloads, and data left behind by
apps that are gone. Everything here goes to the Trash. Only what's rebuilt or downloaded again by itself is
selected for you.

Time Machine's local snapshots are listed as a note: macOS removes them by itself when space is needed, and
deleting them early needs an administrator, so Wick explains instead.
"""

from __future__ import annotations

import plistlib
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..formats import plural
from .common import Candidate, ProgressFn, ScanResult, cancelled, folder_size

INSTALLER_EXTS = {".dmg", ".pkg", ".mpkg"}
INSTALLER_DAYS = 30
LEFTOVER_MIN = 10 * 1024 ** 2


def supported() -> bool:
    return sys.platform == "darwin"


def _lib(*parts: str) -> Path:
    return Path.home().joinpath("Library", *parts)


def _installed_apps() -> tuple[set[str], set[str]]:
    """(bundle identifiers, lower-case names) of the apps on this Mac."""
    ids: set[str] = set()
    names: set[str] = set()
    for base in (Path("/Applications"), Path("/System/Applications"), Path.home() / "Applications",
                 Path("/Applications/Utilities")):
        try:
            apps = [p for p in base.iterdir() if p.suffix == ".app"]
        except OSError:
            continue
        for app in apps:
            names.add(app.stem.lower())
            try:
                with open(app / "Contents" / "Info.plist", "rb") as f:
                    info = plistlib.load(f)
                if info.get("CFBundleIdentifier"):
                    ids.add(str(info["CFBundleIdentifier"]).lower())
                if info.get("CFBundleName"):
                    names.add(str(info["CFBundleName"]).lower())
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue
    return ids, names


def is_leftover(name: str, ids: set[str], names: set[str]) -> bool:
    """Whether a folder named after an app (by bundle identifier or name) belongs to no installed app. Apple's own
    and anything shared by several apps (a vendor's folder) are never counted."""
    low = name.lower()
    if low.startswith(("com.apple.", "apple", "group.")) or low in ("caches", "crashreporter", "addressbook"):
        return False
    if low in ids or low in names:
        return False
    if "." in low and re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+){2,}", low):      # a bundle identifier
        return not any(i.startswith(low) or low.startswith(i) for i in ids)
    # A name: left over only if no installed app's name or identifier mentions it (e.g. "Google" covers Chrome).
    return not any(low in n or low in i for n in names for i in ids)


def find(progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> ScanResult:
    result = ScanResult("macclean")
    now = time.time()

    def add(path: Path, title: str, detail: str, group: str, selected: bool, warning: str = "") -> None:
        if not path.exists() or cancelled(cancel):
            return
        if progress is not None:
            progress(f"Measuring {title}", 0, 0)
        size = folder_size(path, cancel)[0] if path.is_dir() else path.stat().st_size
        if size > 0:
            result.items.append(Candidate(path, size, title=title, detail=detail, selected=selected,
                                          is_dir=path.is_dir(), group=group, warning=warning,
                                          extra={"location": str(path)}))

    dev = _lib("Developer")
    add(dev / "Xcode" / "DerivedData", "Xcode build data (DerivedData)",
        "Xcode rebuilds it the next time you build a project.", "Xcode", True)
    for platform in ("iOS", "watchOS", "tvOS", "visionOS"):
        support = dev / "Xcode" / f"{platform} DeviceSupport"
        try:
            versions = sorted((p for p in support.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
        except OSError:
            continue
        for old in versions[:-1]:            # (the newest is the device you use now)
            add(old, f"{platform} device support {old.name}",
                "Debugging data for a device version you connected once; Xcode copies it again if needed.",
                "Xcode", True)
    add(dev / "CoreSimulator" / "Caches", "Simulator caches", "Rebuilt when simulators run.", "Xcode", True)
    add(dev / "Xcode" / "Archives", "Xcode archives",
        "Builds you archived for release. Keep the ones you may need to read crash reports for.", "Xcode", False,
        warning="Needed to read crash reports from released builds")
    add(_lib("Caches"), "Apps' caches (~/Library/Caches)",
        "Apps rebuild these as needed; some open more slowly the first time after.", "Caches and downloads", False)
    add(_lib("Containers", "com.apple.mail", "Data", "Library", "Mail Downloads"), "Mail's opened attachments",
        "Copies Mail made when you opened attachments; the messages still have them.", "Caches and downloads", True)
    for device in ("iPhone", "iPad", "iPod"):
        add(_lib("iTunes", f"{device} Software Updates"), f"{device} update files",
            "Downloaded again if a device needs restoring.", "Caches and downloads", True)
    downloads = Path.home() / "Downloads"
    try:
        for p in downloads.iterdir():
            if p.suffix.lower() in INSTALLER_EXTS and now - p.stat().st_mtime > INSTALLER_DAYS * 86400:
                add(p, p.name, f"An installer downloaded {int((now - p.stat().st_mtime) // 86400)} days ago.",
                    "Old installers", False)
    except OSError:
        pass
    ids, names = _installed_apps()
    for base, what in ((_lib("Containers"), "data"), (_lib("Application Support"), "support files")):
        try:
            folders = [p for p in base.iterdir() if p.is_dir() and not p.is_symlink()]
        except OSError:
            continue
        for folder in folders:
            if cancelled(cancel):
                result.cancelled = True
                return result
            if is_leftover(folder.name, ids, names):
                size = folder_size(folder, cancel)[0]
                if size >= LEFTOVER_MIN:
                    result.items.append(Candidate(
                        folder, size, title=f"{folder.name} ({what})", is_dir=True, group="App leftovers",
                        detail="No installed app matches this name.",
                        warning="Check it belongs to an app you removed before deleting",
                        extra={"location": str(folder)}))
    note = snapshots_note()
    if note:
        result.notes.append(note)
    return result


def snapshots_note() -> str:
    try:
        out = subprocess.run(["/usr/bin/tmutil", "listlocalsnapshots", "/"], capture_output=True, text=True,
                             timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    count = sum(1 for line in out.splitlines() if "com.apple.TimeMachine" in line)
    if not count:
        return ""
    return (f"Time Machine keeps {plural(count, 'local snapshot')} on this Mac. macOS deletes them by "
            "itself when space is needed, so the free space shown can be lower than what's really available. To "
            "remove them now: tmutil deletelocalsnapshots / in Terminal.")


__all__ = ["find", "supported", "is_leftover", "snapshots_note"]
