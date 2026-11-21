"""Music and other audio files.

* Lossless sources (WAV, AIFF, ALAC, APE, WavPack, FLAC) become FLAC: every sample is kept, and the
  result is verified by decoding both files and comparing the audio sample for sample (MD5 of the PCM).
* Or anything becomes Opus, AAC or MP3. These encoders already drop what people can't hear (their
  psychoacoustic models and band limits), so there is no separate "remove inaudible frequencies" step.
* Two lossless-in-practice reductions: stereo files whose channels are identical are stored as mono, and
  (optionally) hi-res audio is reduced to CD quality (44.1/48 kHz, 16-bit with dither) - ultrasonic
  content and dynamic range beyond human hearing.
* Tags and album art are kept (Opus: art stored as a METADATA_BLOCK_PICTURE comment).
"""

from __future__ import annotations

import base64
import re
import struct
import threading
from collections.abc import Callable
from pathlib import Path

from . import steps, tools
from .fileops import remove_quietly, temp_name
from .images import VerificationFailed
from .plan import Plan
from .probe import AudioInfo, probe_audio
from .settings import Settings

LOSSLESS_CODECS = {"flac", "alac", "ape", "wavpack", "tta", "mlp", "truehd", "wmalossless", "shorten"}

# format -> (extension, ffmpeg encoder, label)
FORMATS = {
    "flac": (".flac", "flac", "FLAC"),
    "opus": (".opus", "libopus", "Opus"),
    "aac": (".m4a", "aac", "AAC"),
    "mp3": (".mp3", "libmp3lame", "MP3"),
}
# Stereo bitrates (kbps). Opus 128 kbps is transparent for almost all listeners; AAC needs more.
BITRATE = {
    "opus": {"high": 160, "balanced": 128, "small": 96},
    "aac": {"high": 256, "balanced": 192, "small": 128},
    "mp3": {"high": 245, "balanced": 190, "small": 130},   # LAME VBR V0 / V2 / V5 averages
}
MP3_VBR = {"high": "0", "balanced": "2", "small": "5"}
# Speech (podcasts, audiobooks, lectures), stored as mono: kbps per format and quality.
SPEECH_KBPS = {"opus": {"high": 64, "balanced": 48, "small": 32}, "aac": {"high": 96, "balanced": 80, "small": 64},
               "mp3": {"high": 96, "balanced": 80, "small": 64}}
SPEECH_MIN_SECONDS = 600       # shorter files are nearly always songs: not worth the check
TAG = "WICK"
_CODEC_LABEL = {"mp3": "MP3", "aac": "AAC", "vorbis": "Vorbis", "opus": "Opus", "wmav1": "WMA", "wmav2": "WMA",
                "wmapro": "WMA", "flac": "FLAC", "alac": "ALAC", "ape": "APE", "wavpack": "WavPack", "tta": "TTA",
                "ac3": "AC-3", "eac3": "E-AC-3", "dts": "DTS"}


def is_lossless(codec: str) -> bool:
    return codec in LOSSLESS_CODECS or codec.startswith("pcm_")


def source_label(path: Path, info: AudioInfo) -> str:
    if info.codec.startswith("pcm_"):
        ext = path.suffix.lower()
        return "AIFF" if ext.startswith(".aif") else "CAF" if ext == ".caf" else "WAV"
    return _CODEC_LABEL.get(info.codec, info.codec.upper())


def target_kbps(fmt: str, quality: str, channels: int) -> int:
    kb = BITRATE[fmt][quality]
    if channels == 1:
        kb = max(48, int(kb * 0.6))
    elif channels > 2:
        kb = int(kb * channels / 2 * 0.8)
    return kb


def cd_target(info: AudioInfo) -> tuple[int | None, int | None]:
    """(sample rate, bits) for "CD quality", or None where nothing changes."""
    rate = None
    if info.sample_rate > 48000:
        rate = 44100 if info.sample_rate % 44100 == 0 else 48000
    bits = 16 if info.bits > 16 else None
    return rate, bits


