"""Estimates that learn: compare each estimated size with the real result and correct future estimates.

Kept per kind of conversion (e.g. "jpeg:jxl_transcode:lossless", "video:video:h264>hevc") as a running
average of log(actual / estimated), so a route that keeps coming out smaller (or bigger) than predicted
gets its estimates scaled accordingly. Stored in calibration.json in the app data folder.
"""

from __future__ import annotations

import json
import math
import threading

MIN_SAMPLES = 3          # don't trust a correction until a few files agree
MAX_SAMPLES = 200        # older results fade out (running average over roughly this many)
_CLAMP = (0.4, 2.5)      # never scale an estimate by more than this

class Calibration:
    """The corrections learned so far, read from calibration.json the first time they're needed."""

    def __init__(self, path=None):
        self._path = path               # (None: calibration.json in the app data folder)
        self._lock = threading.Lock()
        self._data: dict[str, list[float]] | None = None   # key -> [mean log ratio, count]
        self._dirty = False
        self.enabled = True

    def _file(self):
        if self._path is not None:
            return self._path
        from .journal import data_dir

        return data_dir() / "calibration.json"

    def _load(self) -> dict[str, list[float]]:
        if self._data is None:
            try:
                raw = json.loads(self._file().read_text("utf-8"))
                self._data = {str(k): [float(v[0]), float(v[1])] for k, v in raw.items()}
            except (OSError, ValueError, TypeError, IndexError):
                self._data = {}
        return self._data

    def factor(self, entry, plan) -> float:
        """How much to scale this plan's raw estimate (1.0 until enough results are known)."""
        if not self.enabled:
            return 1.0
        with self._lock:
            mean, n = self._load().get(key(entry, plan), (0.0, 0))
        if n < MIN_SAMPLES:
            return 1.0
        return max(_CLAMP[0], min(_CLAMP[1], math.exp(mean)))

    def record(self, entry, plan, actual_size: int, raw_estimate: int) -> None:
        """Remember how a real result compared with the uncorrected estimate."""
        if not self.enabled or actual_size <= 0 or raw_estimate <= 0:
            return
        r = math.log(actual_size / raw_estimate)
        with self._lock:
            d = self._load()
            mean, n = d.get(key(entry, plan), (0.0, 0))
            n = min(n + 1, MAX_SAMPLES)
            d[key(entry, plan)] = [mean + (r - mean) / n, n]
            self._dirty = True

    def save(self) -> None:
        with self._lock:
            if not self._dirty or self._data is None:
                return
            try:
                from .fileops import atomic_write_text

                atomic_write_text(self._file(), json.dumps(self._data, indent=1))
                self._dirty = False
            except OSError:
                pass

    def reload(self) -> None:
        """Read the file again the next time (what's not saved is dropped)."""
        with self._lock:
            self._data, self._dirty = None, False

    def reset(self) -> None:
        """Forget everything learned."""
        with self._lock:
            self._data, self._dirty = {}, False
            try:
                self._file().unlink()
            except OSError:
                pass


def key(entry, plan) -> str:
    if entry.kind == "video" and entry.video is not None:
        return f"video:{entry.video.codec}>{plan.target_ext}"
    return f"{entry.kind}:{plan.route}:{'lossless' if plan.lossless else 'lossy'}"


_store = Calibration()      # the app's own (module functions below)


def set_enabled(on: bool) -> None:
    _store.enabled = on


def factor(entry, plan) -> float:
    return _store.factor(entry, plan)


def record(entry, plan, actual_size: int, raw_estimate: int) -> None:
    _store.record(entry, plan, actual_size, raw_estimate)


def save() -> None:
    _store.save()


def reload() -> None:
    _store.reload()


def reset() -> None:
    _store.reset()


__all__ = ["Calibration", "factor", "record", "save", "reload", "reset", "key", "set_enabled"]
