"""A small bridge to macOS's Objective-C frameworks through ctypes (nothing to install): enough for the Trash,
the Dock tile and App Nap. Every call names its exact argument types, which Apple silicon requires.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os

_objc = None


def runtime():
    global _objc
    if _objc is None:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")
        for framework in ("Foundation", "AppKit"):
            ctypes.cdll.LoadLibrary(f"/System/Library/Frameworks/{framework}.framework/{framework}")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
        _objc = objc
    return _objc


def send(restype, *argtypes):
    """objc_msgSend typed for one call: send(ret, argtypes...)(receiver, selector, args...)."""
    addr = ctypes.cast(runtime().objc_msgSend, ctypes.c_void_p).value
    if addr is None:
        raise OSError("The Objective-C runtime has no objc_msgSend")
    return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(addr)


def sel(name: str):
    return runtime().sel_registerName(name.encode())


def cls(name: str):
    return runtime().objc_getClass(name.encode())


def nsstring(text: str):
    return send(ctypes.c_void_p, ctypes.c_char_p)(cls("NSString"), sel("stringWithUTF8String:"),
                                                  text.encode("utf-8"))


def nsstring_path(path) -> int:
    return send(ctypes.c_void_p, ctypes.c_char_p)(cls("NSString"), sel("stringWithUTF8String:"),
                                                  os.fsencode(os.path.abspath(path)))


def text_of(nsstring_obj) -> str:
    raw = send(ctypes.c_char_p)(nsstring_obj, sel("UTF8String")) if nsstring_obj else None
    return (raw or b"").decode("utf-8", "replace")


class Pool:
    """An autorelease pool for the objects made in a block."""

    def __enter__(self):
        self.token = runtime().objc_autoreleasePoolPush()
        return self

    def __exit__(self, *exc):
        runtime().objc_autoreleasePoolPop(self.token)
        return False


__all__ = ["runtime", "send", "sel", "cls", "nsstring", "nsstring_path", "text_of", "Pool"]
