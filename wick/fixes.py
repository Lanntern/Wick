"""What to do about a failed file: the common reasons, each with the one action that usually fixes it.

Matched on the message the engine reports (see engine._why and the tools' errors), so it stays in step with what
people read in the list. Each fix is (key, button label, explanation); the page decides what a key does.
"""

from __future__ import annotations

FIXES = (
    # key, words in the message (any), button, what to tell people
    ("protected", ("controlled folder access",),
     "Open Windows Security", "Windows' Controlled folder access blocked the change. Allow Wick there, then retry."),
    ("in_use", ("being used by another process", "in use", "sharing violation", "is locked"),
     "Retry Failed", "Close the app that has the file open, then retry."),
    ("space", ("no space left", "not enough space", "disk full", "errno 28", "winerror 112"),
     "Free Up Space", "The drive is full. Clean Up can free space, then retry."),
    ("denied", ("permission denied", "access is denied", "read-only", "errno 13", "winerror 5"),
     "Retry Failed", "Wick wasn't allowed to change the file (read-only, or a folder that needs administrator "
                     "rights). Change its permissions, or save the results to another folder, then retry."),
    ("tool", ("was not found. put it in the 'tools' folder", "isn't installed", "not installed", "encoder is missing"),
     "Open Encoders", "An encoder Wick needs is missing. Settings › Encoders shows which."),
    ("changed", ("changed since the scan", "no longer exists"),
     "Scan Again", "The file changed or moved while Wick worked. Scan again to pick up the current version."),
)


def suggest(messages: list[str]) -> list[tuple[str, str, str, int]]:
    """(key, button, explanation, how many files) for the reasons found in ``messages``, most common first;
    a message matches the first reason it fits."""
    counts: dict[str, int] = {}
    for m in messages:
        low = m.lower()
        for key, words, _label, _why in FIXES:
            if any(w in low for w in words):
                counts[key] = counts.get(key, 0) + 1
                break
    out = [(key, label, why, counts[key]) for key, _w, label, why in FIXES if key in counts]
    return sorted(out, key=lambda f: -f[3])


__all__ = ["suggest", "FIXES"]
