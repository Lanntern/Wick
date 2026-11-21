"""Speed or size: the first-start question ("is a few percent smaller worth twice as long?") and what each answer
sets. The answer only fills in the defaults; JPEG XL effort and the processor speed for videos stay in the
compression options to change any time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .settings import Settings


@dataclass(frozen=True)
class Choice:
    key: str
    title: str
    about: str
    image_effort: int           # cjxl effort
    video_effort: str           # processor encodes: medium | slow


CHOICES = (
    Choice("faster", "Faster", "Quickest, with files a few percent bigger.", 5, "medium"),
    Choice("balanced", "Balanced", "A good fit for most computers.", 7, "medium"),
    Choice("smaller", "Smallest files",
           "About 5% smaller, but compressing takes about twice as long.", 9, "slow"),
)
BY_KEY = {c.key: c for c in CHOICES}


def apply(key: str, s: Settings) -> Settings:
    """``s`` with this answer's effort settings (everything else unchanged)."""
    c = BY_KEY.get(key, BY_KEY["balanced"])
    s.image_effort, s.video_effort = c.image_effort, c.video_effort
    s.validate()
    return s


def suggest(threads: int | None = None, memory_gb: float | None = None) -> tuple[str, str]:
    """(choice, why) for this computer: more cores make the slower settings cheap, few make them hurt."""
    if threads is None:
        threads = os.cpu_count() or 4
    if memory_gb is None:
        from . import tools

        memory_gb = tools.total_memory_gb() or 8
    if threads >= 12 and memory_gb >= 16:
        return "smaller", f"This computer has {threads} processor threads, so the slower settings won't feel slow."
    if threads <= 4 or memory_gb < 8:
        return "faster", f"This computer has {threads} processor threads, so faster settings keep it responsive."
    return "balanced", f"This computer has {threads} processor threads."


def choice_of(s: Settings) -> str:
    """Which answer these settings match ("" when they were changed by hand)."""
    for c in CHOICES:
        if s.image_effort == c.image_effort and s.video_effort == c.video_effort:
            return c.key
    return ""


__all__ = ["Choice", "CHOICES", "BY_KEY", "apply", "suggest", "choice_of"]
