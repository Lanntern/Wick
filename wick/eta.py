"""Time left for a whole job, from how fast this job has actually gone.

The rate is bytes done per second since the job started (photos, videos and documents all count by size, and the
file that's being worked on counts by how far its encoder says it is). The first seconds say nothing reliable, so
there's no estimate until the job has run a little and done a little; after that the estimate is smoothed so it
doesn't jump with every file, and it's rounded the way people say it ("about 4 min").
"""

from __future__ import annotations

WARMUP_SECONDS = 8.0       # no estimate before this...
WARMUP_FRACTION = 0.02     # ...or before this much of the job is done
SMOOTHING = 0.25           # weight of the newest estimate


class TimeLeft:
    def __init__(self) -> None:
        self.smoothed: float | None = None

    def update(self, done_bytes: float, total_bytes: float, elapsed: float) -> float | None:
        """Seconds left, or None while it can't be told yet."""
        if total_bytes <= 0 or elapsed < WARMUP_SECONDS or done_bytes < total_bytes * WARMUP_FRACTION:
            return None
        if done_bytes >= total_bytes:
            return 0.0
        raw = elapsed * (total_bytes - done_bytes) / done_bytes
        self.smoothed = raw if self.smoothed is None else SMOOTHING * raw + (1 - SMOOTHING) * self.smoothed
        # Never more than a third off the plain estimate (a long video finishing changes the picture at once).
        self.smoothed = min(max(self.smoothed, raw * 0.67), raw * 1.5)
        return self.smoothed


def say(seconds: float | None) -> str:
    """"about 4 min left", "about 1 h 20 min left", "less than a minute left"; "" when unknown."""
    if seconds is None:
        return ""
    if seconds < 50:
        return "less than a minute left"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"about {minutes} min left"
    hours, minutes = divmod(round(seconds / 300) * 5, 60)     # (to five minutes: no false precision)
    return f"about {hours} h {minutes} min left" if minutes else f"about {hours} h left"


__all__ = ["TimeLeft", "say"]
