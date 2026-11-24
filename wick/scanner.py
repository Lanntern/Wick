"""Walking folders (or taking individual files) and collecting compressible files."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .formats import (
    _EXT_KIND, AUDIO, AUDIO_EXTS, CONVERT_INPUTS, CONVERT_ONLY_KINDS, DOC, DOCUMENT, DOCUMENT_EXTS, HEIC, IMAGE_KINDS,
    ARCHIVE, COMIC, COMIC_EXTS, FONT, JPEG, JXL, PDF, SVG, TEMP_PREFIX, VIDEO, VIDEO_EXTS,
    ImageInfo, classify, read_image_info, sniff,
)
from .probe import AudioInfo, VideoInfo, detect_cadence, probe_audio, probe_video
from .protected import cloud_only, inside_library, is_hidden, is_library
from .settings import Settings

# Folder names that are never scanned (case-insensitive). These hold OS,
# program or game data where converting files would break things.
EXCLUDED_DIR_NAMES = {
    "windows", "program files", "program files (x86)", "programdata", "appdata",
    "$recycle.bin", "system volume information", "$windows.~bt", "$windows.~ws",
    "recovery", "steamapps", "steamlibrary", "epic games", "riot games",
    "node_modules", ".git", ".svn", ".hg", "site-packages", "__pycache__",
    ".venv", "venv", ".cache", ".trash",
}

# A folder containing any of these files is treated as a program/game folder
# and skipped together with its subfolders (when skip_program_folders is on).
PROGRAM_MARKER_EXTS = {".dll", ".so", ".dylib"}



@dataclass
class FileEntry:
    path: Path
    size: int
    kind: str
    mtime: float
    root: Path | None = None        # the folder the user added (for "keep folder structure")
    image: ImageInfo | None = None
    video: VideoInfo | None = None
    audio: AudioInfo | None = None
    live_pair: bool = False         # photo or video half of an Apple Live Photo (same name, .HEIC/.JPG + .MOV)
    real_ext: str = ""              # its contents are another type than its name says (".png" for a PNG named .jpg)
    picked: bool = False            # chosen by itself (dropped or picked, not found in a folder): no size minimums

    @property
    def info(self):
        """The probed details for this kind of file (image, video or audio)."""
        return self.video if self.kind == VIDEO else self.audio if self.kind == AUDIO else self.image


@dataclass
class ScanResult:
    roots: list[Path]
    entries: list[FileEntry] = field(default_factory=list)
    skipped_program_dirs: list[Path] = field(default_factory=list)
    skipped_libraries: list[Path] = field(default_factory=list)    # Photos and other apps' libraries
    unreadable_dirs: list[Path] = field(default_factory=list)
    cloud_only_files: int = 0
    cloud_only_live: list[Path] = field(default_factory=list)   # cloud-only photos and .MOVs (Live Photo halves)
    jxl_files: int = 0          # JPEG XL files seen (already efficient; not listed as entries)
    jxl_bytes: int = 0
    dirs_scanned: int = 0
    cancelled: bool = False

    @property
    def root(self) -> Path:
        return self.roots[0] if self.roots else Path.cwd()


ProgressFn = Callable[[str, int, int], None]  # (message, done, total)
PARTS_PREFIX = ".wick-parts-"   # a video being encoded scene by scene (chunked.py): never scanned


def scan(
    paths: Path | Iterable[Path],
    settings: Settings,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    probe: bool = True,
    picked: bool = False,
    found: Callable[[FileEntry], None] | None = None,
    listed: Callable[[list[FileEntry]], None] | None = None,
    ready: Callable[[list[FileEntry]], None] | None = None,
) -> ScanResult:
    """Scan folders and/or individual files. Files given directly are always included; with ``picked`` (someone
    chose them, rather than Watch Folders finding them) the size minimums don't apply to them either.
    ``found`` runs on the scanning thread for each new entry, before optional compression probing.
    ``listed`` gets every entry once the folders are listed, before their details are read (a list can show them
    straight away); ``ready`` then gets batches of entries whose details have been read."""
    if isinstance(paths, (str, os.PathLike)):
        paths = [Path(paths)]
    roots = [Path(os.path.abspath(p)) for p in paths]
    result = ScanResult(roots=roots)
    seen: set[str] = set()

    for root in roots:
        library = inside_library(root)
        if library is not None:             # (even when chosen directly: changing it would break the app's library)
            if library not in result.skipped_libraries:
                result.skipped_libraries.append(library)
            continue
        if root.is_file():
            _add_file(result, root, None, seen, settings, found)
            if picked and result.entries and result.entries[-1].path == root:
                result.entries[-1].picked = True
        elif root.is_dir():
            listing = _fast_listing(root, progress, cancel)
            if listing is not None:
                walk_listing(result, root, listing, 0, settings, seen, found, cancel)
            else:
                _walk(result, root, settings, progress, cancel, seen, found)
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            return result

    mark_live_photos(result.entries, result.cloud_only_live)
    if listed is not None:
        listed(list(result.entries))
    if probe:
        probe_entries(result.entries, progress, cancel, settings, ready)
        if cancel is not None and cancel.is_set():
            result.cancelled = True
    result.entries.sort(key=lambda e: e.size, reverse=True)
    return result


_LIVE_PHOTO_EXTS = {".heic", ".heif", ".jpg", ".jpeg"}


def mark_live_photos(entries: list[FileEntry], also_listed: Iterable[Path] = ()) -> None:
    """Flag Apple Live Photos: a photo and a .MOV with the same name in the same folder belong together
    (Photos apps pair them by name; converting either one breaks the pair).

    Files found by listing their folder are paired from the names already listed (``also_listed``: ones that are
    listed but not entries, such as cloud-only files), with no extra look at the disk: on a network drive, each
    look is a round trip. Files chosen by themselves are looked up."""
    names: dict[Path, set[str]] = {}
    for p in (*(e.path for e in entries), *also_listed):
        names.setdefault(p.parent, set()).add(p.name)
    for e in entries:
        ext = e.path.suffix.lower()
        if ext in _LIVE_PHOTO_EXTS:
            partners = [e.path.with_suffix(x) for x in (".MOV", ".mov")]
        elif ext == ".mov":
            partners = [e.path.with_suffix(x) for x in (".HEIC", ".heic", ".JPG", ".jpg", ".JPEG", ".jpeg")]
        else:
            continue
        if e.root is not None:
            listed = names.get(e.path.parent, set())
            e.live_pair = any(p.name in listed for p in partners)
        else:
            e.live_pair = any(p.is_file() for p in partners)


def _kind(p: Path, settings: Settings | None) -> str | None:
    if settings is not None and settings.convert_inputs and p.suffix.lower() == ".pdf":
        return PDF                                    # (Convert: its own kind, with its own choices)
    if settings is not None and settings.convert_inputs and p.suffix.lower() in COMIC_EXTS:
        return COMIC                                  # (Convert: pages to pictures or PDF; Compress: a document)
    if settings is not None and not settings.convert_inputs:
        if p.suffix.lower() == ".svg" and settings.image_svg:
            return SVG
        if p.suffix.lower() == ".zip" and settings.archive_pictures:
            return DOCUMENT                           # (its pictures, like a comic book's)
    kind = classify(p)
    if kind is None and settings is not None:
        ext = p.suffix.lower()
        if settings.scan_jxl and ext == ".jxl":
            return JXL
        if settings.convert_inputs:
            from .archives import archive_ext

            return CONVERT_INPUTS.get(ext) or (ARCHIVE if archive_ext(p) else None)
    return kind


def _add_file(result: ScanResult, p: Path, root: Path | None, seen: set[str], settings: Settings | None = None,
              found: Callable[[FileEntry], None] | None = None) -> None:
    key = os.path.normcase(str(p))
    if key in seen or p.name.startswith(TEMP_PREFIX):
        return
    kind = _kind(p, settings)
    if kind is None:
        return
    try:
        st = p.stat()
    except OSError:
        return
    if cloud_only(st):
        result.cloud_only_files += 1
        return
    seen.add(key)
    entry = FileEntry(p, st.st_size, kind, st.st_mtime, root)
    result.entries.append(entry)
    if found:
        found(entry)


# Every extension the scanner lists (and JPEG XL, counted as already efficient): what to ask Everything for.
_LISTED_EXTS = ({*_EXT_KIND, *VIDEO_EXTS, *AUDIO_EXTS, *DOCUMENT_EXTS, *CONVERT_INPUTS, ".zip", ".7z", ".tar", ".tgz",
                 ".gz", ".tbz2", ".bz2", ".txz", ".xz"})

FAST_DRIVES = True   # Settings › "Fast whole-drive scanning" (Windows, NTFS: reads the drive's file table)


def _is_drive_root(p: Path) -> bool:
    return os.name == "nt" and p.anchor != "" and Path(p.anchor) == p


def _mac_volume_root(p: Path) -> bool:
    return sys.platform == "darwin" and (str(p) == "/" or (p.parent == Path("/Volumes")))


def _big_scope(root: Path) -> bool:
    """Worth asking Everything: a whole drive or your whole user folder (where it saves minutes). A smaller
    folder is read directly: quick anyway, and certain to include a file saved a moment ago (Everything's
    index can trail the disk by a moment)."""
    try:
        return _is_drive_root(root) or _mac_volume_root(root) or os.path.normcase(os.path.abspath(root)) == \
            os.path.normcase(os.path.abspath(Path.home()))
    except OSError:
        return False


def _fast_listing(root: Path, progress: ProgressFn | None, cancel: threading.Event | None):
    """Windows: the list of files without asking the disk about every one. From Everything when it's running
    (any folder, instant), else for a whole drive from its NTFS file table (Windows asks for administrator
    permission). None: scan the usual way."""
    if sys.platform == "darwin" and _big_scope(root):
        from . import spotlight

        try:
            return spotlight.listing_for(root, _LISTED_EXTS, progress)
        except Exception:  # noqa: BLE001 - Spotlight's answer is a bonus: any problem, the usual way
            return None
    if os.name == "nt" and _big_scope(root):
        from . import everything

        try:
            listing = everything.listing_for(root, _LISTED_EXTS, progress)
        except Exception:  # noqa: BLE001 - Everything's answer is a bonus: any problem, the usual way
            listing = None
        if listing is not None:
            return listing
    if not (FAST_DRIVES and _is_drive_root(root)):
        return None
    from . import ntfs
    import tempfile

    if progress:
        progress(f"Reading the file table of {root} (Windows asks for permission)…", 0, 0)
    return ntfs.listing_for([root], lambda n: classify(Path(n)) is not None or n.lower().endswith(".jxl"),
                            Path(tempfile.gettempdir()), cancel)


def walk_listing(result: ScanResult, root: Path, listing, root_index: int, settings: Settings,
                 seen: set[str], found: Callable[[FileEntry], None] | None = None,
                 cancel: threading.Event | None = None) -> None:
    """The same rules as _walk, over a drive's file table (ntfs.Listing) instead of the disk."""
    from .ntfs import CLOUD_ONLY, HIDDEN, REPARSE, SYSTEM

    top = listing.roots.get(root_index)
    if top is None:
        return
    stack = [(top, root)]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        d, path = stack.pop()
        result.dirs_scanned += 1
        _parent, _attrs, marker, _name = listing.dirs[d]
        if settings.skip_program_folders and marker:
            result.skipped_program_dirs.append(path)
            continue
        subdirs = []
        for sub in listing.subdirs.get(d, []):
            _p, sattrs, _m, sname = listing.dirs[sub]
            if sattrs & REPARSE or sname.lower() in EXCLUDED_DIR_NAMES or sname.startswith(PARTS_PREFIX):
                continue
            if is_library(sname):
                result.skipped_libraries.append(path / sname)
                continue
            if not settings.include_hidden and (sname.startswith(".") or sattrs & (HIDDEN | SYSTEM)):
                continue
            subdirs.append((sname, sub))
        for size, mtime, fattrs, fname in listing.files.get(d, []):
            if cancel is not None and cancel.is_set():
                return
            if (fname.startswith(TEMP_PREFIX) or fattrs & REPARSE
                    or os.path.splitext(fname)[1].lower() not in _LISTED_EXTS):
                continue
            if not settings.include_hidden and (fname.startswith(".") or fattrs & (HIDDEN | SYSTEM)):
                continue
            p = path / fname
            kind = _kind(p, settings)
            if kind is None:
                if fname.lower().endswith(".jxl"):
                    result.jxl_files += 1
                    result.jxl_bytes += size
                continue
            if fattrs & CLOUD_ONLY:
                result.cloud_only_files += 1
                if kind in (VIDEO, JPEG, HEIC):
                    result.cloud_only_live.append(p)
                continue
            key = os.path.normcase(str(p))
            if key in seen:
                continue
            seen.add(key)
            entry = FileEntry(p, size, kind, mtime, root)
            result.entries.append(entry)
            if found:
                found(entry)
        stack.extend((sub, path / name) for name, sub in sorted(subdirs, reverse=True))


