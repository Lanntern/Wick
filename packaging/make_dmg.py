"""The Mac download: a disk image that opens to Wick.app next to a shortcut to Applications, the usual "drag it
over" install. Compressed (UDZO) and checked by hdiutil after it's made.

Usage: python packaging/make_dmg.py dist/Wick.app Wick-macos-arm64.dmg
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main(app: str, out: str) -> int:
    bundle, dmg = Path(app), Path(out)
    with tempfile.TemporaryDirectory(prefix="wick-dmg-") as tmp:
        stage = Path(tmp) / "Wick"
        stage.mkdir()
        # ditto keeps the bundle exactly as it is (links, attributes, the signature)
        subprocess.run(["ditto", str(bundle), str(stage / bundle.name)], check=True)
        os.symlink("/Applications", stage / "Applications")
        root = Path(__file__).resolve().parent.parent
        for name in ("README.md", "LICENSE", "THIRD-PARTY-NOTICES.md"):
            shutil.copy2(root / name, stage / name)
        dmg.unlink(missing_ok=True)
        subprocess.run(["hdiutil", "create", "-volname", "Wick", "-srcfolder", str(stage), "-fs", "HFS+",
                        "-format", "UDZO", "-imagekey", "zlib-level=9", "-ov", str(dmg)], check=True)
    subprocess.run(["hdiutil", "verify", str(dmg)], check=True)
    print(f"{dmg}: {dmg.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:3] if len(sys.argv) > 2 else ("dist/Wick.app", "Wick-macos-arm64.dmg"))))
