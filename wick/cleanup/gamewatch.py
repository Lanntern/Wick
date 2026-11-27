"""Recursive Windows change notifications for tracked folders; no file contents are read."""

import ctypes
import sys
import threading
import time
from pathlib import Path

from ..log import log


class GameWatcher:
    def __init__(self, folders):
        self.folders = tuple(sorted({str(Path(p).resolve()) for p in folders}))
        self.changed = threading.Event()
        self.cancel = threading.Event()
        self.last_change = 0.0
        self.failures = []
        self.thread = None
        if sys.platform == "win32" and self.folders:
            self.thread = threading.Thread(target=self.run, daemon=True, name="game-update-watch")
            self.thread.start()

    def run(self):
        if sys.platform != "win32":
            return
        from ctypes import wintypes


        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        first = k32.FindFirstChangeNotificationW
        first.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
        first.restype = wintypes.HANDLE
        wait = k32.WaitForSingleObject
        wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        wait.restype = wintypes.DWORD
        advance = k32.FindNextChangeNotification
        advance.argtypes = [wintypes.HANDLE]
        close = k32.FindCloseChangeNotification
        close.argtypes = [wintypes.HANDLE]
        handles = []
        try:
            for folder in self.folders:
                handle = first(folder, True, 0x1 | 0x2 | 0x4 | 0x8 | 0x10)
                if handle in (None, ctypes.c_void_p(-1).value):
                    self.failures.append(folder)
                    log.info("Using periodic update checks for %s (change notifications unavailable)", folder)
                else:
                    handles.append(handle)
            while not self.cancel.wait(0.25):
                for handle in handles:
                    if wait(handle, 0) == 0:
                        self.last_change = time.monotonic()
                        self.changed.set()
                        advance(handle)
        except Exception:  # noqa: BLE001 - logged; the periodic checks carry on
            log.exception("Game update notifications stopped; periodic checks remain enabled")
        finally:
            for handle in handles:
                close(handle)

    def stop(self):
        self.cancel.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
