"""Decoding files Wick didn't make, in helper processes with fewer rights (the worker is _decoders.py).

Thumbnails, the preview window's pictures, Broken Files and Similar Photos open whatever is on the disk: a
file crafted to exploit a bug in an image or video decoder would otherwise run with everything Wick can do.
In a worker it can at most read files; and a decoder that crashes or hangs takes down a worker, never Wick.

Each call borrows one worker (started when needed, kept for the next call). A worker that crashes, times out or
answers with anything unexpected is ended and replaced. If workers can't start at all (a locked-down PC), the
work is done in Wick itself, as before, and the log says so once.

Nothing a worker sends is unpickled: replies are JSON with plain values, plus raw pixels whose size must match
what the header says.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from .log import log

START_TIMEOUT = 30.0          # a new worker must be ready within this (antivirus scans the first start)
MAX_IDLE = 8                  # workers kept for later calls
MAX_PIXELS = 64_000_000       # largest picture a worker may send (the preview's, shrunk beyond this)


class DecoderFailed(Exception):
    """The worker crashed, timed out, sent something malformed or couldn't open the file."""


class DecoderCrashed(DecoderFailed):
    """The worker stopped or hung while working on the file (the file made a decoder fail)."""


_lock = threading.Lock()
_idle: list[_Worker] = []
_all: set[_Worker] = set()
_unavailable: str | None = None     # why workers can't be used (then decoding happens in Wick)
_started: list[bool] = []


def tmp_dir() -> str:
    """Where workers may write (decoders' temporary files). On Windows a low-integrity process can only write to
    LocalLow, so their folder is there."""
    if sys.platform == "win32":
        base = Path(os.environ.get("USERPROFILE") or Path.home()) / "AppData" / "LocalLow"
        return str(base / "Wick" / "decode")

    return str(Path(tempfile.gettempdir()) / "wick-decode")


class _Worker:
    def __init__(self) -> None:
        import multiprocessing

        from . import _decoders, tools

        ctx = multiprocessing.get_context("spawn")
        self.conn, child = ctx.Pipe()
        folder = tmp_dir()
        os.makedirs(folder, exist_ok=True)
        self.proc = ctx.Process(target=_decoders.serve, daemon=True, name="wick-decoder",
                                args=(child, folder, tools.encoder_memory_limit(), tools.LOW_PRIORITY))
        self.proc.start()
        child.close()
        if not self.conn.poll(START_TIMEOUT):
            self.kill()
            raise DecoderFailed("the decoder worker didn't start in time")
        try:
            hello = json.loads(self.conn.recv_bytes(4096))
        except (EOFError, OSError, ValueError) as e:
            self.kill()
            raise DecoderFailed(f"the decoder worker didn't start ({e})") from None
        self.applied = hello.get("applied") if isinstance(hello, dict) else None
        if not isinstance(self.applied, dict):
            self.kill()
            raise DecoderFailed("the decoder worker didn't start properly")
        if "error" in self.applied:
            # It couldn't give up its rights: don't decode in it (it would be no safer than Wick, and pretend).
            self.kill()
            raise DecoderFailed(f"the decoder worker couldn't restrict itself ({self.applied['error']})")

    def ask(self, fn: str, args: list, timeout: float, max_bytes: int) -> tuple[dict, bytes]:
        self.conn.send_bytes(json.dumps({"fn": fn, "args": args}).encode())
        if not self.conn.poll(timeout):
            raise DecoderCrashed(f"took longer than {timeout:.0f} s")
        header = json.loads(self.conn.recv_bytes(64 * 1024))
        if not isinstance(header, dict):
            raise DecoderFailed("malformed reply")
        size = header.get("bytes", 0)
        if not isinstance(size, int) or size < 0 or size > max_bytes:
            raise DecoderFailed("reply too large")
        payload = b""
        if size:
            if not self.conn.poll(timeout):
                raise DecoderCrashed(f"took longer than {timeout:.0f} s")
            payload = self.conn.recv_bytes(max_bytes)
            if len(payload) != size:
                raise DecoderFailed("reply doesn't match its size")
        return header, payload

    def kill(self) -> None:
        try:
            self.conn.close()
        except OSError:
            pass
        if self.proc.is_alive():
            self.proc.kill()
        self.proc.join(5)


def _borrow() -> _Worker | None:
    """An idle worker, or a new one; None if workers can't be started here (logged once)."""
    global _unavailable
    with _lock:
        if _idle:
            return _idle.pop()
    try:
        w = _Worker()
    except Exception as e:  # noqa: BLE001 - (can't start processes, or can't restrict them)
        from .log import log

        with _lock:
            if _unavailable is None:
                _unavailable = str(e)
                log.warning("Decoding files in Wick itself: separate decoder processes aren't available (%s)", e)
        return None
    with _lock:
        if not _started:
            from .log import log

            log.info("Decoder workers restricted: %s", w.applied)
            atexit.register(shutdown)
            _started.append(True)
        _all.add(w)
    return w


def _give_back(w: _Worker) -> None:
    with _lock:
        if len(_idle) < MAX_IDLE:
            _idle.append(w)
            return
        _all.discard(w)
    w.kill()


def _discard(w: _Worker) -> None:
    with _lock:
        _all.discard(w)
    w.kill()


def available() -> bool:
    return _unavailable is None and not os.environ.get("WICK_NO_SANDBOX")


