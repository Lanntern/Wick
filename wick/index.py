"""A small persistent index of file details, so repeat scans don't re-read unchanged files.

Keyed by path; an entry is only used when size and modification time still match.
Stored in SQLite in the app data folder. Safe to delete at any time.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .formats import ImageInfo
from .probe import AudioInfo, StreamInfo, VideoInfo

SCHEMA = 4   # 3: videos carry their frame cadence; 4: Motion Photos, HDR, VFR, interlacing
_lock = threading.Lock()


class _State:
    """The open database, and whether the index is used at all (Settings › Remember file details)."""

    def __init__(self) -> None:
        self.conn: sqlite3.Connection | None = None
        self.enabled = True


_state = _State()
_stale: set[str] = set()      # entries found again whose "last seen" date is old (see refresh_seen)
_REFRESH_AFTER = 30 * 86400   # (rewriting it on every scan would cost a write per file)


def set_enabled(on: bool) -> None:
    _state.enabled = on


def is_enabled() -> bool:
    return _state.enabled


def path() -> Path:
    from .journal import data_dir

    return data_dir() / "index.sqlite"


def _db() -> sqlite3.Connection:
    try:
        return _open_db()
    except sqlite3.DatabaseError:
        _rebuild()                  # damaged: it's only a cache, so start it again
        return _open_db()


def _rebuild() -> None:
    """Close and remove a damaged index (and its write-ahead files); the next scan fills a new one."""
    if _state.conn is not None:
        try:
            _state.conn.close()
        except sqlite3.Error:
            pass
        _state.conn = None
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(str(path()) + suffix)
        except OSError:
            pass


def _corrupt(e: sqlite3.Error) -> bool:
    return isinstance(e, sqlite3.DatabaseError) and ("malformed" in str(e) or "not a database" in str(e))


def _open_db() -> sqlite3.Connection:
    if _state.conn is None:
        p = path()
        _state.conn = sqlite3.connect(str(p), check_same_thread=False, timeout=10)
        _state.conn.execute("PRAGMA journal_mode=WAL")
        _state.conn.execute("PRAGMA synchronous=NORMAL")   # (with WAL: still consistent after a crash; no flush per save)
        _state.conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        row = _state.conn.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
        if row is None or int(row[0]) != SCHEMA:
            _state.conn.execute("DROP TABLE IF EXISTS files")
            _state.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA),))
        _state.conn.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, "
                            "kind TEXT, info TEXT, seen REAL)")
        _state.conn.execute("CREATE TABLE IF NOT EXISTS extra (name TEXT, path TEXT, size INTEGER, mtime REAL, "
                            "value TEXT, PRIMARY KEY (name, path))")
        _state.conn.commit()
    return _state.conn


def _key(p: Path) -> str:
    return os.path.normcase(str(p))


def lookup(p: Path, size: int, mtime: float, kind: str):
    """ImageInfo / VideoInfo if known and unchanged, else None."""
    if not _state.enabled:
        return None
    try:
        with _lock:
            row = _db().execute("SELECT size, mtime, kind, info, seen FROM files WHERE path=?",
                                (_key(p),)).fetchone()
    except sqlite3.Error as e:
        if _corrupt(e):
            with _lock:
                _rebuild()
        return None
    if row is None or row[0] != size or abs(row[1] - mtime) > 1e-3 or row[2] != kind:
        return None
    if (row[4] or 0) < time.time() - _REFRESH_AFTER:
        with _lock:
            _stale.add(_key(p))
    try:
        data = json.loads(row[3])
    except ValueError:
        return None
    if data is None:
        return None
    try:
        if kind == "video":
            if "video_bit_rate" not in data:
                return None    # re-probe old records: picture rate is needed for the compression budget
            streams = [StreamInfo(**s) for s in data.pop("streams", [])]
            return VideoInfo(**data, streams=streams)
        if kind == "audio":
            return AudioInfo(**data)
        return ImageInfo(**data)
    except TypeError:  # stored by an older version with different fields
        return None


def store(items: list[tuple[Path, int, float, str, Any]]) -> None:
    """items: (path, size, mtime, kind, ImageInfo | VideoInfo | None)."""
    if not _state.enabled or not items:
        return
    now = time.time()
    rows = [(_key(p), size, mtime, kind, json.dumps(asdict(info)) if info is not None else "null", now)
            for p, size, mtime, kind, info in items]
    try:
        with _lock:
            db = _db()
            db.executemany("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)", rows)
            db.commit()
    except sqlite3.Error as e:
        if _corrupt(e):
            with _lock:
                _rebuild()


def refresh_seen() -> None:
    """Mark the entries used since the last call as seen now, so prune() keeps details of files that are still
    being scanned (only a date older than a month is rewritten)."""
    with _lock:
        keys = list(_stale)
        _stale.clear()
    if not _state.enabled or not keys:
        return
    now = time.time()
    try:
        with _lock:
            db = _db()
            db.executemany("UPDATE files SET seen=? WHERE path=?", [(now, k) for k in keys])
            db.commit()
    except sqlite3.Error as e:
        if _corrupt(e):
            with _lock:
                _rebuild()


def lookup_value(name: str, p: Path, size: int, mtime: float):
    """A cached computed value (e.g. a photo fingerprint) if the file hasn't changed."""
    if not _state.enabled:
        return None
    try:
        with _lock:
            row = _db().execute("SELECT size, mtime, value FROM extra WHERE name=? AND path=?",
                                (name, _key(p))).fetchone()
        if row is None or row[0] != size or abs(row[1] - mtime) > 1e-3:
            return None
        return json.loads(row[2])
    except (sqlite3.Error, ValueError):
        return None


def store_values(name: str, items: list[tuple[Path, int, float, object]]) -> None:
    if not _state.enabled or not items:
        return
    rows = [(name, _key(p), size, mtime, json.dumps(v)) for p, size, mtime, v in items]
    try:
        with _lock:
            db = _db()
            db.executemany("INSERT OR REPLACE INTO extra VALUES (?,?,?,?,?)", rows)
            db.commit()
    except sqlite3.Error as e:
        if _corrupt(e):
            with _lock:
                _rebuild()


def prune(max_age_days: float = 365) -> int:
    """Forget entries not refreshed for a long time (files that were moved or deleted). Returns how many."""
    try:
        with _lock:
            db = _db()
            n = db.execute("DELETE FROM files WHERE seen < ?", (time.time() - max_age_days * 86400,)).rowcount
            db.commit()
        return max(0, n)
    except sqlite3.Error:
        return 0


def clear() -> None:
    with _lock:
        if _state.conn is not None:
            _state.conn.close()
            _state.conn = None
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(str(path()) + suffix)
            except OSError:
                pass


def stats() -> tuple[int, int]:
    """(entries, bytes on disk)."""
    try:
        with _lock:
            n = _db().execute("SELECT COUNT(*) FROM files").fetchone()[0]
        size = sum(os.path.getsize(str(path()) + s) for s in ("", "-wal") if os.path.exists(str(path()) + s))
        return n, size
    except (sqlite3.Error, OSError):
        return 0, 0