def _walk(result: ScanResult, root: Path, settings: Settings, progress: ProgressFn | None,
          cancel: threading.Event | None, seen: set[str],
          found: Callable[[FileEntry], None] | None = None) -> None:
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                children = list(it)
        except OSError:
            result.unreadable_dirs.append(d)
            continue
        result.dirs_scanned += 1

        if settings.skip_program_folders and any(
            c.is_file(follow_symlinks=False)
            and os.path.splitext(c.name)[1].lower() in PROGRAM_MARKER_EXTS
            for c in children
        ):
            result.skipped_program_dirs.append(d)
            continue

        subdirs = []
        for c in children:
            if cancel is not None and cancel.is_set():
                return
            try:
                if c.is_symlink():
                    continue
                if c.is_dir(follow_symlinks=False):
                    if c.name.lower() in EXCLUDED_DIR_NAMES or c.name.startswith(PARTS_PREFIX):
                        continue
                    if is_library(c.name):          # (Photos and other apps' libraries: see protected.py)
                        result.skipped_libraries.append(Path(c.path))
                        continue
                    if not settings.include_hidden and is_hidden(c):
                        continue
                    subdirs.append(Path(c.path))
                    continue
                if not c.is_file(follow_symlinks=False) or c.name.startswith(TEMP_PREFIX):
                    continue
                if os.path.splitext(c.name)[1].lower() not in _LISTED_EXTS:
                    continue            # (most files: no path made, and on a Mac no extra look for the hidden flag)
                if not settings.include_hidden and is_hidden(c):
                    continue
                p = Path(c.path)
                kind = _kind(p, settings)
                if kind is None:
                    if c.name.lower().endswith(".jxl"):
                        result.jxl_files += 1
                        result.jxl_bytes += c.stat(follow_symlinks=False).st_size
                    continue
                st = c.stat(follow_symlinks=False)
                if cloud_only(st):
                    result.cloud_only_files += 1
                    if kind in (VIDEO, JPEG, HEIC):
                        result.cloud_only_live.append(p)
                    continue
                key = os.path.normcase(c.path)
                if key in seen:
                    continue
                seen.add(key)
                entry = FileEntry(p, st.st_size, kind, st.st_mtime, root)
                result.entries.append(entry)
                if found:
                    found(entry)
            except OSError:
                continue
        stack.extend(sorted(subdirs, reverse=True))
        if progress and result.dirs_scanned % 25 == 0:
            progress(f"Scanning {d}", result.dirs_scanned, 0)


