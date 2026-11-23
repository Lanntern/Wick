"""macOS: the files to scan from Spotlight's index instead of asking the disk about every folder, the Mac's
counterpart of Everything on Windows. Used for big scans (your home folder, a whole drive), where it saves the
most; a smaller folder is read directly (quick anyway, and certain to include a file saved a moment ago).

Spotlight answers with paths; each folder on the way is added, so the scanner applies the same rules as a disk
walk (hidden and system folders, app libraries, program folders, iCloud-only files). When Spotlight isn't indexing
that volume, or doesn't answer, the scan reads the disk as usual.
"""

from __future__ import annotations

import os
import stat as stat_mod
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

UF_HIDDEN = 0x8000                 # stat st_flags: hidden in Finder (~/Library is)
ENABLED = True


def available(root: Path) -> bool:
    if not ENABLED or sys.platform != "darwin":
        return False
    try:
        out = subprocess.run(["/usr/bin/mdutil", "-s", str(root)], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "Indexing enabled" in out


def query(extensions: Iterable[str]) -> str:
    """Spotlight's query for files with these extensions (case-insensitive)."""
    return " || ".join(f'kMDItemFSName == "*{ext}"c' for ext in sorted(set(extensions)))


def paths(root: Path, extensions: Iterable[str], timeout: float = 300) -> list[str] | None:
    try:
        proc = subprocess.run(["/usr/bin/mdfind", "-0", "-onlyin", str(root), query(extensions)],
                              capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [os.fsdecode(p) for p in proc.stdout.split(b"\0") if p]


def items_for(root: Path, found: list[str]) -> list[tuple[str, bool, int, float, int]]:
    """(name, is folder, size, modified, attributes) for the files and every folder between them and ``root``,
    with the attributes the scanner reads (hidden; iCloud-only), as Everything gives them on Windows."""
    from .ntfs import CLOUD_ONLY, HIDDEN
    from .protected import dataless

    base = os.path.abspath(root)
    out: list[tuple[str, bool, int, float, int]] = []
    folders: set[str] = set()
    for p in found:
        if not p.startswith(base.rstrip("/") + "/"):
            continue
        try:
            st = os.stat(p, follow_symlinks=False)
        except OSError:
            continue
        if not stat_mod.S_ISREG(st.st_mode):
            continue
        attrs = (HIDDEN if getattr(st, "st_flags", 0) & UF_HIDDEN else 0) | (CLOUD_ONLY if dataless(st) else 0)
        out.append((p, False, st.st_size, st.st_mtime, attrs))
        d = os.path.dirname(p)
        while d != base and d.startswith(base) and d not in folders:
            folders.add(d)
            d = os.path.dirname(d)
    for d in folders:
        try:
            flags = getattr(os.stat(d, follow_symlinks=False), "st_flags", 0)
        except OSError:
            flags = 0
        out.append((d, True, 0, 0.0, HIDDEN if flags & UF_HIDDEN else 0))
    return out


def listing_for(root: Path, extensions: Iterable[str], progress: Callable | None = None):
    """The scanner's listing of ``root`` from Spotlight, or None to read the disk instead."""
    if not available(root):
        return None
    if progress:
        progress(f"Asking Spotlight about {root}…", 0, 0)
    found = paths(root, extensions)
    if not found:
        return None
    from .everything import to_listing

    return to_listing(Path(os.path.abspath(root)), items_for(root, found))


__all__ = ["available", "query", "paths", "items_for", "listing_for", "ENABLED", "UF_HIDDEN"]
