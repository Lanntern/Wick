"""Point the Homebrew cask (Casks/wick.rb) at a published release: its version and the disk image's checksum.

    python scripts/update_cask.py 0.2.0 SHA256SUMS.txt

The checksum comes from the release's SHA256SUMS.txt. A release without a Mac disk image leaves the cask as it is.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CASK = Path(__file__).resolve().parent.parent / "Casks" / "wick.rb"
DMG = "Wick-macos-arm64.dmg"


def dmg_hash(sums: str) -> str | None:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == DMG and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def update(version: str, sums: str, path: Path = CASK) -> bool:
    version = version.removeprefix("v")
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        raise SystemExit(f"Not a version number: {version!r}")
    digest = dmg_hash(sums)
    if digest is None:
        print(f"{DMG} isn't in this release: cask left as it is")
        return False
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'^  version "[^"]*"$', f'  version "{version}"', text, count=1, flags=re.M)
    text = re.sub(r'^  sha256 .*$', f'  sha256 "{digest}"', text, count=1, flags=re.M)
    path.write_text(text, encoding="utf-8")
    return True


if __name__ == "__main__":
    update(sys.argv[1], Path(sys.argv[2]).read_text(encoding="utf-8"))
