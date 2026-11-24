"""Shared types and helpers for the clean-up tools."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .. import walk
from ..protected import cloud_only, inside_library, is_hidden, is_library
from ..scanner import EXCLUDED_DIR_NAMES, PROGRAM_MARKER_EXTS

ProgressFn = Callable[[str, int, int], None]  # message, done, total
_scan_context = threading.local()


@contextmanager
def stream_results(callback):
    previous = getattr(_scan_context, "publish", None)
    _scan_context.publish = callback
    try:
        yield
    finally:
        _scan_context.publish = previous


def publish(tool: str, items: list) -> None:
    callback = getattr(_scan_context, "publish", None)
    if callback is not None and items:
        callback(tool, list(items))


def publisher():
    """Capture the current sink for a detector's worker pool (thread locals do not cross threads)."""
    return getattr(_scan_context, "publish", None) or (lambda tool, items: None)


class _LiveItems(list):
    def __init__(self, values=(), *, tool=""):
        super().__init__(values)
        self.tool = tool

    def append(self, item) -> None:
        super().append(item)
        publish(self.tool, [item])

    def extend(self, items) -> None:
        batch = list(items)
        super().extend(batch)
        publish(self.tool, batch)


@contextmanager
def shared_inventory():
    """Reuse folder listings for compatible scans in one batch. Never retain snapshots across scans."""
    previous = getattr(_scan_context, "inventory", None)
    _scan_context.inventory = {}
    try:
        yield
    finally:
        _scan_context.inventory = previous


@dataclass
class Candidate:
    """One thing a tool suggests acting on."""
    path: Path
    size: int                      # bytes freed if acted on
    title: str = ""                # what to show (defaults to the file name)
    detail: str = ""               # why it's listed / what happens
    selected: bool = True          # pre-selected? (only when verified safe)
    is_dir: bool = False
    keeper: Path | None = None     # duplicates: the copy that stays
    warning: str = ""              # shown prominently; such items are never pre-selected
    group: str = ""                # items with the same group are shown together
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.title:
            self.title = self.path.name or str(self.path)
        if self.warning:
            self.selected = False


