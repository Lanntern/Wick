"""Subtitle fonts cut to the characters used, unused fonts left out; audio copied when already small, surround
given more; the settings tag; a light grain against banding."""

from pathlib import Path

import pytest

from wick import subfonts, video
from wick.probe import StreamInfo, VideoInfo
from wick.settings import Settings

from .conftest import ffmpeg, have

ASS = r"""[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Italic, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Test Sans,40,&H00FFFFFF,0,0,2,10,10,10,1
Style: Sign,@Test Serif,30,&H00FFFFFF,0,0,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Hello, 世界!\Nnext{\fnTest Serif}草{\r}本
Dialogue: 0,0:00:00.00,0:00:02.00,Sign,,0,0,0,,{\pos(10,10)}標識{\p1}m 0 0 l 100 100{\p0}
Comment: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,不要
"""


def test_characters_are_counted_per_font():
    used = subfonts.used_characters(ASS)
    assert {"test sans", "test serif"} == set(used)
    sans, serif = used["test sans"], used["test serif"]
    assert {ord(c) for c in "Hello, 世界!next本"} == sans            # (\N is a line break, not text)
    assert ord("草") in serif and ord("標") in serif and ord("識") in serif
    assert ord("m") not in serif                                     # (drawing commands aren't text)
    assert ord("不") not in sans                                     # (comments aren't shown)


