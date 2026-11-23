"""Finish the macOS app bundle PyInstaller made: what Finder needs to offer Wick in Open With (pictures, videos,
music, documents and folders), the version, the lowest macOS it runs on, then an ad-hoc signature again.

The signature is ad hoc (no Apple certificate): enough for Apple silicon to run the app, but macOS still says it's
from an unidentified developer. Open it the first time with right-click › Open, or clear the download mark:
    xattr -dr com.apple.quarantine Wick.app

Usage: python packaging/mac_bundle.py dist/Wick.app
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

DOCUMENT_TYPES = [
    ("Picture", ["public.image"]),
    ("Video", ["public.movie"]),
    ("Audio", ["public.audio"]),
    ("Document", ["com.adobe.pdf", "org.openxmlformats.openxml", "org.idpf.epub-container", "public.zip-archive"]),
    ("Folder", ["public.folder"]),
]


def main(app: str) -> int:
    bundle = Path(app)
    plist_path = bundle / "Contents" / "Info.plist"
    with open(plist_path, "rb") as f:
        info = plistlib.load(f)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from wick import APP_NAME, __version__

    info.update({
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,       # (follows dark mode)
        "LSApplicationCategoryType": "public.app-category.utilities",
        "CFBundleDocumentTypes": [{"CFBundleTypeName": name, "CFBundleTypeRole": "Editor",
                                   "LSHandlerRank": "Alternate", "LSItemContentTypes": types}
                                  for name, types in DOCUMENT_TYPES],
    })
    with open(plist_path, "wb") as f:
        plistlib.dump(info, f)
    # Changing Info.plist breaks PyInstaller's signature; Apple silicon refuses to start unsigned code.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(bundle)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
    print(f"{bundle}: {APP_NAME} {__version__}, Open With for {len(DOCUMENT_TYPES)} kinds, signed ad hoc")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist/Wick.app"))
