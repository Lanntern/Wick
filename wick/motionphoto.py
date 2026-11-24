"""Motion Photos (Google Pixel, and Samsung's Google-format ones): a JPEG with a short MP4 clip stored after it.

Both parts are made smaller and put back together: the photo losslessly (identical pixels, any Ultra HDR gain map
kept), the clip as HEVC, which the phones' galleries and Google Photos play. The XMP that says where the clip
starts (GCamera:MicroVideoOffset, and the Container directory's Item:Length) is updated to the new clip's size.

Files whose clip isn't described that way (older Samsung files keep it in their own "SEF" trailer) are left
alone, as are HEIC Motion Photos.
"""

from __future__ import annotations

import dataclasses
import re
import struct
import tempfile
from pathlib import Path

from . import video
from .fileops import remove_quietly, temp_name
from .images import NotWorthIt, VerificationFailed
from .plan import Plan

_OFFSET = re.compile(rb'(GCamera:MicroVideoOffset=")(\d+)(")')
_ITEM = re.compile(rb'(<Container:Item\b[^>]*?Item:Semantic="MotionPhoto"[^>]*?Item:Length=")(\d+)(")', re.S)
_ITEM_ALT = re.compile(rb'(<Container:Item\b[^>]*?Item:Length=")(\d+)("[^>]*?Item:Semantic="MotionPhoto")', re.S)


def clip_length(data: bytes) -> int:
    """Bytes of the clip at the end of a Motion Photo, from its XMP (0 when it isn't described or isn't an MP4)."""
    head = data[:256 * 1024]
    if data[-4:] == b"SEFT" or b"SEFH" in data[-4096:]:
        return 0                                   # (Samsung's own trailer: not rebuilt)
    length = 0
    for pattern in (_ITEM, _ITEM_ALT, _OFFSET):
        m = pattern.search(head)
        if m:
            length = int(m.group(2))
            break
    if not 0 < length < len(data) or data[len(data) - length + 4:len(data) - length + 8] != b"ftyp":
        return 0
    return length


def _set_length(jpeg: bytes, new_length: int) -> bytes:
    """The photo with its XMP saying the clip is ``new_length`` bytes (the XMP segment's size fixed after)."""
    from .jpegparts import segments

    for marker, start, end in segments(jpeg):
        body = jpeg[start + 4:end]
        if marker != 0xE1 or not body.startswith(b"http://ns.adobe.com/xap/1.0/\0"):
            continue
        new = body
        for pattern in (_ITEM, _ITEM_ALT, _OFFSET):
            new = pattern.sub(lambda m: m.group(1) + str(new_length).encode() + m.group(3), new)
        if new == body:
            continue
        return jpeg[:start] + b"\xff\xe1" + struct.pack(">H", len(new) + 2) + new + jpeg[end:]
    return jpeg


def convert(src: Path, info, plan, s) -> Path:
    """A smaller Motion Photo next to ``src`` (checked), or NotWorthIt."""

    from .images import Ctx, jpeg_extras
    from .probe import probe_video

    data = src.read_bytes()
    length = clip_length(data)
    if not length:
        raise NotWorthIt("This Motion Photo's clip isn't stored in a way Wick can rebuild; original kept")
    photo, clip = data[:-length], data[-length:]
    out = temp_name(src.parent, src.suffix)
    with tempfile.TemporaryDirectory(prefix="wick-motion-") as folder:
        work = Path(folder)
        clip_in = work / "clip.mp4"
        clip_in.write_bytes(clip)
        # The photo: losslessly, keeping a gain map (the clip is cut off first, so it doesn't count as "extra").
        import mozjpeg_lossless_optimization as mz

        photo_in = work / "photo.jpg"
        photo_in.write_bytes(photo)
        ctx = Ctx(photo_in, "jpeg", info, plan, s, work / "photo-out.jpg")
        new_photo = jpeg_extras(ctx, mz.optimize(photo, copy=mz.COPY_MARKERS.ALL))
        # The clip: HEVC, at the chosen video quality.
        clip_info = probe_video(clip_in)
        new_clip = clip
        if clip_info is not None and clip_info.codec not in ("hevc", "av1"):
            vs = dataclasses.replace(s, video_codec="hevc", fit_under_kb=0, video_chunked=False, video_tracks="all")
            clip_plan = Plan(True, "", ".mp4", 0, route="video")
            try:
                encoded, _used = video.convert_video(clip_in, clip_info, clip_plan, vs)
                if encoded.stat().st_size < len(clip):
                    new_clip = encoded.read_bytes()
                remove_quietly(encoded)
            except NotWorthIt:
                pass
        rebuilt = _set_length(new_photo, len(new_clip)) + new_clip
    if len(rebuilt) >= len(data):
        raise NotWorthIt("This Motion Photo is already about as small as it gets; original kept")
    out.write_bytes(rebuilt)
    try:
        _verify(out, photo, len(new_clip))
    except BaseException:
        remove_quietly(out)
        raise
    plan.outcome = ("photo made smaller with identical pixels" + (", clip re-encoded as HEVC"
                    if new_clip is not clip else ""))
    return out


def _verify(path: Path, original_photo: bytes, clip_len: int) -> None:
    from .documents import same_pixels
    from .jpegparts import primary_end
    from .probe import probe_video

    data = path.read_bytes()
    if clip_length(data) != clip_len:
        raise VerificationFailed("The photo doesn't point at its clip")
    photo = data[:-clip_len]
    if not same_pixels(original_photo[:primary_end(original_photo)], photo[:primary_end(photo)]):
        raise VerificationFailed("The photo looks different")
    with tempfile.TemporaryDirectory(prefix="wick-motion-check-") as folder:
        clip = Path(folder) / "clip.mp4"
        clip.write_bytes(data[-clip_len:])
        if probe_video(clip) is None:
            raise VerificationFailed("The clip doesn't play")


__all__ = ["convert", "clip_length"]
