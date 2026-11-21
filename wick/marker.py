"""A small "made by Wick" mark in JPEG and PNG files, so a file Wick already compressed is recognised wherever it
goes (copied, moved, History cleared) and isn't done again. Videos and audio carry a WICK tag instead.

JPEG: a comment segment ("Wick 0.2.0") after the APPn segments. PNG: a tEXt chunk with the keyword "Wick" right
after the header. Neither changes a pixel or anything about how the picture is shown.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

KEY = b"Wick"


def _text() -> bytes:
    from . import __version__

    return KEY + b" " + __version__.encode()


def has(path: Path) -> bool:
    """Does this JPEG or PNG carry the mark? Reads only the first segments or chunks."""
    try:
        with open(path, "rb") as f:
            head = f.read(2)
            if head == b"\xff\xd8":
                return _jpeg_has(f)
            if head + f.read(6) == b"\x89PNG\r\n\x1a\n":
                return _png_has(f)
    except OSError:
        pass
    return False


def _jpeg_has(f) -> bool:
    for _ in range(32):
        marker = f.read(2)
        if len(marker) < 2 or marker[0] != 0xFF or marker[1] in (0xDA, 0xD9):   # (start of scan: no more headers)
            return False
        size = struct.unpack(">H", f.read(2))[0]
        if marker[1] == 0xFE:
            return f.read(size - 2).startswith(KEY + b" ")
        f.seek(size - 2, 1)
    return False


def _png_has(f) -> bool:
    for _ in range(16):
        hdr = f.read(8)
        if len(hdr) < 8:
            return False
        length, kind = struct.unpack(">I4s", hdr)
        if kind == b"IDAT":
            return False
        data = f.read(length)
        f.seek(4, 1)
        if kind == b"tEXt" and data.split(b"\0", 1)[0] == KEY:
            return True
    return False


def add(path: Path) -> bool:
    """Mark a JPEG or PNG in place (False: not one, or already marked)."""
    data = path.read_bytes()
    if has(path):
        return False
    if data[:2] == b"\xff\xd8":
        new = _jpeg_add(data)
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        new = _png_add(data)
    else:
        return False
    if new is None:
        return False
    path.write_bytes(new)
    return True


def _jpeg_add(data: bytes) -> bytes | None:
    from . import jpegparts

    pos = 2
    while pos + 4 <= len(data) and data[pos] == 0xFF and 0xE0 <= data[pos + 1] <= 0xEF:     # past APP0..APP15
        if data[pos + 1] == 0xE2 and data[pos + 4:pos + 8] == b"MPF\0":
            break           # (before the MPF index: the pictures it points to move with it, so offsets hold)
        pos += 2 + struct.unpack(">H", data[pos + 2:pos + 4])[0]
    if pos + 2 > len(data) or data[pos] != 0xFF:
        return None
    text = _text()
    comment = b"\xff\xfe" + struct.pack(">H", len(text) + 2) + text
    new = data[:pos] + comment + data[pos:]
    return jpegparts.grow_main_picture(new, len(comment))


def _png_add(data: bytes) -> bytes | None:
    length = struct.unpack(">I", data[8:12])[0]
    if data[12:16] != b"IHDR":
        return None
    end = 8 + 12 + length                                   # (signature, then IHDR: length, type, data, CRC)
    body = b"tEXt" + KEY + b"\0" + _text()[len(KEY) + 1:]
    chunk = struct.pack(">I", len(body) - 4) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    return data[:end] + chunk + data[end:]


__all__ = ["has", "add", "KEY"]
