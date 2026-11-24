"""Folders Wick never looks inside, and files it never reads, on any system.

App libraries: macOS apps keep their data in packages (folders that Finder shows as one file) with their own
database of what's inside. Changing or deleting a file in one behind the app's back can break the library:
Photos, iPhoto and Aperture, Music and TV, iMovie, Final Cut, Logic and GarageBand, Lightroom and Capture One. App
bundles and disk images are left alone too. To shrink what's in a library, export from the app.

iCloud-only files: with "Optimize Mac Storage" a file can be in iCloud Drive with nothing on the Mac (macOS marks
it dataless). Reading it would download it, so it's skipped like OneDrive's online-only files on Windows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

LIBRARY_SUFFIXES = (
    ".photoslibrary", ".photolibrary", ".aplibrary",           # Photos, iPhoto, Aperture
    ".musiclibrary", ".tvlibrary", ".itlp",                    # Music, TV
    ".imovielibrary", ".theater", ".fcpbundle", ".fcpxbundle", # iMovie, Final Cut
    ".logicx", ".band",                                        # Logic, GarageBand
    ".lrdata", ".lrlibrary", ".lrcat-data",                    # Lightroom
    ".cocatalog", ".cosessiondb",                              # Capture One
    ".app", ".framework", ".bundle", ".plugin", ".xcarchive",  # programs
    ".sparsebundle", ".sparseimage", ".backupbundle",          # disk images, backups
)
LIBRARY_NAMES = {"lightroom catalog previews.lrdata", "itunes", "music library", "tv library"}

SF_DATALESS = 0x40000000          # macOS: the file's contents are in the cloud only


def is_library(name: str) -> bool:
    """Whether a folder with this name is an app's library or package (never looked inside)."""
    low = name.lower()
    return low.endswith(LIBRARY_SUFFIXES) or low in LIBRARY_NAMES


def inside_library(path: Path) -> Path | None:
    """The library ``path`` is in (or is), if any."""
    for part in (path, *path.parents):
        if is_library(part.name):
            return part
    return None


def dataless(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


# Windows: OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS. Set on cloud-only OneDrive/Dropbox placeholders.
CLOUD_ONLY_ATTRS = 0x1000 | 0x40000 | 0x400000


def cloud_only(st: os.stat_result) -> bool:
    """Only in the cloud (OneDrive's online-only files on Windows, iCloud Drive's dataless ones on a Mac): reading
    it would download it."""
    return bool(getattr(st, "st_file_attributes", 0) & CLOUD_ONLY_ATTRS) or dataless(st)


def is_hidden(entry: os.DirEntry) -> bool:
    """Hidden the way the system shows it: a dot name; Finder's hidden flag on a Mac (~/Library, where apps keep
    their data, has it); Windows' hidden or system attribute."""
    if entry.name.startswith("."):
        return True
    if sys.platform == "darwin":
        try:
            return bool(getattr(entry.stat(follow_symlinks=False), "st_flags", 0) & 0x8000)
        except OSError:
            return False
    if sys.platform == "win32":
        try:
            attrs = entry.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
            return bool(attrs & 0x2 or attrs & 0x4)  # HIDDEN | SYSTEM
        except (OSError, AttributeError):
            return False
    return False


def describe(library: Path) -> str:
    """Why a library was skipped, for people."""
    low = library.name.lower()
    app = next((a for suffixes, a in (
        ((".photoslibrary", ".photolibrary", ".aplibrary"), "Photos"), ((".musiclibrary", ".tvlibrary"), "Music"),
        ((".imovielibrary", ".theater"), "iMovie"), ((".fcpbundle", ".fcpxbundle"), "Final Cut Pro"),
        ((".logicx", ".band"), "Logic or GarageBand"), ((".lrdata", ".lrlibrary", ".lrcat-data"), "Lightroom"),
        ((".cocatalog", ".cosessiondb"), "Capture One"), ((".app",), "an app")) if low.endswith(suffixes)), None)
    return (f"{library.name}: {app}'s library. Files inside are managed by {app}; export from it to shrink them"
            if app and app != "an app" else f"{library.name}: an app or package, left as it is")


__all__ = ["is_library", "inside_library", "dataless", "describe", "LIBRARY_SUFFIXES", "SF_DATALESS"]