_preloaded = False
_heif_loaded = False


def preload_codecs(heif: bool = True) -> None:
    """Import image libraries on the calling thread before worker threads use them.

    Importing them for the first time from several threads at once can deadlock
    in a frozen (PyInstaller) app. HEIC support (which brings a large x265 library) only loads when
    HEIC files are involved.
    """
    global _preloaded, _heif_loaded
    if not _preloaded:
        _preloaded = True
        from PIL import Image

        Image.init()
        for mod in ("mozjpeg_lossless_optimization", "oxipng", "imagequant"):
            try:
                __import__(mod)
            except Exception:  # noqa: BLE001 - reported when a file actually needs it
                from wick.log import ignored
                ignored("scanner.preload_codecs")
                pass
    if heif and not _heif_loaded:
        _heif_loaded = True
        from .formats import register_heif

        register_heif()


def cadence_at_scan(info: VideoInfo) -> bool:
    """Whether to look for repeated frames while scanning. That takes several times longer than reading the video's
    details, and it only changes the plan for a video in an efficient codec (repeated frames are the reason to
    re-encode it at all). Other videos are re-encoded anyway: they're checked just before encoding (video.py)."""
    from .plan import EFFICIENT_VIDEO_CODECS

    return info.codec in EFFICIENT_VIDEO_CODECS