def flac_problem(info: AudioInfo, s: Settings) -> str:
    """Why this source can't be stored losslessly as FLAC ("" if it can)."""
    if s.audio_cd_quality and cd_target(info)[1]:
        return ""  # becomes 16-bit anyway
    if info.sample_fmt.startswith(("flt", "dbl")) or info.codec.startswith(("pcm_f", "pcm_lxf")):
        return "Floating-point audio can't be stored losslessly as FLAC"
    if info.bits > 24:
        return "32-bit audio can't be stored losslessly as FLAC"
    return ""


def identical_channels(src: Path, info: AudioInfo, exact: bool) -> bool:
    """True when a stereo file's left and right channels carry the same signal."""
    if info.channels != 2:
        return False
    ffmpeg = tools.require("ffmpeg")
    proc = tools.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(src), "-map", f"0:{info.audio_index}",
                      "-af", "pan=mono|c0=c0-c1,astats=metadata=0:measure_perchannel=none:measure_overall=Peak_level",
                      "-f", "null", "-"], timeout=1800, check=False)
    m = re.search(r"Peak level dB:\s*(-?inf|-?[0-9.]+)", proc.stderr)
    if proc.returncode != 0 or not m:
        return False
    level = m.group(1)
    if level.endswith("inf"):
        return True
    return not exact and float(level) < -80.0  # lossy decoders may differ by rounding noise


def _cover_metadata(src: Path, info: AudioInfo, folder: Path | None = None) -> Path | None:
    """An ffmetadata file carrying the album art as METADATA_BLOCK_PICTURE (for Ogg/Opus), written in
    ``folder`` (default: next to ``src``)."""
    mime = {"mjpeg": "image/jpeg", "png": "image/png"}.get(info.cover_codec)
    if info.cover_index < 0 or not mime:
        return None
    data = _cover_bytes(src, info)
    if not data:
        return None
    data = _smaller(data)
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001
        w = h = 0
    m = mime.encode()
    block = (struct.pack(">II", 3, len(m)) + m + struct.pack(">I", 0) + struct.pack(">IIII", w, h, 24, 0)
             + struct.pack(">I", len(data)) + data)
    b64 = base64.b64encode(block).decode()
    escaped = b64.replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#")
    meta = temp_name(folder or src.parent, ".txt", "-meta")
    meta.write_text(";FFMETADATA1\nMETADATA_BLOCK_PICTURE=" + escaped + "\n", encoding="ascii")
    return meta


def _cover_bytes(src: Path, info: AudioInfo) -> bytes:
    if info.cover_index < 0 or info.cover_codec not in ("mjpeg", "png"):
        return b""
    ffmpeg = tools.require("ffmpeg")
    proc = tools.run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(src), "-map",
                      f"0:{info.cover_index}", "-c", "copy", "-f", "image2pipe", "-"], timeout=120, text=False,
                     check=False)
    return (proc.stdout or b"") if proc.returncode == 0 else b""


def _smaller(data: bytes) -> bytes:
    """The album art with the same pixels in fewer bytes (or as it was)."""
    from .documents import shrink_jpeg, shrink_png

    return shrink_png(data) if data[:8] == b"\x89PNG\r\n\x1a\n" else shrink_jpeg(data)


def small_cover(src: Path, info: AudioInfo, folder: Path | None = None) -> tuple[Path | None, int]:
    """(file with the album art stored smaller, bytes saved), or (None, 0) when it doesn't get smaller."""
    data = _cover_bytes(src, info)
    new = _smaller(data) if data else data
    if len(new) >= len(data):
        return None, 0
    out = temp_name(folder or src.parent, ".png" if new[:4] == b"\x89PNG" else ".jpg", "-cover")
    out.write_bytes(new)
    return out, len(data) - len(new)


def settings_tag(fmt: str, kbps: int, speech: bool) -> str:
    from . import __version__

    return "; ".join(x for x in (__version__, f"{fmt} {kbps}k" if kbps else fmt, "speech" if speech else "") if x)


