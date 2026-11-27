"""What Wick found out about this computer: which tools are installed, which encoders work, what the graphics card
can do, how much memory there is. Each finding is worked out once (some take an encoder run) and kept for the
session; ``forget()`` drops them all, so they're worked out again (after a test changes what's installed, or after
the person installs a driver and asks Wick to look again).

    @detected
    def encoder_works(encoder: str) -> bool: ...
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

_clears: list[Callable[[], None]] = []      # each finding's cache_clear
_lock = threading.Lock()


def detected(fn: Callable[P, R]) -> Callable[P, R]:
    """Keep ``fn``'s answers (per argument) until ``forget()``."""
    cached = functools.cache(fn)
    with _lock:
        _clears.append(cached.cache_clear)
    return cached          # type: ignore[return-value]  # (functools.cache's wrapper, typed as the function)


def forget() -> None:
    """Work everything out again the next time it's asked."""
    with _lock:
        for clear in _clears:
            clear()


__all__ = ["detected", "forget"]
