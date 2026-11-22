"""macOS Trash: move with Finder's own call, and remember where each item went, so it can be put back.

macOS keeps an item's original location in Finder's private .DS_Store data, which apps can't read. So Wick asks
NSFileManager to trash the item (Finder's Put Back keeps working, and a name already in the Trash is handled the
way Finder does it), which answers with where the item ended up, and records that next to its original path in
trash-macos.json in Wick's data folder. Undo and "free just these from the Trash" look there.

NSFileManager is reached through the Objective-C runtime with ctypes: nothing extra to install.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_objc = None


def _runtime():
    global _objc
    if _objc is None:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
        _objc = objc
    return _objc


def _send(restype, *argtypes):
    """objc_msgSend with exact argument types (needed on Apple silicon, where it isn't variadic)."""
    objc = _runtime()
    addr = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(addr)


def _sel(name: str):
    return _runtime().sel_registerName(name.encode())


def _cls(name: str):
    return _runtime().objc_getClass(name.encode())


def trash_item(path: Path) -> Path:
    """Move ``path`` to the Trash; returns where it is now. Raises OSError if macOS refused."""
    objc = _runtime()
    pool = objc.objc_autoreleasePoolPush()
    try:
        string = _send(ctypes.c_void_p, ctypes.c_char_p)(_cls("NSString"), _sel("stringWithUTF8String:"),
                                                          os.fsencode(os.path.abspath(path)))
        url = _send(ctypes.c_void_p, ctypes.c_void_p)(_cls("NSURL"), _sel("fileURLWithPath:"), string)
        manager = _send(ctypes.c_void_p)(_cls("NSFileManager"), _sel("defaultManager"))
        result = ctypes.c_void_p()
        error = ctypes.c_void_p()
        ok = _send(ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p))(
            manager, _sel("trashItemAtURL:resultingItemURL:error:"), url, ctypes.byref(result), ctypes.byref(error))
        if not ok or not result.value:
            why = ""
            if error.value:
                text = _send(ctypes.c_void_p)(error.value, _sel("localizedDescription"))
                why = (_send(ctypes.c_char_p)(text, _sel("UTF8String")) or b"").decode("utf-8", "replace")
            raise OSError(f"macOS couldn't move it to the Trash{': ' + why if why else ''}")
        where = _send(ctypes.c_void_p)(result.value, _sel("path"))
        out = _send(ctypes.c_char_p)(where, _sel("UTF8String"))
        return Path(os.fsdecode(out))
    finally:
        objc.objc_autoreleasePoolPop(pool)


# ---------------------------------------------------------------- what Wick put in the Trash

def _record_file() -> Path:
    from .journal import data_dir

    return data_dir() / "trash-macos.json"


def _load() -> list[dict]:
    try:
        data = json.loads(_record_file().read_text("utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(entries: list[dict]) -> None:
    path = _record_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


def move_to_trash(path: Path) -> Path:
    """Trash ``path`` and remember where it went."""
    original = os.path.abspath(path)
    with _lock:
        where = trash_item(Path(original))
        # Items emptied from the Trash since are forgotten as the list is saved.
        entries = [e for e in _load() if os.path.lexists(e.get("trashed", ""))]
        entries.append({"original": original, "trashed": str(where), "at": time.time()})
        _save(entries)
    return where


def entries() -> list[tuple[Path, float, Path]]:
    """(original, when, where it is in the Trash) for every item Wick put there that's still there."""
    with _lock:
        found = _load()
    return [(Path(e["original"]), float(e.get("at", 0)), Path(e["trashed"])) for e in found
            if isinstance(e, dict) and "original" in e and "trashed" in e and os.path.lexists(e["trashed"])]


def forget(trashed: Path) -> None:
    """The item left the Trash (put back or deleted): drop its record."""
    with _lock:
        _save([e for e in _load() if e.get("trashed") != str(trashed)])


__all__ = ["move_to_trash", "trash_item", "entries", "forget"]