def build_args(src: Path, dst: Path, info: AudioInfo, s: Settings, fmt: str, mono: bool,
               meta: Path | None = None, kbps: int = 0, rate: int = 0, channels: int = 0,
               cover: Path | None = None, speech: bool = False) -> list[str]:
    """``kbps``, ``rate`` and ``channels`` (Convert's per-file settings) override the automatic choices.
    ``cover``: the album art stored smaller, used instead of the original's. ``speech``: mono, at the speech
    bitrate."""
    ffmpeg = tools.require("ffmpeg")
    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
            "-i", str(src)]
    if meta is not None:
        args += ["-i", str(meta)]
    art = fmt in ("flac", "aac", "mp3") and info.cover_index >= 0 and info.cover_codec in ("mjpeg", "png")
    if art and cover is not None:
        args += ["-i", str(cover)]
    args += ["-map", f"0:{info.audio_index}"]
    if art:
        args += ["-map", f"{2 if meta is not None else 1}:0" if cover is not None else f"0:{info.cover_index}",
                 "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    args += ["-map_metadata", "0"]
    if meta is not None:
        args += ["-map_metadata:s:a:0", "1:g"]
    if speech and not kbps:
        kbps = SPEECH_KBPS[fmt][s.audio_quality] if fmt in SPEECH_KBPS else 0
        channels = channels or 1

    filters = []
    if mono:
        filters.append("pan=mono|c0=c0")
    elif channels:
        args += ["-ac", str(channels)]
    cd_rate, bits = cd_target(info) if s.audio_cd_quality else (None, None)
    rate = rate or cd_rate or 0
    if rate and fmt != "opus":  # Opus always works at 48 kHz internally
        filters.append(f"aresample={rate}:resampler=swr:filter_size=64:phase_shift=10")
    out_channels = 1 if mono else channels or info.channels
    if fmt == "flac":
        if bits == 16:
            filters.append("aresample=osf=s16:dither_method=triangular")
        args += ["-c:a", "flac", "-compression_level", "12"]    # (FFmpeg's strongest: about 1% smaller than 8)
    elif fmt == "opus":
        args += ["-c:a", "libopus", "-b:a", f"{kbps or target_kbps('opus', s.audio_quality, out_channels)}k",
                 "-vbr", "on", "-compression_level", "10"]
        if out_channels > 2:
            args += ["-mapping_family", "1"]
    elif fmt == "aac":
        args += ["-c:a", "aac", "-b:a", f"{kbps or target_kbps('aac', s.audio_quality, out_channels)}k",
                 "-movflags", "+faststart+use_metadata_tags"]
    else:  # mp3
        args += ["-c:a", "libmp3lame", *(["-b:a", f"{kbps}k"] if kbps else ["-q:a", MP3_VBR[s.audio_quality]]),
                 "-id3v2_version", "3"]
    if filters:
        args += ["-af", ",".join(filters)]
    args += ["-metadata", f"{TAG}={settings_tag(fmt, kbps, speech)}"]
    args.append(str(dst))
    return args


def made_by_wick(info: AudioInfo) -> bool:
    return any(k.upper() == TAG for k in info.tags)


def is_speech(src: Path, info: AudioInfo) -> bool:
    """Talk rather than music (a podcast, audiobook or lecture), from three 30-second stretches: the pauses between
    words and sentences (music rarely falls silent that often), little sound above 5 kHz (cymbals, strings and
    most mixes have plenty), and, for stereo, channels that are nearly the same. Long files only."""
    import numpy as np

    if info.duration < SPEECH_MIN_SECONDS or info.channels > 2:
        return False
    ffmpeg = tools.require("ffmpeg")
    rate = 16000
    verdicts = []
    for at in (0.2, 0.5, 0.8):
        p = tools.run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{info.duration * at:.1f}", "-t",
                       "30", "-i", str(src), "-map", f"0:{info.audio_index}", "-ac", "2", "-ar", str(rate), "-f",
                       "s16le", "-"], timeout=120, check=False, text=False)
        pcm = np.frombuffer(p.stdout or b"", dtype="<i2").astype(np.float32)
        if p.returncode != 0 or len(pcm) < rate * 2 * 10:
            return False
        left, right = pcm[0::2], pcm[1::2]
        mid = (left + right) / 2
        if info.channels == 2 and np.std(left) > 1 and np.std(right) > 1:
            if float(np.corrcoef(left, right)[0, 1]) < 0.9:
                verdicts.append(False)
                continue
        frame = rate // 40                                           # 25 ms
        frames = mid[: len(mid) // frame * frame].reshape(-1, frame)
        rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-3
        loud = np.percentile(rms, 95)
        if loud < 30:                                                # (near silence: nothing to tell)
            return False
        quiet = rms < loud * 0.1
        pauses = float(quiet.mean())
        # Speech pauses come often and short (between words and sentences); music's rests are rare and long.
        edges = np.flatnonzero(np.diff(np.concatenate(([0], quiet.astype(np.int8), [0]))))
        lengths = edges[1::2] - edges[0::2]
        per_minute = int(((lengths >= 3) & (lengths <= 60)).sum()) / (len(rms) * 0.025 / 60)   # 75 ms to 1.5 s
        spectrum = np.abs(np.fft.rfft(frames * np.hanning(frame), axis=1)) ** 2
        high = float(spectrum[:, int(5000 / rate * frame):].sum() / max(spectrum.sum(), 1e-9))
        verdicts.append(0.08 <= pauses <= 0.6 and per_minute >= 10 and high < 0.05)
    return sum(verdicts) >= 2


def pcm_md5(path: Path, stream: str, mono: bool = False) -> str:
    """MD5 of the decoded audio as 32-bit samples (comparable across WAV, FLAC, ALAC...)."""
    ffmpeg = tools.require("ffmpeg")
    args = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-map", stream]
    if mono:
        args += ["-af", "pan=mono|c0=c0"]
    out = tools.run(args + ["-c:a", "pcm_s32le", "-f", "md5", "-"], timeout=3600).stdout
    m = re.search(r"MD5=([0-9a-f]+)", out)
    if not m:
        raise VerificationFailed("couldn't decode the audio to compare")
    return m.group(1)


def verify_audio(src: Path, info: AudioInfo, out: Path, fmt: str, mono: bool, exact: bool,
                 channels: int = 0) -> None:
    got = probe_audio(out)
    expected = {"flac": "flac", "opus": "opus", "aac": "aac", "mp3": "mp3", "wav": "pcm_"}[fmt]
    if got is None or not got.codec.startswith(expected):
        raise VerificationFailed(f"result isn't readable {FORMATS[fmt][2] if fmt in FORMATS else fmt.upper()}")
    channels = 1 if mono else channels or info.channels
    if got.channels != channels:
        raise VerificationFailed(f"{got.channels} channels instead of {channels}")
    if info.duration > 0 and abs(got.duration - info.duration) > max(0.5, info.duration * 0.01):
        raise VerificationFailed(f"duration {got.duration:.1f}s instead of {info.duration:.1f}s")
    if exact:
        if pcm_md5(src, f"0:{info.audio_index}", mono) != pcm_md5(out, f"0:{got.audio_index}"):
            raise VerificationFailed("decoded audio differs from the original")
        return
    ffmpeg = tools.require("ffmpeg")
    proc = tools.run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(out), "-map",
                      f"0:{got.audio_index}", "-f", "null", "-"], timeout=3600, check=False)
    if proc.returncode != 0 or proc.stderr.strip():
        raise VerificationFailed("result doesn't decode cleanly")