def make_font(path: Path, family: str, codepoints, fs_type: int = 0) -> Path:
    """A real TrueType font with a square for each code point."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    def box():
        pen = TTGlyphPen(None)
        pen.moveTo((50, 0)), pen.lineTo((50, 700)), pen.lineTo((450, 700)), pen.lineTo((450, 0)), pen.closePath()
        return pen.glyph()

    names = [".notdef"] + [f"u{c:04X}" for c in codepoints]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({c: f"u{c:04X}" for c in codepoints})
    fb.setupGlyf({n: box() for n in names})
    fb.setupHorizontalMetrics({n: (500, 50) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2(fsType=fs_type)
    fb.setupPost()
    fb.save(path)
    return path


CJK = [*range(0x20, 0x7F), *range(0xA0, 0x100), *range(0x4E00, 0x5800), 0x7A7A]   # (0x5800: 標 is 0x6A19)


def test_a_font_is_cut_to_what_is_drawn_and_licences_are_respected(tmp_path):
    from fontTools.ttLib import TTFont

    big = make_font(tmp_path / "big.ttf", "Test Sans", [*CJK, 0x6A19, 0x8B58])
    out = tmp_path / "cut.ttf"
    assert subfonts.subset(big, out, {0x4E16, 0x6A19})
    cmap = set(TTFont(out).getBestCmap())
    assert {0x4E16, 0x6A19, ord("A"), 0xE9} <= cmap and 0x4E00 not in cmap
    assert out.stat().st_size < big.stat().st_size / 5
    locked = make_font(tmp_path / "locked.ttf", "Locked", CJK, fs_type=0x0100)    # no subsetting allowed
    assert not subfonts.subset(locked, tmp_path / "locked-cut.ttf", {0x4E16})


@pytest.mark.skipif(not have("ffmpeg", "ffprobe"), reason="ffmpeg not installed")
def test_fonts_in_an_hevc_mkv_are_cut_and_unused_ones_left_out(tmp_path, monkeypatch):
    from fontTools.ttLib import TTFont

    from wick.formats import video_output_ext
    from wick.plan import plan_entry
    from wick.probe import probe_video
    from wick.scanner import FileEntry

    sans = make_font(tmp_path / "sans.ttf", "Test Sans", CJK)
    serif = make_font(tmp_path / "serif.ttf", "Test Serif", [*CJK, 0x6A19, 0x8B58, 0x8349])
    unused = make_font(tmp_path / "unused.ttf", "Nobody Uses Me", CJK)
    (tmp_path / "subs.ass").write_text(ASS, "utf-8")
    src = tmp_path / "episode.mkv"
    # (attached by relative name: FFmpeg on Windows would store the full path as the font's name)
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=160x120:d=2", "-i", "subs.ass", "-map", "0", "-map", "1",
           "-pix_fmt", "yuv420p", "-c:v", "libx265", "-x265-params", "log-level=error", "-c:s", "copy",
           "-attach", sans.name, "-attach", serif.name, "-attach", unused.name,
           "-metadata:s:t", "mimetype=application/x-truetype-font", src.name, cwd=tmp_path)
    info = probe_video(src)
    monkeypatch.setattr(subfonts, "MIN_FONT_BYTES", 200_000)        # (real fonts are bigger: quicker to make)
    assert info is not None and subfonts.font_bytes(info) > subfonts.MIN_FONT_BYTES
    s = Settings()
    plan = plan_entry(FileEntry(src, src.stat().st_size, "video", 0, video=info, picked=True), s)
    assert plan.convert and plan.route == "video_remux" and "subtitle fonts" in plan.reason
    assert video_output_ext(src.suffix, info.codec) == plan.target_ext

    out, used = video.convert_video(src, info, plan, s)
    try:
        assert used == "copy" and "2 subtitle fonts cut" in plan.outcome and "1 unused font left out" in plan.outcome
        assert out.stat().st_size < src.stat().st_size / 3
        back = probe_video(out)
        fonts = [st for st in back.streams if subfonts.is_font(st)]
        assert sorted(st.filename for st in fonts) == ["sans.ttf", "serif.ttf"]
        assert "fonts cut" in video.made_by_wick(back)
        # The fonts as they're stored: the characters drawn are there.
        (tmp_path / "x").mkdir()
        try:
            ffmpeg("-dump_attachment:t", "", "-i", str(out), "-t", "0", "-f", "null", "-", cwd=tmp_path / "x")
        except Exception:  # noqa: BLE001 - (ffmpeg may complain about the output; the files count)
            pass
        cmap = set(TTFont(tmp_path / "x" / "serif.ttf").getBestCmap())
        assert {0x8349, 0x6A19, 0x8B58} <= cmap and 0x4E00 not in cmap
        # Done once: the next look skips it.
        again = plan_entry(FileEntry(out, out.stat().st_size, "video", 0, video=back, picked=True), s)
        assert not again.convert and again.reason == "Already compressed with Wick"
    finally:
        out.unlink(missing_ok=True)


def test_audio_already_small_is_copied_and_surround_gets_more():
    s = Settings(audio_bitrate=128)
    stereo_small = StreamInfo(1, "audio", "aac", channels=2, layout="stereo", bit_rate=96_000)
    stereo_big = StreamInfo(2, "audio", "ac3", channels=2, layout="stereo", bit_rate=384_000)
    surround = StreamInfo(3, "audio", "dts", channels=6, layout="5.1(side)", bit_rate=1_509_000)
    flac = StreamInfo(4, "audio", "flac", channels=2, layout="stereo", bit_rate=90_000)
    args = video._audio_args([stereo_small, stereo_big, surround, flac], s, mp4=False, fit=None)
    joined = " ".join(args)
    assert "-c:a:0" not in joined                                   # copied
    assert "-b:a:1 128k" in joined and "-b:a:2 256k" in joined and "-c:a:3 libopus" in joined
    assert "-filter:a:2 aformat=channel_layouts=7.1|" in joined     # (Opus doesn't take 5.1 with side speakers)
    assert video.audio_rate(128, 1) == 77 and video.audio_rate(128, 8) == 341 and video.audio_rate(96, 0) == 96
    mp4 = video._audio_args([StreamInfo(1, "audio", "opus", channels=2, bit_rate=64_000)], s, mp4=True, fit=None)
    assert mp4 == []                                                # (Opus plays in MP4 too)


@pytest.mark.skipif(not have("ffmpeg", "ffprobe"), reason="ffmpeg not installed")
def test_opus_takes_surround_with_side_speakers(tmp_path):
    from wick import tools

    src = tmp_path / "in.mkv"
    ffmpeg("-f", "lavfi", "-i", "anullsrc=cl=5.1(side):r=48000", "-t", "1", "-c:a", "ac3", str(src))
    st = StreamInfo(0, "audio", "ac3", channels=6, layout="5.1(side)", bit_rate=448_000)
    args = video._audio_args([st], Settings(audio_bitrate=128), mp4=False, fit=None)
    tools.run([tools.require("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-map", "0", "-c", "copy", *args,
               str(tmp_path / "out.mkv")])
    assert (tmp_path / "out.mkv").stat().st_size > 0


def test_banding_grain_is_light_and_nothing_is_removed(tmp_path, monkeypatch):
    info = VideoInfo("h264", 1920, 1080, "yuv420p", 24.0, 60.0, 5_000_000, 0, [StreamInfo(0, "video", "h264")])
    monkeypatch.setattr(video, "noise_sigma", lambda *a, **k: 0.5)                  # a clean picture
    assert video.grain_level(tmp_path / "x.mkv", info, Settings(video_grain="auto"), "libsvtav1") == 0
    level = video.grain_level(tmp_path / "x.mkv", info, Settings(video_grain="banding"), "libsvtav1")
    assert level == video.BANDING_GRAIN
    monkeypatch.setattr(video, "noise_sigma", lambda *a, **k: 4.0)                  # grainy: as before
    assert video.grain_level(tmp_path / "x.mkv", info, Settings(video_grain="banding"), "libsvtav1") == 14
    monkeypatch.setattr(video.tools, "require", lambda name: name)
    args = video.build_args(tmp_path / "x.mkv", tmp_path / "y.mkv", info, Settings(), "libsvtav1", 30,
                            grain=video.BANDING_GRAIN)
    assert "film-grain=4:film-grain-denoise=0" in args[args.index("-svtav1-params") + 1]
    from wick import quality

    assert quality._denoise_filter(video.BANDING_GRAIN) == "" and quality._denoise_filter(10)


def test_the_settings_tag_says_what_made_the_file(tmp_path, monkeypatch):
    from wick import __version__

    info = VideoInfo("h264", 1280, 720, "yuv420p", 30.0, 10.0, 2_000_000, 0, [StreamInfo(0, "video", "h264")])
    monkeypatch.setattr(video.tools, "require", lambda name: name)
    args = video.build_args(tmp_path / "x.mp4", tmp_path / "y.mp4", info, Settings(audio_bitrate=96), "libx265", 24)
    tag = args[args.index("-metadata", args.index("-c:v:0")) + 1]
    assert tag.startswith(f"WICK={__version__}; libx265 24; preset ") and "audio 96k" in tag
    assert "+faststart+use_metadata_tags" in args
    assert video.made_by_wick(VideoInfo("av1", 1, 1, "", 1, 1, 1, 0, tags={"WICK": "0.2.0"})) == "0.2.0"
    assert video.made_by_wick(info) == ""
