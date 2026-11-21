"""Soundtracks in videos: uncompressed audio packed as FLAC, lower-quality copies left out, surround mixed down."""

import re

import pytest

from wick import video
from wick.probe import StreamInfo, probe_video
from wick.settings import Settings

from .conftest import ffmpeg, have

needs_ffmpeg = pytest.mark.skipif(not have("ffmpeg", "ffprobe"), reason="ffmpeg not installed")


def _md5(path, stream: str) -> str:
    from wick import tools

    out = tools.run([tools.require("ffmpeg"), "-v", "error", "-i", str(path), "-map", stream, "-c:a", "pcm_s32le",
                     "-f", "md5", "-"]).stdout
    return re.search(r"MD5=(\w+)", out).group(1)


def test_lower_copies_are_found_by_language_and_quality():
    def a(i, codec, lang, ch, **kw):
        return StreamInfo(i, "audio", codec, language=lang, channels=ch, **kw)

    truehd, ac3 = a(1, "truehd", "eng", 8), a(2, "ac3", "eng", 6, default=True)
    commentary = a(3, "ac3", "eng", 2, title="Director's commentary", commentary=True)
    dts_ma, dts = a(4, "dts", "fra", 6, profile="DTS-HD MA"), a(5, "dts", "fra", 6, profile="DTS")
    german, untagged = a(6, "ac3", "deu", 6), a(7, "ac3", "", 6)
    copies = video.lower_copies([truehd, ac3, commentary, dts_ma, dts, german, untagged])
    assert copies == [ac3, dts]
    assert not video.lower_copies([a(1, "flac", "eng", 2), a(2, "eac3", "eng", 6)])   # (more channels: not a copy)


@needs_ffmpeg
def test_an_hevc_video_gets_flac_audio_and_loses_the_copy(tmp_path):
    from wick.plan import plan_entry
    from wick.scanner import FileEntry

    src = tmp_path / "film.mkv"
    tone = "sine=f=440:d=3,aformat=sample_fmts=s32:channel_layouts=5.1(side)"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=160x120:d=3", "-f", "lavfi", "-i", tone,
           "-map", "0", "-map", "1", "-map", "1", "-pix_fmt", "yuv420p", "-c:v", "libx265", "-x265-params",
           "log-level=error", "-c:a:0", "ac3", "-c:a:1", "pcm_s24le", "-metadata:s:a", "language=eng",
           "-disposition:a:0", "default", "-disposition:a:1", "0", str(src))
    info = probe_video(src)
    pcm = info.audio_streams()[1]
    assert pcm.codec == "pcm_s24le" and pcm.bits == 24 and video.packs_as_flac(pcm, mp4=False)
    s = Settings(video_dup_audio=True)
    plan = plan_entry(FileEntry(src, src.stat().st_size, "video", 0, video=info, picked=True), s)
    assert plan.convert and plan.route == "video_remux", plan.reason
    assert "packed as FLAC" in plan.reason and "lower-quality copy" in plan.reason
    out, used = video.convert_video(src, info, plan, s)
    try:
        back = probe_video(out)
        audio = back.audio_streams()
        assert [a.codec for a in audio] == ["flac"] and audio[0].default         # (the copy was the default)
        assert _md5(out, "0:a:0") == _md5(src, "0:a:1")                          # every sample kept
        assert "packed as FLAC" in plan.outcome and "1 lower-quality copy of a soundtrack left out" in plan.outcome
        assert out.stat().st_size < src.stat().st_size * 0.8
    finally:
        out.unlink(missing_ok=True)


@needs_ffmpeg
def test_surround_is_mixed_down_to_stereo_when_asked(tmp_path):
    from wick import tools

    src = tmp_path / "in.mkv"
    ffmpeg("-f", "lavfi", "-i", "sine=d=1,aformat=channel_layouts=7.1", "-c:a", "ac3", str(src))
    st = StreamInfo(0, "audio", "ac3", channels=8, layout="7.1", bit_rate=640_000)
    args = video._audio_args([st], Settings(video_stereo=True), mp4=False, fit=None)
    assert args == ["-c:a:0", "libopus", "-b:a:0", "160k", "-ac:a:0", "2"]
    tools.run([tools.require("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-map", "0", *args,
               str(tmp_path / "out.mka")])
    got = tools.run([tools.require("ffprobe"), "-v", "error", "-show_entries", "stream=channels", "-of", "csv=p=0",
                     str(tmp_path / "out.mka")]).stdout.strip()
    assert got == "2"
    # Copy mode leaves stereo alone; an audio bitrate is used for the mix when set.
    assert video._audio_args([StreamInfo(0, "audio", "aac", channels=2, bit_rate=128_000)],
                             Settings(video_stereo=True), False, None) == []
    assert "96k" in video._audio_args([st], Settings(video_stereo=True, audio_bitrate=96), False, None)
