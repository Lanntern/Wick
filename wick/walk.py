"""Walking folders, the same way everywhere: symbolic links, Windows junctions and mount points are never
followed (no loops, no wandering onto another drive), a folder that can't be read is passed over, each folder is
listed once, and a cancel stops the walk between folders."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

REPARSE_POINT = 0x400       # Windows: junctions, mount points, symbolic links, cloud placeholders' folders


def is_link(e: os.DirEntry) -> bool:
    """A symbolic link, or a Windows junction or mount point: never followed, never removed through."""
    try:
        if e.is_symlink():
            return True
        if sys.platform != "win32":            # (junctions and mount-point folders are Windows'; no extra look elsewhere)
            return False
        if hasattr(e, "is_junction") and e.is_junction():
            return True
        return bool(getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & REPARSE_POINT)
    except OSError:
        return True


class Folder:
    """One folder of a walk: its path and everything in it."""
    __slots__ = ("path", "entries", "_skip")

    def __init__(self, path: Path, entries: list[os.DirEntry]):
        self.path = path
        self.entries = entries
        self._skip = False

    def skip(self) -> None:
        """Don't go into this folder's subfolders."""
        self._skip = True

    def files(self) -> Iterator[os.DirEntry]:
        """Its regular files (not links, folders or devices)."""
        for e in self.entries:
            try:
                if e.is_file(follow_symlinks=False):
                    yield e
            except OSError:
                continue


def folders(roots: Iterable[Path | str], *, cancel: threading.Event | None = None,
            enter: Callable[[os.DirEntry], bool] | None = None, unreadable: Callable[[Path], None] | None = None,
            ordered: bool = False, seen: set[str] | None = None) -> Iterator[Folder]:
    """Each folder under ``roots`` (the roots included), depth first. After a folder is handed out, its
    subfolders are gone into unless it was skipped (Folder.skip) or ``enter`` says no for one.
    ``unreadable`` hears about folders that couldn't be listed. ``ordered``: subfolders in name order (else as
    the system lists them). ``seen``: folder keys already walked (shared between walks to avoid repeats)."""
    seen = set() if seen is None else seen
    stack = [Path(r) for r in reversed(list(roots))]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        d = stack.pop()
        key = os.path.normcase(str(d))
        if key in seen:
            continue
        seen.add(key)
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            if unreadable is not None:
                unreadable(d)
            continue
        folder = Folder(d, entries)
        yield folder
        if folder._skip:
            continue
        subdirs = []
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False) or is_link(e):
                    continue
            except OSError:
                continue
            if enter is None or enter(e):
                subdirs.append(e)
        if ordered:
            subdirs.sort(key=lambda e: e.name, reverse=True)       # (popped from the end: name order)
        stack.extend(Path(e.path) for e in subdirs)


__all__ = ["Folder", "folders", "is_link", "REPARSE_POINT"]