def convert_audio(src: Path, info: AudioInfo, plan: Plan, s: Settings,
                  on_progress: Callable[[float], None] | None = None,
                  cancel: threading.Event | None = None, kbps: int = 0, rate: int = 0, channels: int = 0) -> Path:
    """Encode to a temp file next to ``src``, verify it, and return its path."""
    fmt = plan.route.split("_", 1)[1]
    if fmt == "art":
        return _shrink_art(src, info, plan, on_progress, cancel)
    speech = plan.speech and not (kbps or channels)
    if speech:
        channels = 1
    exact = (fmt == "flac" and is_lossless(info.codec) and not flac_problem(info, s)
             and not (s.audio_cd_quality and any(cd_target(info))) and not rate and not channels)
    mono = s.audio_mono and not channels and identical_channels(src, info, exact=exact)
    if cancel is not None and cancel.is_set():
        raise tools.Cancelled()
    out = temp_name(src.parent, FORMATS[fmt][0])
    meta = _cover_metadata(src, info) if fmt == "opus" else None
    cover, art_saved = small_cover(src, info) if fmt != "opus" else (None, 0)
    try:
        steps.report(f"Encoding {steps.format_name(FORMATS[fmt][0])}")
        tools.run_ffmpeg_progress(build_args(src, out, info, s, fmt, mono, meta, kbps, rate, channels, cover,
                                             speech), info.duration, on_progress, cancel)
        steps.report("Checking the result")
        verify_audio(src, info, out, fmt, mono, exact, channels)
        notes = []
        if mono:
            notes.append("stored as mono (both channels were identical)")
        if speech:
            notes.append("speech: mono at a lower bitrate")
        if art_saved:
            notes.append("album art stored smaller (same pixels)")
        plan.outcome = ", ".join(notes)
        return out
    except BaseException:
        remove_quietly(out)
        raise
    finally:
        remove_quietly(meta)
        remove_quietly(cover)


