"""Plain-language summaries for the Compress page: what the current options will do (one line above the list),
where the space would come from (by kind of file, before starting), and why files were left alone (grouped,
afterwards)."""

from __future__ import annotations

import re
from collections import Counter

from .formats import AUDIO, DOCUMENT, VIDEO, human
from .settings import Settings

_FORMAT = {"jxl": "JPEG XL", "avif": "AVIF", "webp": "WebP", "jpeg": "JPEG"}
_VIDEO_QUALITY = {"identical": "visually identical", "high": "high quality", "balanced": "balanced quality",
                  "small": "small files", "custom": "custom quality"}


def describe_settings(s: Settings, kinds: set[str] | None = None, bin_name: str = "Recycle Bin") -> str:
    """e.g. "Photos to JPEG XL (identical pixels) · Videos to AV1, balanced quality · Originals to the Recycle Bin".
    ``kinds``: only the parts for these groups ("image", "video", "audio", "document"); None for all."""
    want = (lambda k: True) if kinds is None else (lambda k: k in kinds)
    parts = []
    if want("image"):
        if s.image_format == "none":
            parts.append("Pictures left alone")
        else:
            target = "their own format" if s.image_format == "keep" else _FORMAT.get(s.image_format,
                                                                                    s.image_format.upper())
            exact = s.jpeg_mode == "lossless" and s.png_mode == "lossless"
            how = "identical pixels" if exact else "smaller, visually close" if s.jpeg_mode != "lossless" \
                and s.png_mode != "lossless" else "photos exact, others " + (
                    "exact" if s.png_mode == "lossless" else "visually close")
            if s.image_format == "keep":
                parts.append(f"Pictures kept in {target}, {how}")
            else:
                parts.append(f"Pictures to {target} ({how})")
            if s.resize_mode != "none":
                parts[-1] += ", resized"
    if want("video"):
        if s.video_codec == "none":
            parts.append("Videos left alone")
        else:
            codec = {"auto": "AV1 or HEVC", "av1": "AV1", "hevc": "HEVC"}.get(s.video_codec, s.video_codec.upper())
            quality = "smallest that looks identical" if s.smart_quality else \
                _VIDEO_QUALITY.get(s.video_quality, s.video_quality)
            parts.append(f"Videos to {codec}, {quality}")
    if want("audio"):
        lossless = {"flac": "lossless audio to FLAC", "lossy": f"lossless audio to {s.audio_format.upper()}",
                    "keep": ""}.get(s.audio_lossless, "")
        lossy = f"other audio re-encoded to {s.audio_format.capitalize()}" if s.audio_lossy == "reencode" else ""
        bits = [b for b in (lossless, lossy) if b]
        if bits:
            text = ", ".join(bits)
            parts.append(text[0].upper() + text[1:])
    if want("document") and s.documents not in ("off", "none", ""):
        parts.append("Documents: pictures inside shrunk" + (" (identical pixels)" if s.documents == "lossless"
                                                            else ""))
    if s.fit_under_kb:
        parts.append(f"each file under {human(s.fit_under_kb * 1024)}")
    parts.append({"replace": f"Originals to the {bin_name}", "suffix": "Results saved next to the originals",
                  "folder": "Results saved to the output folder"}.get(s.output_mode, ""))
    return " · ".join(p for p in parts if p)


def group_of(kind: str) -> str:
    return "video" if kind == VIDEO else "audio" if kind == AUDIO else "document" if kind == DOCUMENT else "image"


_GROUP_WORDS = {"video": ("video", "videos"), "image": ("picture", "pictures"), "audio": ("audio file", "audio files"),
                "document": ("document", "documents")}


def savings_breakdown(items) -> list[tuple[str, int, int]]:
    """(group, files, bytes it would save) for (FileEntry, Plan) pairs, biggest saving first; groups that save
    nothing are left out."""
    files: Counter = Counter()
    saved: Counter = Counter()
    for e, p in items:
        g = group_of(e.kind)
        files[g] += 1
        saved[g] += max(0, e.size - p.est_size)
    return sorted(((g, files[g], saved[g]) for g in files if saved[g] > 0), key=lambda t: -t[2])


def say_breakdown(parts: list[tuple[str, int, int]]) -> list[str]:
    """["9.1 GB from 40 videos", ...]"""
    out = []
    for g, n, b in parts:
        one, many = _GROUP_WORDS[g]
        out.append(f"{human(b)} from {n:,} {one if n == 1 else many}")
    return out


def skip_reason(message: str) -> str:
    """The reason a file was left alone, worded so files with the same reason group together."""
    m = message.strip()
    low = m.lower()
    if low.startswith(("would only save", "result would be", "tried before", "nothing worth removing")) or \
            "not smaller" in low or "already as small" in low:
        return "already as small as they'll get"
    if "changed since the scan" in low or "no longer exists" in low:
        return "changed or moved since the scan"
    m = re.sub(r"[;,] original kept\.?$", "", m).rstrip(".")
    m = re.sub(r"\d[\d.,]*\s*(%|[KMGT]?B\b|s\b)?", "#", m)           # numbers and sizes vary from file to file
    if not m:
        return "no reason given"
    return m if m[1:2].isupper() else m[0].lower() + m[1:]       # ("HDR photo" stays as it is)


def group_skips(messages: list[str]) -> list[tuple[str, int]]:
    """(reason, how many) most common first."""
    return Counter(skip_reason(m) for m in messages).most_common()


__all__ = ["describe_settings", "savings_breakdown", "say_breakdown", "skip_reason", "group_skips", "group_of"]
