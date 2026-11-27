"""Exact duplicate files: size → partial fingerprint → full checksum."""

from __future__ import annotations

import hashlib
import os
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..formats import plural
from .common import Candidate, ProgressFn, ScanResult, cancelled, walk_files
from .similar import format_pair, live_companion

PARTIAL = 64 * 1024
HASH_READERS = 4               # files read and hashed at once on SSDs (one on hard drives: see drives.readers)


def _hasher():
    """BLAKE3 where it's installed (several times faster, still cryptographic: a file made to collide can't pose
    as a duplicate), else BLAKE2b. Both release Python's lock while hashing, so readers run in parallel."""
    try:
        from blake3 import blake3

        return blake3()
    except ImportError:
        return hashlib.blake2b(digest_size=32)


def _partial(path: Path, size: int) -> bytes | None:
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as f:
            h.update(f.read(PARTIAL))
            if size > PARTIAL * 2:
                f.seek(size - PARTIAL)
                h.update(f.read(PARTIAL))
        return h.digest()
    except OSError:
        return None


def _full(path: Path, cancel: threading.Event | None, on_bytes) -> bytes | None:
    try:
        h = _hasher()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                if cancelled(cancel):
                    return None
                h.update(chunk)
                on_bytes(len(chunk))
        return h.digest()
    except OSError:
        return None


def choose_keeper(paths: list[tuple[Path, os.stat_result]], rule: str, prefer: Path | None = None) -> int:
    """Index of the copy to keep."""
    def shortest(i):
        return (len(str(paths[i][0])), str(paths[i][0]).lower())

    idx = range(len(paths))
    if rule == "folder" and prefer is not None:
        pref = os.path.normcase(str(prefer)).rstrip("\\/") + os.sep
        inside = [i for i in idx if os.path.normcase(str(paths[i][0])).startswith(pref)]
        if inside:
            return min(inside, key=shortest)
        rule = "oldest"
    if rule == "newest":
        return max(idx, key=lambda i: (paths[i][1].st_mtime, -len(str(paths[i][0]))))
    if rule == "shortest_path":
        return min(idx, key=shortest)
    return min(idx, key=lambda i: (paths[i][1].st_mtime, len(str(paths[i][0]))))


