"""A smaller copy of a music library (for a phone or a car), kept in sync.

Lossless files (FLAC, WAV, ALAC...) become Opus, AAC or MP3; lossy files (MP3, AAC...) are copied as they
are, because encoding them again only loses quality. Tags and album art come along, and the folder layout
stays the same; cover.jpg/folder.jpg images are copied too. The library itself is never changed.

Running it again only does what changed: new or edited songs, a different format or quality. Copies whose
song was removed from the library are removed from the copy, but only files this app made (they're listed
in ``.wick-sync.json`` in the copy) are ever touched there.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import tools, walk
from .audio import FORMATS as AF
from .audio import build_args, cover_metadata, is_lossless, verify_audio
from .fileops import remove_quietly, temp_name
from .formats import AUDIO, classify
from .probe import probe_audio
from .settings import Settings

MANIFEST = ".wick-sync.json"
FORMATS = ("opus", "aac", "mp3")
QUALITIES = ("high", "balanced", "small")
_COVERS = {"cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png", "albumart.jpg"}


@dataclass
class SyncResult:
    encoded: int = 0
    copied: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)   # (song, why)
    library_bytes: int = 0
    copy_bytes: int = 0


def _ext(fmt: str) -> str:

    return AF[fmt][0]


def _load(dest: Path) -> dict:
    try:
        data = json.loads((dest / MANIFEST).read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(dest: Path, manifest: dict) -> None:
    tmp = dest / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=0, sort_keys=True), "utf-8")
    os.replace(tmp, dest / MANIFEST)


def _inside(a: Path, b: Path) -> bool:
    try:
        Path(os.path.abspath(a)).relative_to(os.path.abspath(b))
        return True
    except ValueError:
        return False


def sync(library: Path, dest: Path, fmt: str = "opus", quality: str = "balanced",
         progress: Callable[[str, int, int], None] | None = None, cancel: threading.Event | None = None,
         workers: int = 0) -> SyncResult:
    """Bring the copy in ``dest`` up to date with ``library`` (see the module notes)."""

    library, dest = Path(os.path.abspath(library)), Path(os.path.abspath(dest))
    if fmt not in FORMATS or quality not in QUALITIES:
        raise ValueError("Unknown format or quality")
    if _inside(dest, library) or _inside(library, dest):
        raise ValueError("The copy must be in a different folder than the library, not inside it")
    dest.mkdir(parents=True, exist_ok=True)
    old = _load(dest)
    entries: dict[str, dict] = old.get("files", {}) if old.get("format") == fmt and old.get("quality") == quality \
        else {}
    previous: dict[str, dict] = old.get("files", {})          # everything we made before, to tidy up
    res = SyncResult()
    songs, covers = _library_files(library, res)
    job = _Sync(dest, fmt, Settings(audio_quality=quality), entries, res, progress, cancel, len(songs) + len(covers))
    n = workers or max(1, min(8, (os.cpu_count() or 2)))
    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(lambda it: job.one(it, False), songs))
        list(pool.map(lambda it: job.one(it, True), covers))
    if cancel is not None and cancel.is_set():   # stopped: keep what's known so the next run continues
        for rel, e in entries.items():
            job.new_entries.setdefault(rel, e)
    else:
        _tidy(dest, previous, {e["out"] for e in job.new_entries.values()}, res)
    _save(dest, {"format": fmt, "quality": quality, "library": str(library), "files": job.new_entries})
    for e in job.new_entries.values():
        try:
            res.copy_bytes += (dest / e["out"]).stat().st_size
        except OSError:
            pass
    return res


_Item = tuple[str, Path, os.stat_result]      # (path inside the library, the file, its stat)


def _library_files(library: Path, res: SyncResult) -> tuple[list[_Item], list[_Item]]:
    """The library's songs and cover pictures (hidden folders left out)."""
    songs: list[_Item] = []
    covers: list[_Item] = []
    for folder in walk.folders([library], enter=lambda e: not e.name.startswith(".")):
        for entry in folder.entries:
            p = Path(entry.path)
            try:
                if not entry.is_file():       # (a link to a song counts, as before)
                    continue
                st = p.stat()
            except OSError:
                continue
            rel = p.relative_to(library).as_posix()
            if entry.name.lower() in _COVERS:
                covers.append((rel, p, st))
            elif classify(p) == AUDIO:
                songs.append((rel, p, st))
                res.library_bytes += st.st_size
    return songs, covers


