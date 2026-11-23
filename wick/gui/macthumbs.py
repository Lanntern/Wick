"""macOS: thumbnails from Quick Look, the Mac's counterpart of Windows' thumbnail cache.

Quick Look makes the previews Finder shows, in Apple's own sandboxed service, so nothing from the file is decoded
in Wick; it's quick for photos macOS decodes in hardware (HEIC) and for camera RAW files. ``qlmanage -t`` writes
a PNG of at most the size asked for.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

IS_MAC = sys.platform == "darwin"
PREFERRED = {"heic", "raw", "avif"}    # (kinds Quick Look makes faster than Wick's own decoders)


def thumbnail(path: Path, max_px: int) -> bytes | None:
    """PNG bytes of Quick Look's thumbnail, or None."""
    if not IS_MAC:
        return None
    with tempfile.TemporaryDirectory(prefix="wick-ql-") as out:
        try:
            subprocess.run(["/usr/bin/qlmanage", "-t", "-s", str(int(max_px)), "-o", out, str(path)],
                           capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        made = next(Path(out).glob("*.png"), None)
        if made is None:
            return None
        try:
            return made.read_bytes()
        except OSError:
            return None


__all__ = ["thumbnail", "IS_MAC", "PREFERRED"]
