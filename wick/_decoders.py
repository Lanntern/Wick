"""The decoder worker: opens files Wick didn't make (thumbnails, previews, Broken Files, Similar Photos) in a
separate process that gives up rights before it reads any of them (see sandbox.py, which starts it).

What it gives up, on Windows:

- **Integrity level: low.** It can still read your files, but it can't change them, your settings or most of
  the registry, and can't send messages to other programs' windows. Windows applies this to every program it
  starts too (ffmpeg, ffprobe, djxl).
- **A job object** that it can't leave: no clipboard, no changes to system settings, no other desktops or
  logging off; at most a few programs at once (no runaway process trees); a memory limit; and everything in it
  ends when the worker does.

Elsewhere: no new privileges (Linux), a memory limit and no core dumps.

Replies are JSON plus raw pixels, never pickled objects: a worker that a crafted file took over can only send
back data, which sandbox.py checks before using.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable

LOW_INTEGRITY_RID = 0x1000        # SECURITY_MANDATORY_LOW_RID


# ------------------------------------------------------------------------------------------ giving up rights


def enter_sandbox(tmp_dir: str, memory_limit: int) -> dict:
    """Restrict this process (before any untrusted file is read). Returns what took effect, for the log."""
    applied: dict = {}
    if tmp_dir:
        os.makedirs(tmp_dir, exist_ok=True)
        for var in ("TEMP", "TMP", "TMPDIR"):
            os.environ[var] = tmp_dir
        import tempfile

        tempfile.tempdir = tmp_dir
    try:
        if os.name == "nt":
            applied["job"] = _windows_job(memory_limit)
            applied["integrity"] = _windows_low_integrity()
        else:
            applied.update(_posix_limits(memory_limit))
    except Exception as e:  # noqa: BLE001 - reported to the app, which decides whether to use this worker
        applied["error"] = f"{type(e).__name__}: {e}"
    return applied


def _windows_job(memory_limit: int) -> bool:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
    info = Extended()
    # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS | DIE_ON_UNHANDLED_EXCEPTION (no breakaway: its programs stay in it)
    info.BasicLimitInformation.LimitFlags = 0x2000 | 0x8 | 0x400
    info.BasicLimitInformation.ActiveProcessLimit = 8     # itself and the decoders it runs, never a process tree
    if memory_limit:
        info.BasicLimitInformation.LimitFlags |= 0x100       # PROCESS_MEMORY
        info.ProcessMemoryLimit = memory_limit
    if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject (limits) failed")
    # Basic UI restrictions, all of them: desktop, display settings, log off, global atoms, other processes'
    # windows (handles), clipboard (read and write), system parameters.
    ui = wintypes.DWORD(0xFF)
    if not k32.SetInformationJobObject(wintypes.HANDLE(job), 4, ctypes.byref(ui), ctypes.sizeof(ui)):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject (UI) failed")
    if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), k32.GetCurrentProcess()):
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
    _windows_job.handle = job  # type: ignore[attr-defined]   # (kept open: closing it ends the worker)
    return True


def _windows_low_integrity() -> str:
    """Lower this process's own integrity level to low (allowed: a process may lower, never raise, its own)."""
    import ctypes
    from ctypes import wintypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    adv.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x80 | 0x8, ctypes.byref(token)):  # ADJUST_DEFAULT|QUERY
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    sid = ctypes.c_void_p()
    if not adv.ConvertStringSidToSidW("S-1-16-4096", ctypes.byref(sid)):        # Mandatory Label\Low
        raise OSError(ctypes.get_last_error(), "ConvertStringSidToSid failed")
    label = SidAndAttributes(sid, 0x20)                                          # SE_GROUP_INTEGRITY
    size = ctypes.sizeof(label) + adv.GetLengthSid(sid)
    if not adv.SetTokenInformation(token, 25, ctypes.byref(label), size):       # TokenIntegrityLevel
        raise OSError(ctypes.get_last_error(), "SetTokenInformation failed")
    # Read it back: only a level Windows reports counts.
    needed = wintypes.DWORD()
    adv.GetTokenInformation(token, 25, None, 0, ctypes.byref(needed))
    buf = ctypes.create_string_buffer(needed.value)
    if not adv.GetTokenInformation(token, 25, buf, needed, ctypes.byref(needed)):
        raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")
    got = ctypes.cast(buf, ctypes.POINTER(SidAndAttributes)).contents.Sid
    rid = adv.GetSidSubAuthority(got, adv.GetSidSubAuthorityCount(got).contents.value - 1).contents.value
    if rid > LOW_INTEGRITY_RID:
        raise OSError(f"integrity level is still {rid:#x}")
    return "low"


def _posix_limits(memory_limit: int) -> dict:
    import resource

    applied = {}
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if memory_limit:
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
        applied["memory"] = memory_limit
    if sys.platform.startswith("linux"):
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(38, 1, 0, 0, 0) == 0:                  # PR_SET_NO_NEW_PRIVS
            applied["no_new_privs"] = True
    return applied


# ------------------------------------------------------------------------------------------ the work