def probe_entries(entries: list[FileEntry], progress: ProgressFn | None = None,
                  cancel: threading.Event | None = None, settings: Settings | None = None,
                  ready: Callable[[list[FileEntry]], None] | None = None) -> None:
    """Read image headers and video info (in parallel). Repeated frames in videos are only looked for when
    the option to remove them is on (one FFmpeg run per video saved otherwise)."""
    from .probe import NOT_MEASURED

    cadence = settings is None or settings.video_fix_cadence
    from . import index

    todo = []
    for e in entries:
        if e.info is not None or e.kind in (DOCUMENT, DOC, PDF, FONT, COMIC, ARCHIVE):     # (nothing to read ahead)
            continue
        cached = index.lookup(e.path, e.size, e.mtime, e.kind)
        if cached is None:
            todo.append(e)
        elif e.kind == VIDEO and cadence and cached.cadence == NOT_MEASURED and cadence_at_scan(cached):
            todo.append(e)                        # (scanned with the option off: look for repeated frames now)
        elif e.kind == VIDEO:
            e.video = cached
        elif e.kind == AUDIO:
            e.audio = cached
        else:
            e.image = cached
    waiting = {id(e) for e in todo}
    batch: list[FileEntry] = [e for e in entries if id(e) not in waiting] if ready is not None else []
    last_sent = [time.monotonic()]

    def send(force: bool = False) -> None:
        # (called under ``lock``) every quarter second at most, so the list updates smoothly without flooding
        if ready is None or not batch or (not force and time.monotonic() - last_sent[0] < 0.25):
            return
        ready(batch.copy())
        batch.clear()
        last_sent[0] = time.monotonic()

    index.refresh_seen()
    if not todo:
        send(True)
        return
    preload_codecs(heif=any(e.kind == HEIC for e in entries))
    done = 0
    lock = threading.Lock()
    unsaved: list[FileEntry] = []         # read but not yet in the index
    last_saved = [time.monotonic()]

    def save(force: bool = False) -> None:
        # Saved as they come in (every 200 files or 5 seconds), so a long scan that's cancelled, or a crash, doesn't
        # lose what was already read. Called outside ``lock``.
        with lock:
            if not unsaved or (not force and len(unsaved) < 200 and time.monotonic() - last_saved[0] < 5):
                return
            items = [(e.path, e.size, e.mtime, e.kind, e.info) for e in unsaved if e.info is not None]
            unsaved.clear()
            last_saved[0] = time.monotonic()
        index.store(items)
    with lock:
        send(True)                     # (cached ones first)

    def work(e: FileEntry) -> None:
        nonlocal done
        if cancel is not None and cancel.is_set():
            return
        if e.kind not in (VIDEO, AUDIO, *CONVERT_ONLY_KINDS):   # (a RAW photo is a TIFF inside, and so on)
            # Images are named by whoever saved them (phones save HEIC as .jpg, sites PNGs as .jpg): read what
            # the file really is, so it's decoded and compressed as that. The file is opened once for both.
            read = True
            try:
                f = open(e.path, "rb")
            except OSError:
                f = None
            if f is not None:
                with f:
                    real = sniff(e.path, f.read(64))
                    if real is not None and real[0] != e.kind and real[0] in (*IMAGE_KINDS, VIDEO, AUDIO):
                        e.kind, e.real_ext = real
                    read = e.kind not in (VIDEO, AUDIO)
                    if read:
                        e.image = read_image_info(e.path, e.kind, f)
        else:
            read = False
        if read:
            pass                                # (a picture: read above, or unreadable)
        elif e.kind == VIDEO:
            e.video = probe_video(e.path)
            if e.video is not None:
                e.video.cadence = (detect_cadence(e.path, e.video) if cadence and cadence_at_scan(e.video)
                                   else NOT_MEASURED)
        elif e.kind == AUDIO:
            e.audio = probe_audio(e.path)
        else:
            e.image = read_image_info(e.path, e.kind)
        with lock:
            done += 1
            unsaved.append(e)
            if ready is not None:
                batch.append(e)
                send()
            if progress and (done % 20 == 0 or e.kind in (VIDEO, AUDIO)):
                progress(f"Reading file info: {e.path.name}", done, len(todo))
        save()

    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 2))) as pool:
        list(pool.map(work, todo))
    with lock:
        send(True)
    save(force=True)
