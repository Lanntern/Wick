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


def _plan(path, info, s):
    from wick.plan import plan_entry
    from wick.scanner import FileEntry

    return plan_entry(FileEntry(path, path.stat().st_size, "video", 0, video=info, picked=True), s)


FAST = dict(video_codec="av1", video_encoder="cpu", smart_quality=False, video_chunked=False, video_grain="off")


@needs_ffmpeg
def test_an_mp4_keeps_its_cover_stored_smaller(tmp_path):
    from PIL import Image

    Image.new("RGB", (800, 800), (200, 40, 40)).save(tmp_path / "poster.png", compress_level=0)
    src = tmp_path / "film.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=160x120:d=2", "-i", str(tmp_path / "poster.png"), "-map", "0", "-map", "1",
           "-c:v:0", "libx264", "-pix_fmt", "yuv420p", "-c:v:1", "png", "-disposition:v:1", "attached_pic", str(src))
    info = probe_video(src)
    assert any(st.attached_pic for st in info.streams)
    s = Settings(**FAST)
    out, _used = video.convert_video(src, info, _plan(src, info, s), s)
    try:
        back = probe_video(out)
        covers = [st for st in back.streams if st.attached_pic]
        assert out.suffix == ".mp4" and len(covers) == 1 and back.codec == "av1"
    finally:
        out.unlink(missing_ok=True)


@needs_ffmpeg
def test_silent_and_identical_soundtracks(tmp_path):
    src = tmp_path / "dashcam.mkv"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=160x120:d=3", "-f", "lavfi", "-i", "anullsrc=cl=stereo:d=3", "-f", "lavfi",
           "-i", "sine=d=3,pan=stereo|c0=c0|c1=c0", "-map", "0", "-map", "1", "-map", "2", "-pix_fmt", "yuv420p",
           "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k", "-t", "3", str(src))
    info = probe_video(src)
    s = Settings(**FAST, video_drop_silent=True, audio_bitrate=128)
    plan = _plan(src, info, s)
    out, _used = video.convert_video(src, info, plan, s)
    try:
        audio = probe_video(out).audio_streams()
        assert len(audio) == 1 and audio[0].channels == 1                 # the silent one left out; mono
        assert "1 silent soundtrack left out" in plan.outcome and "stored as mono" in plan.outcome
    finally:
        out.unlink(missing_ok=True)


def test_talk_in_a_video_gets_the_speech_bitrate():
    st = StreamInfo(1, "audio", "aac", channels=1, bit_rate=192_000, speech=True)
    assert video._audio_args([st], Settings(audio_bitrate=128), mp4=False, fit=None)[3] == "48k"
    st2 = StreamInfo(1, "audio", "aac", channels=2, bit_rate=192_000, speech=True)
    assert video._audio_args([st2], Settings(audio_bitrate=128), mp4=False, fit=None)[3] == "72k"


@needs_ffmpeg
def test_a_tv_recording_is_rewrapped_without_its_padding(tmp_path):
    src = tmp_path / "news.ts"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=320x240:d=4", "-f", "lavfi", "-i", "sine=d=4", "-pix_fmt", "yuv420p",
           "-c:v", "libx265", "-x265-params", "log-level=error", "-c:a", "aac", "-muxrate", "4000000", "-f", "mpegts",
           str(src))
    info = probe_video(src)
    assert info.container == "mpegts"
    s = Settings()
    plan = _plan(src, info, s)
    assert plan.convert and plan.route == "video_remux" and "broadcast padding" in plan.reason, plan.reason
    out, used = video.convert_video(src, info, plan, s)
    try:
        assert used == "copy" and out.suffix == ".mkv" and out.stat().st_size < src.stat().st_size * 0.7
    finally:
        out.unlink(missing_ok=True)


@needs_ffmpeg
def test_screen_recordings_are_told_from_camera_video(tmp_path):
    screen, camera = tmp_path / "screen.mp4", tmp_path / "camera.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=white:s=640x360:d=3,drawbox=x=40:y=40:w=300:h=120:color=blue:t=fill,"
           "drawbox=x=40:y=200:w=500:h=20:color=gray:t=fill", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(screen))
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x360:d=3,noise=alls=8:allf=t", "-pix_fmt", "yuv420p", "-c:v",
           "libx264", str(camera))
    assert video.screen_recording(screen, probe_video(screen))
    assert not video.screen_recording(camera, probe_video(camera))


def test_dolby_vision_with_a_base_layer_is_planned_and_others_skipped(monkeypatch):
    from wick.probe import VideoInfo

    def dv(profile, compat):
        return VideoInfo("hevc", 3840, 2160, "yuv420p10le", 30.0, 60.0, 50_000_000, 0,
                         [StreamInfo(0, "video", "hevc")], hdr="Dolby Vision", dv_profile=profile, dv_compat=compat,
                         color={"transfer": "arib-std-b67"})

    assert video.dolby_vision_base(dv(8, 4)) == "HLG" and video.dolby_vision_base(dv(8, 1)) == "HDR10"
    assert video.dolby_vision_base(dv(5, 0)) == "" and video.dolby_vision_base(dv(8, 6)) == ""

    class Entry:
        pass

    from wick.plan import _plan_video

    def plan(info, **kw):
        e = Entry()
        e.path, e.size, e.video, e.picked = __import__("pathlib").Path("x.mov"), 400_000_000, info, True
        return _plan_video(e, Settings(video_max_height=1080, **kw))

    p = plan(dv(8, 4))
    assert p.convert and "HLG kept, Dolby Vision layer left out" in p.reason
    assert not plan(dv(5, 0)).convert and not plan(dv(8, 4), video_dolby_vision=False).convert
