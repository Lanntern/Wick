"""APFS compression on a Mac: Compress Apps' counterpart of Windows' compact.exe.

APFS (and HFS+) can store a file compressed and decompress it as it's read, with nothing changing for the apps
using it: the same mechanism macOS uses for its own system files. Each file is copied compressed beside itself
with ``ditto --hfsCompression`` (which keeps permissions, dates, extended attributes and code signatures), checked
to read back the same size, then swapped in with one rename. Undo expands it in place with macOS's afscexpand
(ditto keeps a compressed file compressed even with --nohfsCompression), or, where that's missing, writes a plain
copy beside it and swaps that in.

Files that are hard links, already compressed (or, for undo, not compressed), tiny, or locked are skipped; apps
installed for every user (owned by root) can't be changed without an administrator, and say so.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

UF_COMPRESSED = 0x20            # stat st_flags: the file is stored compressed
MIN_BYTES = 8 * 1024            # smaller files gain nothing (APFS allocates in 4 KB blocks)
PROTECTED_ROOTS = ("/System", "/usr", "/bin", "/sbin", "/Library", "/private")
CANCELLED = 1223                # (the same code Windows' path returns for Stop)


def supported(path: Path | None = None) -> bool:
    if sys.platform != "darwin":
        return False
    return volume_type(path or Path.home()) in ("apfs", "hfs")


def volume_type(path: Path) -> str:
    from ..drives import mac_mount_fs

    return mac_mount_fs(path)


def is_compressed(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_flags", 0) & UF_COMPRESSED)


def check_folder(folder: Path) -> None:
    """Refuse what mustn't be compressed here: the system, a whole volume, all of Applications at once."""
    text = str(folder)
    if folder.parent == folder or text in ("/Applications", str(Path.home())):
        raise ValueError("Choose an individual app or game folder, not a whole drive or Applications itself")
    if any(text == root or text.startswith(root + "/") for root in PROTECTED_ROOTS) \
            and not text.startswith("/private/var/folders/"):       # (temporary samples live there)
        raise ValueError("macOS system folders can't be compressed here")


def one(path: Path, undo: bool = False) -> int:
    """Compress (or decompress) one file in place; returns bytes saved (negative for undo), 0 if skipped."""
    if sys.platform == "win32":
        raise OSError("APFS compression is a Mac's")
    st = path.stat(follow_symlinks=False)
    if not path.is_file() or path.is_symlink() or st.st_nlink > 1 or st.st_size < MIN_BYTES:
        return 0
    if is_compressed(st) != undo:              # (already as wanted)
        return 0
    if undo:
        return -_expand(path, st)
    tmp = path.with_name(f".{path.name}.wick-apfs")
    try:
        proc = subprocess.run(["/usr/bin/ditto", "--hfsCompression", str(path), str(tmp)],
                              capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise OSError(proc.stderr.strip() or f"ditto exited with {proc.returncode}")
        new = tmp.stat()
        if new.st_size != st.st_size:
            raise OSError("the compressed copy didn't read back the same size")
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, path)
        return st.st_blocks * 512 - new.st_blocks * 512
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


AFSCEXPAND = Path("/usr/bin/afscexpand")


def _expand(path: Path, st: os.stat_result) -> int:
    """Store a compressed file uncompressed again; returns the bytes it takes now beyond before."""
    if sys.platform == "win32":
        raise OSError("APFS compression is a Mac's")
    if AFSCEXPAND.exists():
        proc = subprocess.run([str(AFSCEXPAND), str(path)], capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise OSError(proc.stderr.strip() or f"afscexpand exited with {proc.returncode}")
    else:
        import shutil

        tmp = path.with_name(f".{path.name}.wick-apfs")
        try:
            with open(path, "rb") as src, open(tmp, "wb") as dst:     # (reading gives the plain bytes)
                shutil.copyfileobj(src, dst, 1 << 20)
            os.chmod(tmp, st.st_mode & 0o7777)
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
    new = path.stat()
    if is_compressed(new) or new.st_size != st.st_size:
        raise OSError("it didn't come back uncompressed")
    return new.st_blocks * 512 - st.st_blocks * 512


def run(folder: Path, files: Iterable[Path], undo: bool = False, on_line: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None) -> int:
    """Compress ``files`` (inside ``folder``); 0 when every file worked, CANCELLED on Stop, 1 if some couldn't."""
    check_folder(folder.resolve())
    saved = failed = done = 0
    denied = False
    for path in files:
        if cancel is not None and cancel.is_set():
            return CANCELLED
        try:
            saved += one(path, undo)
            done += 1
        except PermissionError:
            failed += 1
            denied = True
        except (OSError, subprocess.SubprocessError) as e:
            failed += 1
            from ..log import log

            log.warning("APFS %s failed for %s: %s", "decompression" if undo else "compression", path, e)
        if on_line and done % 50 == 0:
            on_line(f"{'Decompressing' if undo else 'Compressing'}: {path.name}")
    if on_line:
        from ..formats import human

        verb = "Decompressed" if undo else "Compressed"
        on_line(f"{verb} {done:,} files, {human(abs(saved))} {'used again' if undo else 'saved'}"
                + (f"; {failed:,} couldn't be changed" if failed else "")
                + (" (installed for every user: only an administrator can change those)" if denied else ""))
    return 1 if failed else 0


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input (IOKit's HIDIdleTime), 0 if unknown."""
    try:
        out = subprocess.run(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    for line in out.splitlines():
        if '"HIDIdleTime"' in line:
            try:
                return int(line.rsplit("=", 1)[1].strip()) / 1e9
            except ValueError:
                return 0
    return 0


__all__ = ["supported", "volume_type", "is_compressed", "check_folder", "one", "run", "idle_seconds"]