def call(fn: str, *args, timeout: float = 120.0, max_bytes: int = 0) -> tuple[dict, bytes]:
    """Run ``_decoders.<fn>(*args)`` in a worker: (header, raw bytes). Raises DecoderFailed if the worker crashed,
    timed out, replied with something malformed, or the function failed. Where workers aren't available it runs
    here instead (same reply)."""
    from . import _decoders

    if not available():
        try:
            header, payload = _decoders.FUNCTIONS[fn](*args)
        except Exception as e:  # noqa: BLE001 - the same as a worker's failure
            raise DecoderFailed(f"{type(e).__name__}: {e}") from None
        return {"ok": True, **header}, payload or b""
    w = _borrow()
    if w is None:          # (decode here: available() now says so)
        return call(fn, *args, timeout=timeout, max_bytes=max_bytes)
    try:
        header, payload = w.ask(fn, list(args), timeout, max_bytes)
    except (DecoderFailed, EOFError, OSError, ValueError) as e:
        if isinstance(e, DecoderFailed):
            failure = e
        elif isinstance(e, (EOFError, OSError)):
            failure = DecoderCrashed(f"the decoder stopped ({type(e).__name__})")
        else:
            failure = DecoderFailed(f"malformed reply ({type(e).__name__})")
        _discard(w)                          # (crashed, hung or misbehaved: never reused)
        _log_failure(fn, args, failure, w.proc.exitcode)
        raise failure from None
    _give_back(w)
    if not header.get("ok"):
        raise DecoderFailed(str(header.get("error", "failed"))[:300])
    return header, payload


def _log_failure(fn: str, args: tuple, failure: DecoderFailed, exitcode: int | None) -> None:
    """A worker that crashed, hung or misbehaved: which file, doing what, and how it ended (an exit code like
    0xC0000005, an access violation, points at a decoder bug; repeated ones at a crafted file)."""

    path = next((a for a in args if isinstance(a, str) and ("/" in a or "\\" in a)), "")
    # (Windows reports a crash as a large status like 0xC0000005; elsewhere -N means signal N.)
    code = f"{exitcode & 0xFFFFFFFF:#010x}" if isinstance(exitcode, int) and abs(exitcode) > 255 else str(exitcode)
    log.warning("Decoder worker %s while running %s on %s (exit code %s)",
                "hung or stopped" if isinstance(failure, DecoderCrashed) else "misbehaved", fn, path or "?", code)


def prewarm() -> None:
    """Start one worker in the background, so the first thumbnail or preview doesn't wait for it (starting
    one takes about a second on Windows, more while antivirus scans it)."""
    if not available():
        return

    def start() -> None:
        w = _borrow()             # (None: logged once in _borrow, and files are decoded in Wick itself)
        if w is not None:
            _give_back(w)

    threading.Thread(target=start, name="decoder-prewarm", daemon=True).start()


def _size(header: dict, channels: int, payload: bytes) -> tuple[int, int] | None:
    w, h = header.get("w"), header.get("h")
    if not (isinstance(w, int) and isinstance(h, int)) or w <= 0 or h <= 0 or w * h > MAX_PIXELS:
        return None
    if len(payload) != w * h * channels:
        raise DecoderFailed("reply doesn't match its size")
    return w, h


# ------------------------------------------------------------------------------------------ what's asked for


def broken_check(path: Path, size: int, thorough: bool) -> tuple[str, str] | None:
    """cleanup.broken.check_file, in a worker. A worker that crashed or hung on the file says it's damaged (it
    made a decoder fail); one that couldn't finish for other reasons says nothing."""
    try:
        header, _ = call("broken_check", str(path), int(size), bool(thorough),
                         timeout=3700 if thorough else 300)
    except DecoderCrashed:
        from .cleanup.broken import DAMAGED

        return DAMAGED, "Opening it made the decoder stop (damaged data, or made to exploit decoders)"
    except DecoderFailed:
        return None
    found = header.get("result")
    if isinstance(found, list) and len(found) == 2 and all(isinstance(x, str) for x in found):
        return found[0][:20], found[1][:200]
    return None


def svg_same(a: Path, b: Path) -> bool:
    """Do both SVGs draw the same (checked in the worker)? False when it can't be told."""
    try:
        header, _ = call("svg_same", str(a), str(b), timeout=120)
    except DecoderFailed:
        return False
    return header.get("result") is True


def similar_fingerprint(path: Path) -> tuple[int, int, int] | None:
    try:
        header, _ = call("similar_fingerprint", str(path), timeout=120)
    except DecoderFailed:
        return None
    found = header.get("result")
    if isinstance(found, list) and len(found) == 3 and all(isinstance(x, int) for x in found):
        return found[0], found[1], found[2]
    return None


def similar_inspect(path: Path, px: int = 256):
    """(sharpness, px×px grayscale Pillow image or None), from a worker."""
    from PIL import Image

    try:
        header, payload = call("similar_inspect", str(path), int(px), timeout=120, max_bytes=px * px)
        sharp = header.get("sharpness", 0.0)
        size = _size(header, 1, payload)
    except DecoderFailed:
        return 0.0, None
    if not isinstance(sharp, (int, float)) or size is None:
        return 0.0, None
    return float(sharp), Image.frombytes("L", size, payload)


def rgba(fn: str, *args, max_pixels: int, timeout: float) -> tuple[int, int, bytes] | None:
    """(width, height, RGBA bytes) from a worker's picture, or None if it couldn't make one."""
    try:
        header, payload = call(fn, *args, timeout=timeout, max_bytes=max_pixels * 4)
        size = _size(header, 4, payload)
    except DecoderFailed:
        return None
    return (size[0], size[1], payload) if size else None


def shutdown() -> None:
    with _lock:
        workers = list(_all)
        _all.clear()
        _idle.clear()
    for w in workers:
        w.kill()


__all__ = ["DecoderFailed", "DecoderCrashed", "available", "call", "prewarm", "broken_check", "similar_fingerprint",
           "similar_inspect", "rgba", "shutdown", "tmp_dir"]
