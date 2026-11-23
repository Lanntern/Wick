"""Proxy and sidecar files from action cameras and drones.

GoPro, DJI and Insta360 cameras save a small low-resolution copy of every video next to it, for previews in
their phone apps, and GoPro also a thumbnail picture. Once the videos are on a computer these copies are
dead weight, often several GB per trip. A file is only listed when the full video it belongs to is right next
to it, so nothing is listed that might be the only copy of a recording.

    GoPro:     GX010123.LRV and GX010123.THM beside GX010123.MP4 (also GOPR, GH, GP names)
    DJI:       DJI_0001.LRF beside DJI_0001.MP4 (or .MOV)
    Insta360:  LRV_20240101_120000_11_123.insv beside VID_20240101_120000_00_123.insv (or _10_)
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from .common import Candidate, ProgressFn, ScanResult, cancelled, walk_files

_VIDEO_EXTS = (".mp4", ".MP4", ".mov", ".MOV")
_INSTA = re.compile(r"^LRV_(\d{8}_\d{6})_1[01]_(\d+)$", re.I)
_DETAIL = {
    "lrv": "A low-resolution copy the camera made for its phone app. The full video is next to it.",
    "thm": "A thumbnail picture the camera made for its phone app. The full video is next to it.",
}


def full_video(path: Path, siblings: set[str]) -> str:
    """The name of the full video a proxy belongs to, when it's in ``siblings`` (the folder's file names, lower
    case); "" when the file isn't a proxy or its video isn't there."""
    ext = path.suffix.lower()
    stem = path.stem
    if ext in (".lrv", ".thm", ".lrf"):
        for e in _VIDEO_EXTS[::2]:
            if (stem + e).lower() in siblings:
                return stem + e
    if ext in (".insv", ".lrv", ".mp4"):
        m = _INSTA.match(stem)
        if m:
            for mid in ("00", "10"):
                name = f"VID_{m.group(1)}_{mid}_{m.group(2)}{path.suffix}"
                if name.lower() in siblings:
                    return name
    return ""


def find_proxies(roots: list[Path], progress: ProgressFn | None = None,
                 cancel: threading.Event | None = None) -> ScanResult:
    result = ScanResult("proxies")
    folders: dict[Path, list[tuple[Path, int]]] = {}
    for path, st in walk_files(roots, cancel=cancel):
        folders.setdefault(path.parent, []).append((path, st.st_size))
    done = 0
    for folder, files in folders.items():
        if cancelled(cancel):
            result.cancelled = True
            break
        names = {p.name.lower() for p, _size in files}
        for path, size in files:
            video = full_video(path, names)
            if not video:
                continue
            kind = "thm" if path.suffix.lower() == ".thm" else "lrv"
            result.items.append(Candidate(path, size, detail=_DETAIL[kind] + f" ({video})", group=str(folder)))
        done += 1
        if progress is not None:
            progress(f"Looking in {folder.name}", done, len(folders))
    return result


__all__ = ["find_proxies", "full_video"]