class _Sync:
    """Songs and covers brought up to date, several at a time."""

    def __init__(self, dest: Path, fmt: str, s: Settings, entries: dict[str, dict], res: SyncResult,
                 progress: Callable[[str, int, int], None] | None, cancel: threading.Event | None, total: int):
        self.dest, self.fmt, self.s, self.entries, self.res = dest, fmt, s, entries, res
        self.progress, self.cancel, self.total = progress, cancel, total
        self.new_entries: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.done = 0

    def up_to_date(self, rel: str, st: os.stat_result) -> bool:
        e = self.entries.get(rel)
        return e is not None and e.get("size") == st.st_size and e.get("mtime_ns") == st.st_mtime_ns \
            and (self.dest / e["out"]).is_file()

    def tick(self, what: str) -> None:
        with self.lock:
            self.done += 1
            if self.progress:
                self.progress(what, self.done, self.total)

    def one(self, item: _Item, is_cover: bool) -> None:
        rel, src, st = item
        if self.cancel is not None and self.cancel.is_set():
            return
        if self.up_to_date(rel, st):
            with self.lock:
                self.new_entries[rel] = self.entries[rel]
                self.res.unchanged += 1
            self.tick(f"Up to date: {src.name}")
            return
        info = None if is_cover else probe_audio(src)
        if not is_cover and info is None:
            self.failed(rel, "can't be read", src)
            return
        encode = info is not None and is_lossless(info.codec)
        out_rel = str(Path(rel).with_suffix(_ext(self.fmt)).as_posix()) if encode else rel
        try:
            self.make(src, st, info, self.dest / out_rel)
        except Exception as e:  # noqa: BLE001 - one bad song doesn't stop the rest (its reason is shown)
            self.failed(rel, str(e)[:200], src)
            return
        with self.lock:
            self.new_entries[rel] = {"out": out_rel, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
            if encode:
                self.res.encoded += 1
            else:
                self.res.copied += 1
        self.tick(f"{'Encoded' if encode else 'Copied'} {src.name}")

    def make(self, src: Path, st: os.stat_result, info, out: Path) -> None:
        """Encode a lossless song (or copy anything else) to ``out``, with the original's modification time."""
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = temp_name(out.parent, out.suffix, "-sync")
        meta = None
        try:
            if info is not None and is_lossless(info.codec):
                meta = cover_metadata(src, info, out.parent) if self.fmt == "opus" else None
                tools.run(build_args(src, tmp, info, self.s, self.fmt, False, meta), timeout=3600)
                verify_audio(src, info, tmp, self.fmt, False, exact=False)
            else:
                shutil.copyfile(src, tmp)
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
            os.replace(tmp, out)
        except BaseException:
            remove_quietly(tmp)
            raise
        finally:
            remove_quietly(meta)

    def failed(self, rel: str, why: str, src: Path) -> None:
        with self.lock:
            self.res.failed.append((rel, why))
        self.tick(src.name)


def _tidy(dest: Path, previous: dict[str, dict], keep: set[str], res: SyncResult) -> None:
    """Copies we made whose song is gone (or that a new format replaced), and the folders they leave empty.
    Never anything else."""
    emptied: set[Path] = set()
    for e in previous.values():
        out_rel = e.get("out", "")
        target = dest / out_rel
        if out_rel and out_rel not in keep and _inside(target, dest) and target.is_file():
            try:
                target.unlink()
                res.removed += 1
                emptied.add(target.parent)
            except OSError:
                pass
    for folder in emptied:   # only folders a removed copy was in, up to the copy's top folder
        while folder != dest and _inside(folder, dest):
            try:
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent


__all__ = ["sync", "SyncResult", "FORMATS", "QUALITIES", "MANIFEST"]