def find_duplicates(roots: list[Path], *, min_size: int = 100 * 1024, keep: str = "oldest",
                    prefer: Path | None = None, skip_program_folders: bool = True,
                    progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> ScanResult:
    result = ScanResult("duplicates")
    by_size: dict[int, list[tuple[Path, os.stat_result]]] = defaultdict(list)
    n = 0
    for path, st in walk_files(roots, skip_program_folders=skip_program_folders, cancel=cancel):
        n += 1
        if progress and n % 500 == 0:
            progress(f"Looking at files… {n:,}", n, 0)
        if st.st_size >= max(1, min_size):
            by_size[st.st_size].append((path, st))
    if cancelled(cancel):
        result.cancelled = True
        return result
    from .. import drives

    same_size = _distinct_data([g for g in by_size.values() if len(g) > 1])
    workers = drives.readers([p for g in same_size for p, _ in g], HASH_READERS)
    # Then: the same start and end (a quick look), then the same checksum (files read in parallel on SSDs).
    same_ends = _split_by(same_size, lambda c: _partial(c[0], c[1].st_size), workers)
    done = 0
    total_bytes = sum(st.st_size for g in same_ends for _, st in g)
    lock = threading.Lock()

    def on_bytes(k: int) -> None:
        nonlocal done
        with lock:
            done += k
            now = done
        if progress:
            progress("Comparing contents…", now, max(1, total_bytes))

    groups = _split_by(same_ends, lambda c: _full(c[0], cancel, on_bytes), workers)
    if cancelled(cancel):
        result.cancelled = True
        return result
    groups.sort(key=lambda g: g[0][1].st_size * (len(g) - 1), reverse=True)
    for gi, g in enumerate(groups):
        k = choose_keeper(g, keep, prefer)
        keeper = g[k][0]
        result.items += [Candidate(path=p, size=st.st_size, keeper=keeper, group=f"dup{gi}",
                                   detail="Identical to the copy that stays",
                                   extra={"same_volume": st.st_dev == g[k][1].st_dev, "mtime_ns": st.st_mtime_ns})
                         for i, (p, st) in enumerate(g) if i != k]
    if groups:
        result.notes.append(f"{plural(len(groups), 'set')} of identical files")
    return result


_Files = list[tuple[Path, os.stat_result]]


def _distinct_data(groups: list[_Files]) -> list[_Files]:
    """Files of the same size, each piece of data once: with the real file ID and volume (Windows' directory
    listing reports 0 for both), names that are already hard links to the same data count once."""
    inodes: set[tuple[int, int]] = set()
    out = []
    for group in groups:
        unique = []
        for p, _st in group:
            try:
                real = os.stat(p)
            except OSError:
                continue
            key = (real.st_dev, real.st_ino)
            if real.st_ino and key in inodes:
                continue  # another name for data we already have: no extra space used
            inodes.add(key)
            unique.append((p, real))
        if len(unique) > 1:
            out.append(unique)
    return out


def _split_by(groups: list[_Files], digest: Callable, workers: int) -> list[_Files]:
    """Each group split by ``digest`` of its files (worked out in parallel); only splits of two or more stay.
    A file whose digest is None (unreadable, or stopped) is left out."""
    files = [c for g in groups for c in g]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        digests = dict(zip([p for p, _ in files], pool.map(digest, files), strict=True))
    out: list[_Files] = []
    for g in groups:
        sub: dict[bytes, _Files] = defaultdict(list)
        for p, st in g:
            d = digests.get(p)
            if d is not None:
                sub[d].append((p, st))
        out.extend(v for v in sub.values() if len(v) > 1)
    return out


def switch_keeper(items: list[Candidate], new_keeper: Path) -> list[Candidate]:
    """The same duplicate set with ``new_keeper`` as the copy that stays (the old keeper becomes removable)."""
    if not items or items[0].keeper is None:
        return items
    old = items[0].keeper
    try:
        kst = os.stat(new_keeper)
    except OSError:
        return items

    similar = str(items[0].group).startswith("sim")
    # Copies that differ (another encoding of the same song or video) carry a description of each copy.
    labels = {c.path: c.extra["label"] for c in items if "label" in c.extra}
    if labels:
        labels[old] = items[0].extra.get("keeper_label", "")
    out = []
    for c in items:
        path = old if c.path == new_keeper else c.path
        size = c.size
        try:
            st = os.stat(path)
            same, mtime = st.st_dev == kst.st_dev, st.st_mtime_ns
            if similar or labels:
                size = st.st_size          # similar photos and other encodings differ in size
        except OSError:
            same, mtime = c.extra.get("same_volume", True), c.extra.get("mtime_ns", 0)
        checked = True if path == old else c.extra.get("checked", c.selected)
        extra = {"same_volume": same, "mtime_ns": mtime, "checked": checked}
        detail = c.detail
        if labels:
            label, keeper_label = labels.get(path, ""), labels.get(new_keeper, "")
            extra.update(label=label, keeper_label=keeper_label)
            detail = (f"{label}, like the copy that stays" if label == keeper_label else
                      f"{label}; the copy that stays is {keeper_label}")
        elif similar:
            detail = "Similar to the copy that stays"
            if format_pair(path, new_keeper):
                detail = f"The same photo saved as {path.suffix[1:].upper()} and {new_keeper.suffix[1:].upper()}"
            mov = live_companion(path, new_keeper)
            if mov is not None:
                try:
                    size += mov.stat().st_size
                    extra["companions"] = [str(mov)]
                    detail += f". Live Photo: its video ({mov.name}) goes with it"
                except OSError:
                    pass
        out.append(Candidate(path=path, size=size, keeper=new_keeper, group=c.group, detail=detail,
                             selected=c.selected, extra=extra))
    return out