ART_CONTAINERS = {".mp3", ".flac", ".m4a", ".m4b"}     # (Ogg keeps art in a comment: not swapped this way)


def _shrink_art(src: Path, info: AudioInfo, plan: Plan, on_progress, cancel) -> Path:
    """Only the album art, stored smaller with the same pixels; the sound is copied untouched."""
    from .images import NotWorthIt

    steps.report("Making the album art smaller")
    cover, saved = small_cover(src, info)
    if cover is None or saved < max(32 * 1024, src.stat().st_size * 0.03):
        remove_quietly(cover)
        raise NotWorthIt("The album art is already stored about as small as it gets; original kept")
    out = temp_name(src.parent, src.suffix)
    ffmpeg = tools.require("ffmpeg")
    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
            "-i", str(src), "-i", str(cover), "-map", f"0:{info.audio_index}", "-map", "1:0", "-c", "copy",
            "-disposition:v:0", "attached_pic", "-map_metadata", "0", "-metadata", f"{TAG}={settings_tag('art', 0, False)}"]
    if src.suffix.lower() == ".mp3":
        args += ["-id3v2_version", "3"]
    elif src.suffix.lower() in (".m4a", ".m4b"):
        args += ["-movflags", "+faststart+use_metadata_tags"]
    try:
        tools.run_ffmpeg_progress(args + [str(out)], info.duration, on_progress, cancel)
        steps.report("Checking the result")
        got = probe_audio(out)
        if got is None or got.cover_index < 0:
            raise VerificationFailed("the album art went missing")
        if pcm_md5(src, f"0:{info.audio_index}") != pcm_md5(out, f"0:{got.audio_index}"):
            raise VerificationFailed("the sound changed")
        plan.outcome = "album art stored smaller (same pixels), sound untouched"
        return out
    except BaseException:
        remove_quietly(out)
        raise
    finally:
        remove_quietly(cover)


__all__ = ["convert_audio", "verify_audio", "is_lossless", "source_label", "target_kbps", "cd_target", "is_speech",
           "made_by_wick", "small_cover", "SPEECH_KBPS", "ART_CONTAINERS",
           "flac_problem", "identical_channels", "FORMATS", "BITRATE"]
