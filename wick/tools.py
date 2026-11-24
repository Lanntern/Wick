"""Locating and running the external command-line encoders.

Search order for each tool:
1. ``$WICK_TOOLS_DIR``
2. ``<app dir>/tools`` (next to the .exe when packaged, repo root in dev)
3. the bundled PyInstaller data dir
4. ``PATH``
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from functools import cache, lru_cache
from pathlib import Path

TOOL_NAMES = ("ffmpeg", "ffprobe", "cjxl", "djxl", "cjpegli", "gifsicle", "ssimulacra2", "butteraugli_main")
# Optional: cjpegli (better lossy JPEG; else Pillow + mozjpeg), ssimulacra2 (Smart quality; else SSIM).
OPTIONAL_TOOLS = ("cjpegli", "ssimulacra2", "butteraugli_main")

# Set by the engine from Settings.low_priority.
LOW_PRIORITY = False
_BELOW_NORMAL = 0x00004000  # Windows BELOW_NORMAL_PRIORITY_CLASS

# Hide the console window that would otherwise flash for every subprocess
# when running as a GUI app on Windows.
_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _popen_kwargs() -> dict:
    flags = _CREATIONFLAGS
    kw: dict = {}
    if LOW_PRIORITY:
        if os.name == "nt":
            flags |= _BELOW_NORMAL
        else:
            kw["preexec_fn"] = _nice
    kw["creationflags"] = flags
    return kw


# ---------------------------------------------------------------- Windows: tidy, considerate child processes

_job = None              # a job object that closes every encoder when Wick exits, even after a crash
_job_lock = threading.Lock()


def _windows_job():
    """The job object (created on first use): JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, so when the last handle to it
    closes (Wick exits, crashes or is killed) Windows ends every encoder still in it, instead of leaving
    ffmpeg running orphaned in the background."""
    global _job
    with _job_lock:
        if _job is None:
            from . import winapi

            # Each encoder may use at most 3/4 of the computer's memory (at least 2 GB): far above any normal
            # encode, but a damaged or malicious file can't make one swallow it all and freeze the computer;
            # that file fails instead (its original kept).
            try:
                _job = winapi.job_object(winapi.KILL_ON_JOB_CLOSE | winapi.BREAKAWAY_OK, encoder_memory_limit())
            except OSError:
                _job = 0
        return _job


def encoder_memory_limit() -> int:
    """Bytes one encoder process may commit (0: no limit, when the computer's memory is unknown)."""
    total = total_memory_gb()
    return int(max(2.0, total * 0.75) * 1024 ** 3) if total else 0


def _considerate(proc: subprocess.Popen) -> None:
    """Windows: put a just-started encoder in the job object, and in low-priority mode also give it low disk
    and memory priority (so the computer stays responsive while it works), plus Windows' efficiency mode while
    Wick runs hidden in the tray (power.set_efficiency_mode). Best effort: never stops an encode."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        handle = wintypes.HANDLE(int(proc._handle))  # type: ignore[attr-defined]
        k = ctypes.windll.kernel32  # type: ignore[attr-defined]
        job = _windows_job()
        if job:
            k.AssignProcessToJobObject(wintypes.HANDLE(job), handle)
        if not LOW_PRIORITY:
            return
        low = wintypes.ULONG(2)                                              # MEMORY_PRIORITY_LOW
        k.SetProcessInformation(handle, 0, ctypes.byref(low), ctypes.sizeof(low))   # ProcessMemoryPriority
        io_low = wintypes.ULONG(1)                                           # IoPriorityLow
        ctypes.windll.ntdll.NtSetInformationProcess(  # type: ignore[attr-defined]
            handle, 33, ctypes.byref(io_low), ctypes.sizeof(io_low))         # ProcessIoPriority
        from . import power

        if power.efficiency_mode():
            class Throttle(ctypes.Structure):
                _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                            ("StateMask", wintypes.ULONG)]

            eco = Throttle(1, 0x1, 0x1)                     # PROCESS_POWER_THROTTLING_EXECUTION_SPEED: on
            k.SetProcessInformation(handle, 4, ctypes.byref(eco), ctypes.sizeof(eco))   # ProcessPowerThrottling
    except Exception:  # noqa: BLE001
        from wick.log import ignored
        ignored("tools._considerate")


def _nice() -> None:  # pragma: no cover - runs in the child
    try:
        os.nice(10)
    except OSError:
        pass


@lru_cache(maxsize=1)
def total_memory_gb() -> float:
    """Installed memory in GB (0 if unknown)."""
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MemoryStatus()
            st.dwLength = ctypes.sizeof(st)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
            return st.ullTotalPhys / 1024 ** 3
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (OSError, ValueError, AttributeError):
        return 0.0


def lower_thread_priority() -> None:
    """Make the calling thread (in-process encoders) run at below-normal priority."""
    try:
        if os.name == "nt":
            import ctypes

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            k32.SetThreadPriority(k32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
        elif sys.platform.startswith("linux") and hasattr(os, "setpriority"):
            # (Linux only: there a thread's id works as a process id. On macOS it doesn't, and could name some
            # other process; the encoders' own processes are already started at low priority there.)
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except Exception:  # noqa: BLE001
        from wick.log import ignored
        ignored("tools.lower_thread_priority")


class ToolMissing(RuntimeError):
    def __init__(self, name: str):
        super().__init__(
            f"'{name}' was not found. Put it in the 'tools' folder next to "
            f"Wick or on your PATH (see README)."
        )
        self.name = name


class ToolError(RuntimeError):
    def __init__(self, args: Sequence[str], returncode: int, stderr: str):
        tool = Path(str(args[0])).stem
        detail = (stderr or "").strip().splitlines()
        tail = " | ".join(detail[-3:]) if detail else "no error output"
        super().__init__(f"{tool} failed (exit {returncode}): {tail}")
        self.returncode = returncode
        self.stderr = stderr


class Cancelled(Exception):
    """Raised when the user cancels while a tool is running."""


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _candidate_dirs() -> Iterator[Path]:
    env = os.environ.get("WICK_TOOLS_DIR")
    if env:
        yield Path(env)
    yield app_dir() / "tools"
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        yield Path(meipass) / "tools"
    if os.name == "nt":                    # tools downloaded when first needed (addons.py)
        from .journal import data_dir

        yield data_dir() / "tools"
    if sys.platform == "darwin":
        # Homebrew (Apple silicon, then Intel) and MacPorts: an app opened from Finder doesn't get the shell's PATH.
        yield from (Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin"))


@cache
def find_tool(name: str) -> str | None:
    exe = name + (".exe" if os.name == "nt" else "")
    for d in _candidate_dirs():
        p = d / exe
        if p.is_file():
            return str(p)
    return shutil.which(name)


def require(name: str) -> str:
    path = find_tool(name)
    if not path:
        raise ToolMissing(name)
    return path


def tool_status() -> dict[str, str | None]:
    return {name: find_tool(name) for name in TOOL_NAMES}


_cancel_context = threading.local()


@contextmanager
def cancelling(cancel: threading.Event):
    """Make nested external tools cancellable on this thread (including preview image tools)."""
    previous = getattr(_cancel_context, "event", None)
    _cancel_context.event = cancel
    try:
        yield
    finally:
        _cancel_context.event = previous


SLOW_RUN = 2.0       # seconds: a tool run taking longer is logged even when it succeeds


def _log_run(cmd: list[str], seconds: float, code: int | None, stderr: str | bytes | None) -> None:
    """One log line per tool run that's worth reading later: failed (with its arguments and the end of what it
    said) or slow. Quick successful runs (most of them, hundreds per scan) aren't logged: they used to fill the
    log and push out what came before a problem."""
    from .log import log

    name = Path(cmd[0]).name
    if code not in (0, None):
        said = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")
        tail = "\n".join(line for line in said.strip().splitlines()[-12:])
        log.info("%s exited with %s after %.1fs\n  command: %s%s", name, code, seconds,
                 subprocess.list2cmdline(cmd[1:])[:2000], f"\n  said:\n{tail}" if tail else "")
    elif seconds >= SLOW_RUN:
        log.info("%s took %.1fs", name, seconds)


def run(
    args: Sequence[str | os.PathLike],
    *,
    cwd: str | os.PathLike | None = None,
    timeout: float | None = None,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    from .log import log

    cancel = getattr(_cancel_context, "event", None)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    cmd = [str(a) for a in args]
    started = time.monotonic()
    log.debug("Starting tool: %s", cmd)
    with subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        stdin=subprocess.DEVNULL,
        **_popen_kwargs(),
    ) as p:
        _considerate(p)
        try:
            if cancel is None:
                out, err = p.communicate(timeout=timeout)
            else:
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    remaining = None if timeout is None else timeout - (time.monotonic() - started)
                    if timeout is not None and remaining is not None and remaining <= 0:
                        raise subprocess.TimeoutExpired(cmd, timeout)
                    try:
                        out, err = p.communicate(timeout=min(0.1, remaining) if remaining is not None else 0.1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
        except BaseException:                       # (timeout, Ctrl+C: as subprocess.run does)
            p.kill()
            p.communicate()
            raise
    proc = subprocess.CompletedProcess(cmd, p.returncode, out, err)
    _log_run(cmd, time.monotonic() - started, p.returncode, err)
    if check and proc.returncode != 0:
        stderr = proc.stderr if text else proc.stderr.decode("utf-8", "replace")
        raise ToolError([str(a) for a in args], proc.returncode, stderr)
    return proc


STALL_AFTER_ERROR = 60.0      # seconds without progress after FFmpeg printed an error: it's stuck
STALL_SILENT = 1800.0         # seconds without any progress at all


def _has_output(f) -> bool:
    try:
        return os.fstat(f.fileno()).st_size > 0
    except OSError:
        return False


def run_ffmpeg_progress(
    args: Sequence[str | os.PathLike],
    duration: float | None,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Run ffmpeg with ``-progress pipe:1`` already in ``args``.

    Calls ``on_progress(fraction)`` and kills ffmpeg if ``cancel`` is set.
    """
    cancel = cancel if cancel is not None else getattr(_cancel_context, "event", None)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    started = time.monotonic()
    with tempfile.TemporaryFile() as errf:
        proc = subprocess.Popen(
            [str(a) for a in args],
            stdout=subprocess.PIPE,
            stderr=errf,
            stdin=subprocess.DEVNULL,
            **_popen_kwargs(),
        )
        _considerate(proc)
        cancelled = False
        finished = threading.Event()
        last_output = [time.monotonic()]
        stalled = [False]

        def watch() -> None:
            # Reading stdout can block indefinitely when an encoder hangs without printing a line: cancelling
            # kills it, and so does a stall (FFmpeg sometimes reports an error and then never exits).
            while not finished.wait(0.1):
                if cancel is not None and cancel.is_set():
                    try:
                        proc.kill()
                    except OSError:
                        pass    # already exited
                    return
                quiet = time.monotonic() - last_output[0]
                if quiet > STALL_AFTER_ERROR and _has_output(errf) or quiet > STALL_SILENT:
                    stalled[0] = True
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    return

        threading.Thread(target=watch, daemon=True, name="encoder-watch").start()
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                last_output[0] = time.monotonic()
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    proc.kill()
                    break
                line = raw.decode("ascii", "replace").strip()
                if on_progress and duration and line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    on_progress(max(0.0, min(1.0, us / 1e6 / duration)))
        except BaseException:
            proc.kill()     # nobody reads its output any more: waiting below would hang once the pipe is full
            raise
        finally:
            finished.set()
            proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()
        if cancelled or (cancel is not None and cancel.is_set()):
            raise Cancelled()
        if proc.returncode != 0:
            errf.seek(0)
            said = errf.read().decode("utf-8", "replace")
            if stalled[0]:
                said += "\n(FFmpeg stopped responding" + (" after this error)" if said.strip() else ")")
            _log_run([str(a) for a in args], time.monotonic() - started, proc.returncode, said)
            raise ToolError([str(a) for a in args], proc.returncode, said)
        _log_run([str(a) for a in args], time.monotonic() - started, 0, None)
