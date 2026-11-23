"""What a JPEG carries besides its picture, and how to keep (or leave out) it when the picture is rewritten.

* **Extra pictures after the main one** (MPF, CIPA DC-007): Ultra HDR gain maps (Android 14+ phones), camera
  previews (a 1920 px copy of the photo), stereo and depth images. Encoders rewrite only the main picture, so
  everything after it used to be lost, an Ultra HDR photo's HDR included. ``rejoin`` puts it back after a
  rewritten main picture, with the MPF index's offsets and sizes corrected.
* **The EXIF thumbnail** (IFD1): a 160 px copy most viewers never show. ``exif_without_thumbnail``.
* **The colour profile**: when it's sRGB in disguise (a large file that gives the same colours), a compact sRGB
  profile does the same job. ``compact_icc``.
"""

from __future__ import annotations

import struct

SOI, EOI, SOS = 0xD8, 0xD9, 0xDA
MPF_PREVIEW_TYPES = (0x010001, 0x010002, 0x010003)       # large thumbnails: VGA, full HD, 4K
GAIN_MAP_MARKS = (b"hdrgm:", b'xmlns:hdrgm="', b"urn:iso:std:iso:ts:21496", b":aux:hdrgainmap")


def segments(data: bytes) -> list[tuple[int, int, int]]:
    """(marker, start, end) of each header segment, up to (not including) the first start of scan."""
    out = []
    pos = 2
    while pos + 4 <= len(data) and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker == 0xFF:                       # (fill byte)
            pos += 1
            continue
        if marker == SOS:
            break
        size = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        out.append((marker, pos, pos + 2 + size))
        pos += 2 + size
    return out


def primary_end(data: bytes) -> int:
    """Where the main picture ends (just after its EOI), following every scan of a progressive file."""
    if data[:2] != b"\xff\xd8":
        return len(data)
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return len(data)
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker == EOI:
            return pos + 2
        size = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        pos += 2 + size
        if marker != SOS:
            continue
        while pos < len(data) - 1:              # entropy-coded data: FF00 and restart markers belong to it
            nxt = data.find(b"\xff", pos)
            if nxt < 0 or nxt + 1 >= len(data):
                return len(data)
            b = data[nxt + 1]
            if b == 0x00 or 0xD0 <= b <= 0xD7 or b == 0xFF:
                pos = nxt + (2 if b != 0xFF else 1)
                continue
            pos = nxt
            break
    return len(data)


# ---------------------------------------------------------------- MPF

def _mpf(data: bytes):
    """(base, endian, [(type, size, offset, record position)]) of the MPF index, or None. Offsets in MPF count from
    ``base`` (the TIFF header right after "MPF\\0")."""
    for marker, start, _end in segments(data):
        if marker != 0xE2 or data[start + 4:start + 8] != b"MPF\0":
            continue
        base = start + 8
        endian = "<" if data[base:base + 2] == b"II" else ">"
        try:
            ifd = base + struct.unpack(endian + "I", data[base + 4:base + 8])[0]
            count = struct.unpack(endian + "H", data[ifd:ifd + 2])[0]
            entries_at, entries = None, 0
            for i in range(count):
                tag, _typ, n, value = struct.unpack(endian + "HHII", data[ifd + 2 + 12 * i:ifd + 14 + 12 * i])
                if tag == 0xB002:                # MP Entry: 16 bytes per picture
                    entries_at, entries = base + value, n // 16
            if entries_at is None:
                return None
            out = []
            for i in range(entries):
                rec = entries_at + 16 * i
                attr, size, offset = struct.unpack(endian + "III", data[rec:rec + 12])
                out.append((attr & 0xFFFFFF, size, offset, rec))
            return base, endian, out
        except struct.error:
            return None
    return None


def kind(head: bytes) -> str:
    """What follows the main picture, from the file's start (its header segments): "gainmap" (Ultra HDR or ISO
    21496 gain map), "previews" (only camera previews), "other" (stereo, depth...), or ""."""
    hdr = any(m in head for m in GAIN_MAP_MARKS)
    found = _mpf(head)
    if found is None:
        return "gainmap" if hdr else ""
    extras = found[2][1:]
    if not extras:
        return "gainmap" if hdr else ""
    if hdr:
        return "gainmap"
    if all(t in MPF_PREVIEW_TYPES for t, _s, _o, _r in extras):
        return "previews"
    return "other"


def rejoin(new_primary: bytes, original: bytes) -> bytes:
    """A rewritten main picture followed by the original's extra pictures, with MPF's offsets and sizes made
    right for the new main picture. ``new_primary`` must carry the original's MPF segment (as copied markers)."""
    old_end = primary_end(original)
    tail = original[old_end:]
    if not tail:
        return new_primary
    old, new = _mpf(original), _mpf(new_primary)
    if old is None or new is None or len(old[2]) != len(new[2]):
        return new_primary + tail               # (no index to correct: Google's lengths count from the end)
    old_base, _e, old_entries = old
    new_base, endian, new_entries = new
    out = bytearray(new_primary)
    for i, ((_t, _size, offset, _r), (_t2, _s2, _o2, rec)) in enumerate(zip(old_entries, new_entries, strict=True)):
        if i == 0:
            struct.pack_into(endian + "I", out, rec + 4, len(new_primary))
            continue
        where = old_base + offset - old_end                # (the picture's place in the tail)
        struct.pack_into(endian + "I", out, rec + 8, len(new_primary) + where - new_base)
    return bytes(out) + tail


def grow_main_picture(data: bytes, by: int) -> bytes:
    """``data`` with the MPF index's size for the main picture grown by ``by`` bytes (something was added to its
    header before the index)."""
    found = _mpf(data)
    if found is None:
        return data
    _base, endian, entries = found
    out = bytearray(data)
    struct.pack_into(endian + "I", out, entries[0][3] + 4, entries[0][1] + by)
    return bytes(out)