@dataclass
class ScanResult:
    tool: str
    items: list[Candidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cancelled: bool = False
    rebuild: Callable[[], list[Candidate]] | None = None   # folder sizes: the rows again, after removing some

    def __post_init__(self) -> None:
        self.items = _LiveItems(self.items, tool=self.tool)
        publish(self.tool, self.items)

    @property
    def total(self) -> int:
        return sum(c.size for c in self.items)


def cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def walk_files(roots: list[Path], *, skip_program_folders: bool = True, include_hidden: bool = False,
               cancel: threading.Event | None = None, dirs_out: list[Path] | None = None,
               into_packages: bool = False) -> Iterator[tuple[Path, os.stat_result]]:
    """``into_packages``: also inside app bundles and libraries (protected.py); only for tools that must, such as
    compressing an app."""
    cache = getattr(_scan_context, "inventory", None)
    if cache is None:
        yield from _walk_files(roots, skip_program_folders=skip_program_folders, include_hidden=include_hidden,
                               cancel=cancel, dirs_out=dirs_out, into_packages=into_packages)
        return
    key = (tuple(os.path.normcase(os.path.abspath(p)) for p in roots), skip_program_folders, include_hidden,
           into_packages)
    if cache is not None and key in cache:
        cached_files, cached_directories = cache[key]
        if dirs_out is not None:
            dirs_out.extend(cached_directories)
        for item in cached_files:
            if cancelled(cancel):
                return
            yield item
        return
    files: list[tuple[Path, os.stat_result]] = []
    directories: list[Path] = []
    for item in _walk_files(roots, skip_program_folders=skip_program_folders, include_hidden=include_hidden,
                            cancel=cancel, dirs_out=directories, into_packages=into_packages):
        # A bounded cache avoids consuming arbitrary memory on a whole drive. Oversized scans fall back
        # to separate walks; normal streaming remains active and no incomplete inventory is reused.
        if cache is not None and len(files) <= 250_000:
            files.append(item)
        yield item
    if dirs_out is not None:
        dirs_out.extend(directories)
    if cache is not None and len(files) <= 250_000 and not cancelled(cancel):
        cache[key] = (files, directories)


def _walk_files(roots: list[Path], *, skip_program_folders: bool = True, include_hidden: bool = False,
               cancel: threading.Event | None = None, dirs_out: list[Path] | None = None,
               into_packages: bool = False) -> Iterator[tuple[Path, os.stat_result]]:
    """All regular files under ``roots`` (no symlinks, no system/program folders, no cloud-only files)."""

    def enter(c: os.DirEntry) -> bool:
        if c.name.lower() in EXCLUDED_DIR_NAMES or (not include_hidden and is_hidden(c)):
            return False
        return into_packages or not is_library(c.name)   # (deleting inside an app's library breaks it)

    seen: set[str] = set()
    for root in roots:
        root = Path(os.path.abspath(root))
        if not into_packages and inside_library(root) is not None:
            continue
        if root.is_file():
            try:
                yield root, root.stat()
            except OSError:
                pass
            continue
        for folder in walk.folders([root], cancel=cancel, enter=enter, seen=seen):
            if skip_program_folders and folder.path != root and any(
                    os.path.splitext(c.name)[1].lower() in PROGRAM_MARKER_EXTS for c in folder.entries):
                folder.skip()
                continue
            if dirs_out is not None:
                dirs_out.append(folder.path)
            for c in folder.files():
                if cancelled(cancel):
                    return
                try:
                    if not include_hidden and is_hidden(c):
                        continue
                    st = c.stat(follow_symlinks=False)
                    if cloud_only(st):
                        continue
                    yield Path(c.path), st
                except OSError:
                    continue


def folder_size(path: Path, cancel: threading.Event | None = None) -> tuple[int, int]:
    """(bytes, file count) of everything under ``path`` (links not followed)."""

    total = count = 0
    for folder in walk.folders([path], cancel=cancel):
        for e in folder.files():
            try:
                total += e.stat(follow_symlinks=False).st_size
                count += 1
            except OSError:
                continue
    return total, count


def newest_mtime(path: Path, skip_names: set[str] | frozenset[str] = frozenset(), limit: int = 20000) -> float:
    """Most recent modification time inside ``path`` (bounded walk; skips heavy dependency folders)."""

    newest = 0.0
    seen = 0
    for folder in walk.folders([path], enter=lambda e: e.name not in skip_names and not e.name.startswith(".")):
        for e in folder.entries:
            seen += 1
            try:
                newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
            except OSError:
                continue
        if seen >= limit:
            break
    return newest


def home() -> Path:
    return Path.home()


def available_drives() -> list[Path]:
    """Connected fixed/removable Windows volumes. Network shares can be added explicitly as folders."""
    if sys.platform != "win32":
        return []
    import ctypes

    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [Path(f"{chr(65 + i)}:/") for i in range(26) if mask & (1 << i) and
            ctypes.windll.kernel32.GetDriveTypeW(f"{chr(65 + i)}:\\") in (2, 3)]


def known_folder(name: str) -> Path | None:
    """User folders such as Downloads, Pictures, Videos, Documents (Windows or XDG)."""
    if sys.platform == "win32":
        guids = {"Downloads": "{374DE290-123F-4565-9164-39C4925E467B}"}
        if name in guids:
            try:
                import ctypes
                from ctypes import wintypes

                class GUID(ctypes.Structure):
                    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                                ("d", ctypes.c_ubyte * 8)]

                import uuid

                u = uuid.UUID(guids[name])
                g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
                ptr = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ptr)) == 0 \
                        and ptr.value:
                    path = Path(ptr.value)
                    ctypes.windll.ole32.CoTaskMemFree(ptr)
                    if path.is_dir():
                        return path
            except Exception:  # noqa: BLE001
                from wick.log import ignored
                ignored("cleanup.common.known_folder")
    p = home() / name
    return p if p.is_dir() else None

