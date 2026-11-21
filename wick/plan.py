"""Deciding what to do with each scanned file, and estimating savings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import tools
from .fileops import BIN_NAME, trash_available
from .formats import AUDIO, AVIF, BMP, DOCUMENT, GIF, HEIC, JPEG, PNG, TIFF, VIDEO, WEBP, video_output_ext
from .scanner import FileEntry
from .settings import Settings

# Codecs that are already efficient; re-encoding them saves little and costs quality.
EFFICIENT_VIDEO_CODECS = {"hevc", "av1", "vp9", "vvc"}
WEBP_MAX_DIM = 16383
AVIF_MAX_DIM = 65536

# Image routes (see images.py):
#   jxl_transcode  JPEG -> JXL, lossless & reversible (cjxl)
#   jxl            anything -> JXL (cjxl), lossless or lossy
#   webp / avif / heic   Pillow encoders (static or animated)
#   jpeg_opt       JPEG -> JPEG lossless (mozjpeg optimization)
#   jpeg           -> JPEG lossy (cjpegli, or Pillow + mozjpeg)
#   png_opt        -> PNG lossless (oxipng)
#   png_quant      -> PNG lossy (libimagequant + oxipng)
#   gif            GIF -> GIF (gifsicle)
#   tiff           TIFF -> TIFF lossless (Deflate)


@dataclass
class Plan:
    convert: bool
    reason: str = ""              # why it's skipped, or a short description of the conversion
    target_ext: str = ""
    est_size: int = 0             # estimated output size in bytes
    lossless: bool = False
    reversible: bool = False      # original can be rebuilt bit-for-bit from the output
    route: str = ""
    resize: tuple[int, int] | None = None
    note: str = ""                # extra info shown to the user
    speech: bool = False          # audio: talk (podcast, audiobook): mono at the speech bitrate
    audio_rate: int = 0           # audio: resample to this (hi-res audio that was upsampled from CD-type audio)
    outcome: str = ""             # filled in by the encoder (e.g. Smart quality's choice)
    raw_est: int = 0              # estimate before learning from past results (see calibration.py)
    dest: Path | None = None      # save exactly here ("Save As…" in the preview) instead of per the output mode


def _skip(reason: str) -> Plan:
    return Plan(False, reason)


def _too_small(e: FileEntry, limit_bytes: int) -> bool:
    """Under a size minimum (Options › Rules), unless the file was picked by itself: the minimums keep folder scans
    from spending time on tiny files, but a file you chose yourself is compressed whatever its size."""
    return e.size < limit_bytes and not e.picked


def plan_entry(entry: FileEntry, s: Settings) -> Plan:
    if s.output_mode == "folder" and not s.output_dir.strip():
        return _skip("Choose an output folder")
    if entry.live_pair:
        return _skip("Part of a Live Photo, so the photo and its video stay together")
    if entry.image is not None and entry.image.motion:
        return _skip("Motion Photo. Its short video is stored inside the file and would be lost")
    if entry.kind == VIDEO:
        p = _plan_video(entry, s)
    elif entry.kind == AUDIO:
        p = _plan_audio(entry, s)
    elif entry.kind == DOCUMENT:
        p = _plan_document(entry, s)
    else:
        p = _plan_image(entry, s)
    if p.convert and p.est_size:
        from . import calibration

        p.raw_est = p.est_size
        p.est_size = int(min(entry.size, p.est_size * calibration.factor(entry, p)))
    if p.convert and s.output_mode == "replace" and not s.allow_permanent_delete \
            and not trash_available(entry.path):
        return _skip(f"No {BIN_NAME} on this drive (USB/network). Choose another output option, "
                     "or allow permanent deletion")
    return p


def _lossy_ratio(q: int, table: tuple[float, float, float, float]) -> float:
    return table[0] if q >= 95 else table[1] if q >= 90 else table[2] if q >= 80 else table[3]


def _plan_image(e: FileEntry, s: Settings) -> Plan:
    fmt = s.image_format
    if fmt == "none":
        return _skip("Image conversion is off")
    if _too_small(e, s.min_image_kb * 1024):
        return _skip(f"Smaller than {s.min_image_kb} KB")
    info = e.image
    if info is None:
        return _skip("Couldn't read this image. It may be damaged or in an unsupported format")

    kind, q = e.kind, s.image_quality
    lossy_src = not info.lossless_source
    mode = s.jpeg_mode if kind == JPEG else ("lossy" if lossy_src else s.png_mode)
    deep = info.bit_depth > 8
    notes: list[str] = []
    fit = bool(s.fit_under_kb) and e.size > s.fit_under_kb * 1024
    if fit and not info.animated:
        mode = "lossy"                  # a size limit is met by choosing the quality, so the encode is lossy
        notes.append(f"made to fit under {s.fit_under_kb / 1024:.3g} MB")

    resize = None
    if s.resize_mode != "none":
        resize = s.resize_target(info.width, info.height)
        if resize and info.animated and not (fmt == "keep" and kind == GIF):
            notes.append("animation not resized")
            resize = None
    out_w, out_h = resize or (info.width, info.height)

    if fmt == "keep" and kind in (JPEG, PNG) and not resize and not fit:
        from . import marker

        if marker.has(e.path):
            return _skip("Already compressed with Wick")
    if info.pages > 1 and not (fmt == "keep" and kind == TIFF):
        return _skip("Multi-page TIFFs can only be compressed with Keep format")
    if kind == WEBP and info.animated and fmt in ("keep", "jxl"):
        return _skip("Animated WebP is already efficient" if fmt == "keep"
                     else "Animated WebP can't be converted to JPEG XL")
    if kind == HEIC and fmt == "keep":
        from .formats import register_heif
        if not register_heif():
            return _skip("HEIC support isn't installed")

    route, target, lossless, reversible = "", "", mode == "lossless", False

    if fmt == "keep":
        target = e.path.suffix.lower()
        if kind == JPEG:
            route = "jpeg_opt" if mode == "lossless" and not resize else "jpeg"
            lossless = route == "jpeg_opt"
            if resize and mode == "lossless":
                notes.append(f"resizing re-encodes at quality {q}")
        elif kind in (PNG, BMP):
            target = ".png"
            if info.animated:
                route, lossless = "png_opt", True
                if mode == "lossy":
                    notes.append("animated PNG optimized losslessly")
            else:
                route = "png_quant" if mode == "lossy" else "png_opt"
        elif kind == GIF:
            if not tools.find_tool("gifsicle"):
                return _skip("Needs gifsicle, which isn't installed")
            route = "gif"
        elif kind == TIFF:
            if deep:
                return _skip("16-bit TIFF can only be converted to JPEG XL")
            route, lossless = "tiff", True
            if mode == "lossy" or resize:
                notes.append("TIFF is re-saved losslessly")
                resize = None
                out_w, out_h = info.width, info.height
        elif kind == WEBP:
            route = "webp"
            lossless = info.lossless_source and mode == "lossless"
        elif kind == HEIC:
            route, lossless = "heic", False
        elif kind == AVIF:
            route, lossless = "avif", False
    elif fmt == "jxl":
        if not (tools.find_tool("cjxl") and tools.find_tool("djxl")):
            return _skip("Needs the JPEG XL tools, which aren't installed")
        target = ".jxl"
        if kind == JPEG and mode == "lossless" and not resize:
            route, reversible = "jxl_transcode", s.metadata == "keep"
        else:
            route = "jxl"
            lossless = mode == "lossless" and not lossy_src
    elif fmt == "webp":
        target, route = ".webp", "webp"
        if kind == JPEG and mode == "lossless":
            return _skip("WebP can't store JPEGs losslessly. Choose Lossy, or use JPEG XL")
        lossless = mode == "lossless" and not lossy_src
        if lossless and deep:
            return _skip("This 16-bit image would lose precision as WebP. Use JPEG XL instead")
        if max(out_w, out_h) > WEBP_MAX_DIM:
            return _skip(f"Larger than WebP's {WEBP_MAX_DIM}px limit")
    elif fmt == "avif":
        if mode == "lossless" and not lossy_src:
            return _skip("AVIF isn't efficient at lossless. Use JPEG XL or WebP, or choose Lossy")
        if kind == JPEG and mode == "lossless":
            return _skip("AVIF can't store JPEGs losslessly. Use JPEG XL, or choose Lossy")
        target, route, lossless = ".avif", "avif", False

    if lossy_src and kind != JPEG and route in ("webp", "avif", "heic", "jxl"):
        notes.append(f"{kind.upper()} source is already lossy")

    est = _estimate(e.size, kind, route, lossless, q, info, resize)
    smart = s.smart_quality and route in _SMART_ROUTES and not (info is not None and getattr(info, "frames", 1) > 1)
    desc = _describe(route, lossless, reversible, q, resize, s.smart_target if smart else "")
    if notes:
        desc += f"; {', '.join(notes)}"
    return Plan(True, desc, target, est, lossless, reversible, route, resize, ", ".join(notes))


_SMART_ROUTES = {"jxl", "webp", "avif", "heic", "jpeg", "png_quant"}    # as images.SMART_ROUTES
_SMART_LABEL = {"identical": "visually identical", "high": "high", "good": "good"}


def _describe(route: str, lossless: bool, reversible: bool, q: int, resize, smart: str = "") -> str:
    if reversible:
        d = "Lossless, reversible"
    elif route in ("jpeg_opt", "png_opt", "tiff"):
        d = "Lossless optimization"
    elif lossless:
        d = "Lossless"
    elif smart:
        d = f"Lossy, Smart quality ({_SMART_LABEL.get(smart, smart)})"
    else:
        d = f"Lossy, quality {q}"
    if resize:
        d += f", resized to {resize[0]}×{resize[1]}"
    return d


def _estimate(size: int, kind: str, route: str, lossless: bool, q: int, info, resize) -> int:
    if route == "jxl_transcode":
        r = 0.80
    elif route == "jpeg_opt":
        r = 0.92
    elif route == "png_opt":
        r = 0.3 if kind == BMP else 0.85
    elif route == "png_quant":
        r = 0.12 if kind == BMP else 0.35
    elif route == "gif":
        r = 0.9 if lossless else 0.6
    elif route == "tiff":
        r = 0.55
    elif lossless:  # jxl / webp lossless from exact sources
        base = {BMP: 0.25, TIFF: 0.35, GIF: 0.8, PNG: 0.65, WEBP: 0.9}.get(kind, 0.7)
        r = base * (1.15 if route == "webp" else 1.0)
    else:
        if kind in (JPEG,):
            table = {"jxl": (0.75, 0.6, 0.45, 0.35), "webp": (0.9, 0.8, 0.65, 0.5),
                     "avif": (0.65, 0.45, 0.35, 0.25), "jpeg": (0.95, 0.75, 0.55, 0.45),
                     "heic": (0.7, 0.5, 0.4, 0.3)}.get(route, (0.8, 0.7, 0.6, 0.5))
            r = _lossy_ratio(q, table)
        elif kind in (HEIC, AVIF) or (kind == WEBP and not info.lossless_source):
            r = 0.95
        else:  # lossy encode of an exact source
            r = _lossy_ratio(q, (0.35, 0.22, 0.15, 0.1))
            if kind in (BMP, TIFF):
                r *= 0.5
            if kind == GIF and info.animated:
                r = 0.5
    if resize:
        area = (resize[0] * resize[1]) / max(1, info.width * info.height)
        r *= area ** 0.8
    return int(size * min(0.98, r))


_VIDEO_BASE = {
    # source codec: (ratio for AV1, ratio for HEVC) at "balanced"
    "h264": (0.45, 0.6),
    "vp8": (0.6, 0.75),
}
_LEGACY_RATIO = (0.25, 0.35)  # mpeg2/mpeg4/wmv/mjpeg/prores/...
_QUALITY_FACTOR = {"identical": 1.2, "high": 1.35, "balanced": 1.0, "small": 0.7}
_CUSTOM_BALANCED = {"av1": 32, "hevc": 24}   # the CPU encoders' Balanced value, the reference for Custom


def _quality_factor(s: Settings, codec: str) -> float:
    if s.video_quality != "custom":
        return _QUALITY_FACTOR[s.video_quality]
    # Every ~6 steps of CRF roughly halves or doubles the size.
    return max(0.25, min(3.0, 2 ** ((_CUSTOM_BALANCED.get(codec, 24) - s.video_crf) / 6)))


_DROPPED_AUDIO_BPS = 448_000     # a typical surround track (AC-3/E-AC-3), for the estimate only


def fps_label(fps: float) -> str:
    """29.97, 23.976, 30 ... the way frame rates are usually written."""
    for nominal in (23.976, 29.97, 59.94, 119.88):
        if abs(fps - nominal) < 0.01:
            return f"{nominal:g}"
    return f"{fps:.2f}".rstrip("0").rstrip(".")


def _plan_video(e: FileEntry, s: Settings) -> Plan:
    if s.video_codec == "none":
        return _skip("Video conversion is off")
    if _too_small(e, s.min_video_mb * 1024 * 1024):
        return _skip(f"Smaller than {s.min_video_mb} MB")
    v = e.video
    if v is None:
        return _skip("Couldn't read video info")
    if v.hdr == "Dolby Vision":
        return _skip("Dolby Vision video. Re-encoding would lose its Dolby Vision layer")
    from .video import codec_for, drops_repeats, output_fps, packs_as_flac, select_tracks
    from .video import tracks_note as tracks_note_of

    resized = s.video_target_size(v.width, v.height) != (v.width - v.width % 2, v.height - v.height % 2)
    capped = output_fps(v, s) is not None
    repeats = drops_repeats(v, s)
    if v.duration <= 0:
        return _skip("Unknown duration")
    kept_audio, _subs, dropped = select_tracks(v, s)
    dropped_audio = sum(1 for st in dropped if st.kind == "audio")
    tracks_note = tracks_note_of(v, s).replace(" left out", "")
    tracks_note = f"leaves out {tracks_note}" if tracks_note else ""
    limit = s.fit_under_kb * 1024
    fit = bool(limit) and e.size > limit
    if fit:
        from .formats import human

        codec = codec_for(v, s)
        target = video_output_ext(e.path.suffix, codec)
        return Plan(True, f"{v.codec.upper()} → {'AV1' if codec == 'av1' else 'HEVC'}, made to fit under "
                    f"{human(limit)}", target, min(limit, int(e.size * 0.95)), route="video")
    if v.codec in EFFICIENT_VIDEO_CODECS and not (resized or capped or repeats or v.interlaced):
        from . import subfonts
        from .video import made_by_wick

        ext = video_output_ext(e.path.suffix, v.codec)
        made = made_by_wick(v)
        fonts = subfonts.font_bytes(v) if s.video_fonts and ext == ".mkv" and "fonts cut" not in made else 0
        fonts = fonts if fonts >= subfonts.MIN_FONT_BYTES else 0
        pcm = [a for a in kept_audio if packs_as_flac(a, ext == ".mp4")]
        stereo = [a for a in kept_audio if s.video_stereo and a.channels > 2]
        if not (dropped or fonts or pcm or stereo):
            return _skip("Already compressed with Wick" if made
                         else f"Already uses an efficient codec ({v.codec.upper()})")
        pcm_bytes = sum(a.bit_rate or 1_536_000 for a in pcm) / 8 * v.duration
        stereo_bytes = sum(a.bit_rate or 640_000 for a in stereo if a not in pcm) / 8 * v.duration
        saved = (dropped_audio * _DROPPED_AUDIO_BPS / 8 * v.duration + fonts * 0.7 + pcm_bytes * 0.45
                 + stereo_bytes * 0.6)
        est = max(int(e.size * 0.3), int(e.size - saved)) if saved else int(e.size * 0.99)
        why = "; ".join(x for x in (tracks_note, "smaller subtitle fonts" if fonts else "",
                                    "uncompressed audio packed as FLAC" if pcm else "",
                                    "surround mixed down to stereo" if stereo else "") if x)
        return Plan(True, f"{v.codec.upper()} kept as it is; {why}", ext, est, lossless=True, route="video_remux")
    codec = codec_for(v, s)

    av1_r, hevc_r = _VIDEO_BASE.get(v.codec, _LEGACY_RATIO)
    ratio = (av1_r if codec == "av1" else hevc_r) * _quality_factor(s, codec)
    # Low-bitrate sources have less to gain.
    pixels_per_s = max(1.0, v.width * v.height * (v.fps or 30))
    bpp = v.bit_rate / pixels_per_s
    if bpp < 0.05:
        ratio *= 1 + (0.05 - bpp) / 0.05
    if v.codec in EFFICIENT_VIDEO_CODECS:
        ratio = 0.85  # only gains from resizing / frame rate below
    tw, th = s.video_target_size(v.width, v.height)
    if resized:
        ratio *= ((tw * th) / max(1, v.width * v.height)) ** 0.75
    if capped:
        ratio *= (s.video_max_fps / max(1.0, v.content_fps if repeats else v.fps)) ** 0.5
    if repeats:
        ratio *= 0.95  # repeats are cheap to encode anyway; the main win is smooth motion
    ratio = min(0.95, ratio)
    target = video_output_ext(e.path.suffix, codec)
    label = "AV1" if codec == "av1" else "HEVC"
    if s.video_codec == "auto" and codec == "hevc":
        label += " (camera video)"
    extra = []
    if resized:
        extra.append(f"{min(tw, th)}p")
    if v.interlaced:
        extra.append("deinterlaced")
    if v.hdr:
        extra.append(f"{v.hdr} kept")
    if repeats and not capped:
        extra.append(f"{fps_label(v.content_fps)} fps (repeated frames removed)")
    if capped:
        extra.append(f"{s.video_max_fps} fps")
    if s.video_quality == "identical":
        extra.append("visually identical, measured")
    elif s.video_quality == "custom":
        extra.append(f"quality {s.video_crf} (custom)")
    elif s.smart_quality:
        extra.append("smart quality")
    if tracks_note:
        extra.append(tracks_note)
    desc = f"{v.codec.upper()} → {label}" + (f", {', '.join(extra)}" if extra else "")
    est = int(e.size * ratio)
    if dropped_audio:
        est = max(int(est * 0.5), int(est - dropped_audio * _DROPPED_AUDIO_BPS / 8 * v.duration * ratio))
    return Plan(True, desc, target, est, route="video")


def _plan_document(e: FileEntry, s: Settings) -> Plan:
    from . import documents

    if s.documents == "none":
        return _skip("Documents are off")
    if _too_small(e, 100 * 1024):
        return _skip("Smaller than 100 KB")
    reduce = s.documents == "reduce"
    ext = e.path.suffix.lower()
    note = "large pictures reduced" if reduce else "identical pictures"
    if ext in documents.PDF_EXTS:
        if not documents.pdf_available():
            return _skip("PDF support isn't installed")
        return Plan(True, f"PDF with optimized pictures and structure ({note})", ext,
                    int(e.size * (0.7 if reduce else 0.92)), lossless=not reduce, route="document")
    try:
        jpeg, png, _other = documents.office_pictures(e.path)
    except documents.Unsupported as x:
        return _skip(str(x))
    if jpeg + png < 50 * 1024:
        return _skip("No pictures inside worth shrinking")
    saved = jpeg * (0.35 if reduce else 0.05) + png * (0.5 if reduce else 0.2)
    return Plan(True, f"Pictures inside shrunk ({note})", ext, int(e.size - saved), lossless=not reduce,
                route="document")


# Typical FLAC size relative to the source, for music.
_FLAC_RATIO = {"pcm": 0.58, "flac": 0.97, "alac": 0.98, "ape": 1.03, "wavpack": 1.0, "tta": 1.0}


def _plan_audio(e: FileEntry, s: Settings) -> Plan:
    from .audio import (ART_CONTAINERS, FORMATS, PADDING_MIN, SPEECH_KBPS, cd_target, flac_problem, is_lossless,
                        is_speech, made_by_wick, padding, source_label, target_kbps, upsampled_from)

    if s.audio_lossless == "keep" and s.audio_lossy == "keep":
        return _skip("Audio conversion is off")
    if _too_small(e, s.min_audio_kb * 1024):
        return _skip(f"Smaller than {s.min_audio_kb} KB")
    a = e.audio
    if a is None:
        return _skip("Couldn't read audio info")
    if a.duration <= 0:
        return _skip("Unknown duration")
    if not tools.find_tool("ffmpeg"):
        return _skip("Needs FFmpeg, which isn't installed")
    if made_by_wick(a):
        return _skip("Already compressed with Wick")
    src = source_label(e.path, a)
    lossless_src = is_lossless(a.codec)
    extra = []
    if lossless_src and s.audio_lossless == "keep":
        return _padding_plan(e, padding, PADDING_MIN) or _skip("Lossless audio is set to stay as it is")
    if lossless_src and s.audio_lossless == "flac":
        problem = flac_problem(a, s)
        if problem:
            return _skip(problem)
        rate, bits = cd_target(a) if s.audio_cd_quality else (None, None)
        fake = 0 if rate or not s.audio_fake_hires else _guess(upsampled_from, e, a)
        if fake:
            rate = fake
            extra.append("the original was upsampled")
        ratio = _FLAC_RATIO.get("pcm" if a.codec.startswith("pcm_") else a.codec, 1.0)
        if rate or bits:
            ratio *= ((rate or a.sample_rate) * (bits or a.bits or 16)) / max(1, a.sample_rate * (a.bits or 16))
            extra.append(f"{(rate or a.sample_rate) / 1000:g} kHz/{bits or a.bits}-bit")
        lossless = not (rate or bits)
        desc = f"{src} → FLAC" + (" (lossless)" if lossless else "") + (f", {', '.join(extra)}" if extra else "")
        return Plan(True, desc, ".flac", int(e.size * min(0.98, ratio)), lossless, False, "audio_flac",
                    audio_rate=fake)
    fmt = s.audio_format
    kbps = target_kbps(fmt, s.audio_quality, a.channels)
    lossy_on = lossless_src or s.audio_lossy != "keep"
    speech = (lossy_on and s.audio_speech and fmt in SPEECH_KBPS and a.duration >= 600
              and _guess(is_speech, e, a))
    if speech:
        kbps = SPEECH_KBPS[fmt][s.audio_quality]
    if not lossless_src:
        art = _art_plan(e, a, ART_CONTAINERS) or _padding_plan(e, padding, PADDING_MIN)
        if s.audio_lossy == "keep":
            return art or _skip(f"Already compressed ({src}); re-encoding is off")
        src_kbps = a.bit_rate / 1000
        if src_kbps < kbps * 1.4:
            return art or _skip(f"Already efficient ({src} at {src_kbps:.0f} kbps)")
    # Album art and tags travel along: count what isn't audio in the source.
    other = max(0, e.size - int(a.bit_rate / 8 * a.duration)) if not lossless_src else 0
    est = int(kbps * 1000 / 8 * a.duration) + other
    label = FORMATS[fmt][2]
    desc = f"{src} → {label} {kbps} kbps" + (" mono (speech)" if speech else "")
    return Plan(True, desc, FORMATS[fmt][0], min(est, int(e.size * 0.95)), False, False, f"audio_{fmt}",
                speech=speech)


ART_MIN_BYTES = 300 * 1024


def _art_plan(e: FileEntry, a, containers) -> Plan | None:
    """Album art stored smaller (same pixels), the sound copied untouched: for files whose art is big."""
    if a.cover_index < 0 or a.cover_codec not in ("mjpeg", "png") or e.path.suffix.lower() not in containers:
        return None
    art = e.size - int(a.bit_rate / 8 * a.duration)
    if art < ART_MIN_BYTES:
        return None
    return Plan(True, "Album art stored smaller (same pixels); sound untouched", e.path.suffix.lower(),
                e.size - int(art * 0.15), True, False, "audio_art")


def _padding_plan(e: FileEntry, measure, minimum: int) -> Plan | None:
    """Only the empty space in the tags removed, for files that keep a lot of it."""
    pad = measure(e.path)
    if pad < minimum:
        return None
    return Plan(True, "Empty space in the tags removed; sound and tags untouched", e.path.suffix.lower(),
                e.size - pad, True, False, "audio_padding")


def _guess(detect, e: FileEntry, a):
    """A check that reads the sound (speech, upsampling); when it can't tell, the answer is no."""
    try:
        return detect(e.path, a)
    except Exception:  # noqa: BLE001
        from .log import ignored
        ignored("plan._guess")
        return 0
