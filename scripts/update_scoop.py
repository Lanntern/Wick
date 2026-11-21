"""Point the Scoop manifest (bucket/wick.json) at a published release: its version, zip URL and checksum.

    python scripts/update_scoop.py 0.2.0 SHA256SUMS.txt

The checksum comes from the release's SHA256SUMS.txt (written by the build that made the zip).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "bucket" / "wick.json"
ZIP = "Wick-windows.zip"


def zip_hash(sums: str) -> str:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == ZIP and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    raise SystemExit(f"{ZIP} isn't listed in the checksums")


def update(version: str, sums: str, path: Path = MANIFEST) -> dict:
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        raise SystemExit(f"Not a version number: {version!r}")
    m = json.loads(path.read_text(encoding="utf-8"))
    m["version"] = version
    m["architecture"]["64bit"] = {
        "url": f"https://github.com/Lanntern/Wick/releases/download/v{version}/{ZIP}",
        "hash": zip_hash(sums),
    }
    path.write_text(json.dumps(m, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    return m


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    got = update(sys.argv[1].removeprefix("v"), Path(sys.argv[2]).read_text(encoding="utf-8"))
    print(f"bucket/wick.json: {got['version']} {got['architecture']['64bit']['hash']}")
