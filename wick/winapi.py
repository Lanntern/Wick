"""Windows calls used in several places: starting a program the way Explorer does (as administrator when asked)
and waiting for it, and job objects that put limits on processes. Every argument and result type is declared: a
handle passed to ctypes as a plain int overflows its default C int on 64-bit Windows."""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes  # (importable everywhere; the calls themselves are Windows-only)

WAIT_TIMEOUT = 0x102
ERROR_CANCELLED = 1223          # the person said no to Windows' permission prompt


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


def run_and_wait(program: str, params: str = "", *, admin: bool = False, hidden: bool = False,
                 cancel: threading.Event | None = None, kill_on_cancel: bool = False,
                 timeout: float | None = None) -> int | None:
    """Start ``program params`` the way Explorer would (as administrator with ``admin``: Windows asks first) and
    wait for it. Returns its exit code, or None if it was stopped first (``cancel`` set, or ``timeout`` seconds
    passed; with ``kill_on_cancel`` it is ended, otherwise left to finish by itself).
    Raises OSError if it couldn't start (``winerror`` ERROR_CANCELLED: the person said no)."""
    shell = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    k32 = _kernel32()
    mask = 0x40 | (0x400 if hidden else 0)          # SEE_MASK_NOCLOSEPROCESS (| SEE_MASK_FLAG_NO_UI)
    info = _ShellExecuteInfo(cbSize=ctypes.sizeof(_ShellExecuteInfo), fMask=mask,
                             lpVerb="runas" if admin else "open", lpFile=program, lpParameters=params or None,
                             nShow=0 if hidden else 1)
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()  # type: ignore[attr-defined]
        raise OSError(err, f"{program} didn't start" + (" (permission was refused)" if err == ERROR_CANCELLED
                                                         else ""))
    if not info.hProcess:            # (handed over to a program that was already running: nothing to wait for)
        return 0
    end = None if timeout is None else time.monotonic() + timeout
    try:
        while k32.WaitForSingleObject(info.hProcess, 200) == WAIT_TIMEOUT:
            if (cancel is not None and cancel.is_set()) or (end is not None and time.monotonic() > end):
                if kill_on_cancel:
                    k32.TerminateProcess(info.hProcess, 1)
                    time.sleep(0.2)
                return None
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        k32.CloseHandle(info.hProcess)


# ---------------------------------------------------------------- job objects

class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


KILL_ON_JOB_CLOSE = 0x2000
BREAKAWAY_OK = 0x800
ACTIVE_PROCESS = 0x8
DIE_ON_UNHANDLED_EXCEPTION = 0x400
PROCESS_MEMORY = 0x100


def job_kernel32():
    """kernel32 with the job-object calls typed."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetCurrentProcess.argtypes = []
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    return k32


def job_object(flags: int, memory_limit: int = 0, active_processes: int = 0) -> int:
    """A new job object with these limit flags (plus a per-process memory limit in bytes, and a cap on how many
    processes it may hold, when given). Raises OSError if Windows refuses."""
    k32 = job_kernel32()
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObject failed")  # type: ignore[attr-defined]
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = flags
    if active_processes:
        info.BasicLimitInformation.LimitFlags |= ACTIVE_PROCESS
        info.BasicLimitInformation.ActiveProcessLimit = active_processes
    if memory_limit:
        info.BasicLimitInformation.LimitFlags |= PROCESS_MEMORY
        info.ProcessMemoryLimit = memory_limit
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):   # extended limits
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject (limits) failed")  # type: ignore[attr-defined]
    return job


__all__ = ["run_and_wait", "job_object", "job_kernel32", "ERROR_CANCELLED", "KILL_ON_JOB_CLOSE", "BREAKAWAY_OK",
           "DIE_ON_UNHANDLED_EXCEPTION"]