def with_mpf_from(new: bytes, original: bytes) -> bytes:
    """``new`` with the original's MPF segment put back after its other header segments, when an encoder didn't
    copy it (``rejoin`` then corrects its offsets)."""
    if _mpf(new) is not None:
        return new
    seg = next(((s, e) for m, s, e in segments(original)
                if m == 0xE2 and original[s + 4:s + 8] == b"MPF\0"), None)
    if seg is None:
        return new
    app = [e for m, _s, e in segments(new) if 0xE0 <= m <= 0xEF]
    at = max(app) if app else 2
    return new[:at] + original[seg[0]:seg[1]] + new[at:]


def without_extras(data: bytes) -> bytes:
    """The main picture alone, without its MPF segment (for files whose extras are only previews)."""
    end = primary_end(data)
    main = data[:end]
    for marker, start, stop in segments(main):
        if marker == 0xE2 and main[start + 4:start + 8] == b"MPF\0":
            return main[:start] + main[stop:]
    return main


# ---------------------------------------------------------------- EXIF thumbnail

def exif_without_thumbnail(tiff: bytes) -> bytes:
    """EXIF (the TIFF structure after "Exif\\0\\0") without its thumbnail (IFD1), when the thumbnail is the last
    thing in it and nothing else points past where it starts; otherwise unchanged."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return tiff
    e = "<" if tiff[:2] == b"II" else ">"
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
    try:
        ifd0 = struct.unpack(e + "I", tiff[4:8])[0]
        ends = [8]

        def walk(at: int, depth: int = 0) -> int:
            n = struct.unpack(e + "H", tiff[at:at + 2])[0]
            ends.append(at + 2 + 12 * n + 4)
            for i in range(n):
                tag, typ, count, value = struct.unpack(e + "HHII", tiff[at + 2 + 12 * i:at + 14 + 12 * i])
                size = sizes.get(typ, 1) * count
                if size > 4:
                    ends.append(value + size)
                if tag in (0x8769, 0x8825, 0xA005) and depth < 3:     # EXIF, GPS, interoperability
                    walk(value, depth + 1)
            return struct.unpack(e + "I", tiff[at + 2 + 12 * n:at + 6 + 12 * n])[0]

        ifd1 = walk(ifd0)
        if not ifd1 or ifd1 < max(ends):
            return tiff
        n0 = struct.unpack(e + "H", tiff[ifd0:ifd0 + 2])[0]
        link = ifd0 + 2 + 12 * n0
        out = bytearray(tiff[:ifd1])
        struct.pack_into(e + "I", out, link, 0)
        return bytes(out)
    except struct.error:
        return tiff


def strip_jpeg_thumbnail(data: bytes) -> bytes:
    """A JPEG with its EXIF segment's thumbnail left out (the rest of the file byte for byte)."""
    for marker, start, end in segments(data):
        if marker == 0xE1 and data[start + 4:start + 10] == b"Exif\0\0":
            tiff = data[start + 10:end]
            slim = exif_without_thumbnail(tiff)
            if len(slim) == len(tiff):
                return data
            body = b"Exif\0\0" + slim
            return data[:start] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + data[end:]
    return data


# ---------------------------------------------------------------- colour profile

def _compact_srgb() -> bytes:
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def compact_icc(icc: bytes | None) -> bytes | None:
    """A compact sRGB profile instead of ``icc`` when ``icc`` gives the same colours as sRGB (every one of 4,096
    test colours within one step) and the compact one is at least 1 KB smaller; else None (keep ``icc``)."""
    if not icc or len(icc) < len(_compact_srgb()) + 1024:
        return None
    import io

    import numpy as np
    from PIL import Image, ImageCms

    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if profile.profile.xcolor_space.strip() != "RGB":
            return None
        levels = np.linspace(0, 255, 16).astype(np.uint8)
        grid = np.array(np.meshgrid(levels, levels, levels)).reshape(3, -1).T.reshape(64, 64, 3)
        img = Image.fromarray(np.ascontiguousarray(grid), "RGB")
        out = ImageCms.profileToProfile(img, profile, ImageCms.createProfile("sRGB"),
                                        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
        diff = np.abs(np.asarray(out, dtype=np.int16) - grid.astype(np.int16)).max()
    except Exception:  # noqa: BLE001 - a profile we can't read stays as it is
        return None
    return _compact_srgb() if diff <= 1 else None


def replace_jpeg_icc(data: bytes, icc: bytes) -> bytes:
    """A JPEG with its ICC profile segments replaced by ``icc`` (in one segment: it's small)."""
    segs = [(s, e) for m, s, e in segments(data) if m == 0xE2 and data[s + 4:s + 16] == b"ICC_PROFILE\0"]
    if not segs:
        return data
    body = b"ICC_PROFILE\0" + bytes([1, 1]) + icc
    new = b"\xff\xe2" + struct.pack(">H", len(body) + 2) + body
    out = data[:segs[0][0]] + new
    last = segs[0][1]
    for s, e in segs[1:]:
        out += data[last:s]
        last = e
    return out + data[last:]


def jpeg_icc(data: bytes) -> bytes | None:
    parts = sorted((data[s + 16], data[s + 18:e]) for m, s, e in segments(data)
                   if m == 0xE2 and data[s + 4:s + 16] == b"ICC_PROFILE\0")
    return b"".join(p for _n, p in parts) or None


__all__ = ["segments", "primary_end", "kind", "rejoin", "with_mpf_from", "without_extras", "exif_without_thumbnail",
           "strip_jpeg_thumbnail", "compact_icc", "replace_jpeg_icc", "jpeg_icc"]
