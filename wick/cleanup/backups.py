"""Phone backups on this computer: iPhone and iPad backups (iTunes and Apple Devices) and Samsung Smart Switch.

Each backup is often tens of gigabytes, and an old phone's backup tends to stay long after the phone is gone.
They're listed with the device's name and when the backup was made, newest first, and never selected for you:
a backup may be the only copy of what was on a phone.
"""

from __future__ import annotations

import os
import plistlib
import sys
import threading
import time
from pathlib import Path

from .common import Candidate, ProgressFn, ScanResult, cancelled, folder_size


def apple_backup_folders() -> list[Path]:
    """Where iTunes (desktop or Microsoft Store) and Apple Devices keep iPhone and iPad backups."""
    out = []
    if sys.platform == "darwin":                  # Finder (and iTunes before Catalina)
        p = Path.home() / "Library" / "Application Support" / "MobileSync" / "Backup"
        if p.is_dir():
            out.append(p)
    for base in (os.environ.get("APPDATA", ""), os.environ.get("USERPROFILE", "")):
        if not base:
            continue
        for sub in (("Apple Computer", "MobileSync", "Backup"), ("Apple", "MobileSync", "Backup")):
            p = Path(base, *sub)
            if p.is_dir():
                out.append(p)
    return list(dict.fromkeys(out))


def samsung_backup_folders() -> list[Path]:
    home = os.environ.get("USERPROFILE") or str(Path.home())
    found = [Path(home, "Documents", "Samsung", "SmartSwitch", "backup")]
    if sys.platform == "darwin":
        found.append(Path.home() / "Documents" / "Samsung" / "SmartSwitch" / "Backup")
    return [p for p in dict.fromkeys(found) if p.is_dir()]


def describe_apple(folder: Path) -> tuple[str, float]:
    """(device name and model, when the backup was last made) from the backup's Info.plist."""
    try:
        with open(folder / "Info.plist", "rb") as f:
            info = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return "", 0.0
    name = str(info.get("Device Name") or info.get("Display Name") or "")
    model = str(info.get("Product Name") or info.get("Product Type") or "")
    when = info.get("Last Backup Date")
    stamp = when.timestamp() if hasattr(when, "timestamp") else 0.0
    return (f"{name} ({model})" if name and model else name or model), stamp


def find_backups(progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> ScanResult:
    result = ScanResult("backups")
    found: list[tuple[Path, str, float, str]] = []
    for base in apple_backup_folders():
        for folder in base.iterdir():
            if folder.is_dir() and (folder / "Info.plist").is_file():
                name, when = describe_apple(folder)
                found.append((folder, name or "iPhone or iPad", when or folder.stat().st_mtime, "apple"))
    for base in samsung_backup_folders():
        for folder in base.iterdir():
            if folder.is_dir():
                found.append((folder, f"Samsung {folder.name}", folder.stat().st_mtime, "samsung"))
    for i, (folder, name, when, kind) in enumerate(sorted(found, key=lambda f: -f[2])):
        if cancelled(cancel):
            result.cancelled = True
            break
        if progress is not None:
            progress(f"Measuring {name}", i, len(found))
        size = folder_size(folder, cancel)[0]
        age = (time.time() - when) / 86400
        date = time.strftime("%d %B %Y", time.localtime(when))
        result.items.append(Candidate(
            folder, size, title=f"{name} backup", is_dir=True, selected=False,
            detail=f"Made {date}" + (f", {int(age // 30)} months ago" if age > 60 else "") + ". "
                   + (("Finder can restore a phone from it." if sys.platform == "darwin" else
                       "iTunes or Apple Devices can restore a phone from it.")
                      if kind == "apple" else "Smart Switch can restore a phone from it."),
            warning="Remove it only if the phone is gone or backed up somewhere else",
            extra={"location": str(folder)}))
    if not found:
        result.notes.append("No iPhone, iPad or Samsung backups on this computer.")
    return result


__all__ = ["find_backups", "apple_backup_folders", "describe_apple"]
