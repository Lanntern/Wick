"""Photos saved twice by the camera: as RAW and as JPEG (or HEIC), same name, same folder.

Many cameras and phones can save both. Once the RAW files are developed (or never will be), one of the two is
redundant. Which one is your call: the RAW keeps everything the sensor saw; the JPEG is what you've been looking
at. So nothing is selected for you, and the choice of which to list is an option.
"""

from __future__ import annotations

import threading
from pathlib import Path

from ..formats import RAW_EXTS
from .common import Candidate, ProgressFn, ScanResult, cancelled, walk_files

DEVELOPED_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".hif"}


def find_pairs(roots: list[Path], keep: str = "raw", progress: ProgressFn | None = None,
               cancel: threading.Event | None = None) -> ScanResult:
    """List one file of each RAW + JPEG/HEIC pair: the JPEG (``keep="raw"``) or the RAW (``keep="jpeg"``)."""
    result = ScanResult("pairs")
    folders: dict[Path, dict[str, list[tuple[Path, int]]]] = {}
    for path, st in walk_files(roots, cancel=cancel):
        ext = path.suffix.lower()
        if ext in RAW_EXTS or ext in DEVELOPED_EXTS:
            folders.setdefault(path.parent, {}).setdefault(path.stem.lower(), []).append((path, st.st_size))
    done = 0
    for folder, stems in folders.items():
        if cancelled(cancel):
            result.cancelled = True
            break
        for files in stems.values():
            raws = [f for f in files if f[0].suffix.lower() in RAW_EXTS]
            developed = [f for f in files if f[0].suffix.lower() in DEVELOPED_EXTS]
            if len(raws) != 1 or len(developed) != 1:
                continue                              # (no pair, or more than one of a kind: left alone)
            (raw, _raw_size), (jpeg, jpeg_size) = raws[0], developed[0]
            if keep == "raw":
                result.items.append(Candidate(jpeg, jpeg_size, selected=False, group=str(folder),
                                              detail=f"The camera's JPEG of {raw.name}, which stays.",
                                              keeper=raw))
            else:
                result.items.append(Candidate(raw, _raw_size, selected=False, group=str(folder),
                                              detail=f"The RAW behind {jpeg.name}, which stays. Editing from the "
                                                     "RAW won't be possible afterwards.", keeper=jpeg))
        done += 1
        if progress is not None:
            progress(f"Looking in {folder.name}", done, len(folders))
    return result


__all__ = ["find_pairs", "DEVELOPED_EXTS"]