def _rgba(img, max_pixels: int):
    """(header, raw RGBA bytes) of a Pillow image, shrunk to at most max_pixels."""
    from PIL import Image

    img = img.convert("RGBA")
    if img.width * img.height > max_pixels:
        scale = (max_pixels / (img.width * img.height)) ** 0.5
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    return {"w": img.width, "h": img.height}, img.tobytes()


def broken_check(path: str, size: int, thorough: bool):
    from pathlib import Path

    from .cleanup.broken import check_file

    found = check_file(Path(path), int(size), bool(thorough))
    return {"result": list(found) if found else None}, None


def similar_fingerprint(path: str):
    from pathlib import Path

    from .cleanup.similar import fingerprint

    found = fingerprint(Path(path))
    return {"result": list(found) if found else None}, None


def similar_inspect(path: str, px: int):
    from pathlib import Path

    from .cleanup.similar import inspect

    sharp, gray = inspect(Path(path), int(px))
    if gray is None:
        return {"sharpness": float(sharp), "w": 0, "h": 0}, None
    return {"sharpness": float(sharp), "w": gray.width, "h": gray.height}, gray.convert("L").tobytes()


def thumbnail(path: str, kind: str, max_px: int):
    """A picture's or video's preview, at most max_px on its longer side (album art for music)."""
    import io
    from pathlib import Path

    from PIL import Image, ImageOps

    from . import tools
    from .formats import AUDIO, HEIC, VIDEO, register_heif

    p, max_px = Path(path), int(max_px)
    scale = f"scale={max_px}:{max_px}:force_original_aspect_ratio=decrease"
    if kind == AUDIO or kind == VIDEO or p.suffix.lower() in (".mp4", ".mkv", ".webm"):
        ffmpeg = tools.find_tool("ffmpeg")
        if not ffmpeg:
            return {"w": 0, "h": 0}, None
        tries = ([["-i", path, "-an"]] if kind == AUDIO else
                 [["-ss", "1", "-i", path], ["-i", path]])        # (a very short clip: its first frame)
        data = b""
        for inp in tries:
            proc = tools.run([ffmpeg, "-v", "error", "-nostdin", *inp, "-frames:v", "1", "-vf", scale,
                              "-f", "image2pipe", "-c:v", "png", "-"], timeout=20, check=False, text=False)
            data = proc.stdout or b""
            if proc.returncode == 0 and data:
                break
        if not data:
            return {"w": 0, "h": 0}, None
        with Image.open(io.BytesIO(data)) as im:
            return _rgba(im, max_px * max_px)
    if p.suffix.lower() == ".jxl":
        from .images import open_for_display

        pic = open_for_display(p)
        pic.thumbnail((max_px, max_px), Image.LANCZOS)
        return _rgba(pic, max_px * max_px)
    if kind == HEIC:
        register_heif()
    with Image.open(p) as im:
        if im.format == "JPEG":
            im.draft("RGB", (max_px * 2, max_px * 2))          # fast DCT-domain downscale
        im.seek(0)
        pic = (ImageOps.exif_transpose(im) or im).convert("RGBA")
        pic.thumbnail((max_px, max_px), Image.LANCZOS)
        return _rgba(pic, max_px * max_px)


def display_image(path: str, max_pixels: int):
    """The first frame of a picture as it's shown (orientation applied), for the preview window."""
    from pathlib import Path

    from .images import open_for_display

    return _rgba(open_for_display(Path(path)), int(max_pixels))


FUNCTIONS: dict[str, Callable] = {f.__name__: f for f in (broken_check, similar_fingerprint, similar_inspect, thumbnail,
                                                          display_image)}


def _test_sleep(seconds: float):
    import time

    time.sleep(float(seconds))
    return {}, None


def _test_write(path: str):
    """Can this worker create a file there? (The tests check a low-integrity worker can't write to your folders.)"""
    try:
        with open(path, "wb") as f:
            f.write(b"x")
        return {"wrote": True}, None
    except OSError:
        return {"wrote": False}, None


if os.environ.get("WICK_DECODER_TESTS") == "1":      # (only the test suite turns these on)
    FUNCTIONS.update(_test_sleep=_test_sleep, _test_write=_test_write)


# ------------------------------------------------------------------------------------------ serving


def serve(conn, tmp_dir: str, memory_limit: int, low_priority: bool) -> None:
    """The worker's main loop: restrict, say what took effect, then answer requests until the app hangs up."""
    from ._workers import set_low_priority

    set_low_priority(low_priority)
    applied = enter_sandbox(tmp_dir, memory_limit)
    os.environ["WICK_IN_SANDBOX"] = "1"
    conn.send_bytes(json.dumps({"ready": True, "applied": applied}).encode())
    while True:
        try:
            request = json.loads(conn.recv_bytes())
        except (EOFError, OSError):
            return
        try:
            header, payload = FUNCTIONS[request["fn"]](*request["args"])
            conn.send_bytes(json.dumps({"ok": True, **header, "bytes": len(payload or b"")}).encode())
            if payload:
                conn.send_bytes(payload)
        except Exception as e:  # noqa: BLE001 - reported to the app; the worker carries on
            conn.send_bytes(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}).encode())
