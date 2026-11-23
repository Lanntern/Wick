"""Audio: talk recognised and stored as mono at a lower bitrate, album art stored smaller, the WICK tag. Images:
the made-by-Wick mark in JPEG and PNG."""

import io

import pytest

from wick import audio, marker
from wick.plan import plan_entry
from wick.probe import probe_audio
from wick.scanner import FileEntry
from wick.settings import Settings

from .conftest import ffmpeg, have

needs_ffmpeg = pytest.mark.skipif(not have("ffmpeg", "ffprobe"), reason="ffmpeg not installed")

# Voice-like: harmonics of a 150 Hz voice, under 3 kHz, half a second on and 0.3 s off. Music-like: chords with
# a full-band noise floor, never silent.
TALK = ("aevalsrc='if(lt(mod(t,0.8),0.5),0.2*(sin(2*PI*150*t)+0.6*sin(2*PI*450*t)+0.4*sin(2*PI*1200*t)"
        "+0.2*sin(2*PI*2400*t))*(0.6+0.4*sin(2*PI*4*t)),0)':s=16000:d={d}")
MUSIC = "aevalsrc='0.2*sin(2*PI*220*t)+0.2*sin(2*PI*277*t)+0.2*sin(2*PI*330*t)+0.05*(random(0)-0.5)':s=44100:d={d}"


def _entry(path, info):
    return FileEntry(path, path.stat().st_size, "audio", 0, audio=info, picked=True)


@needs_ffmpeg
def test_talk_is_recognised_and_music_is_not(tmp_path):
    talk, music = tmp_path / "talk.flac", tmp_path / "music.flac"
    ffmpeg("-f", "lavfi", "-i", TALK.format(d=610), str(talk))
    ffmpeg("-f", "lavfi", "-i", MUSIC.format(d=610), str(music))
    assert audio.is_speech(talk, probe_audio(talk))
    assert not audio.is_speech(music, probe_audio(music))
    rests = tmp_path / "rests.flac"            # music with a few long rests (a slow classical piece)
    ffmpeg("-f", "lavfi", "-i", "aevalsrc='if(lt(mod(t,20),16),0.3*sin(2*PI*220*t),0)':s=16000:d=610", str(rests))
    assert not audio.is_speech(rests, probe_audio(rests))
    short = tmp_path / "short.flac"
    ffmpeg("-f", "lavfi", "-i", TALK.format(d=60), str(short))
    assert not audio.is_speech(short, probe_audio(short))                   # (songs are short: not checked)


@needs_ffmpeg
def test_a_podcast_becomes_mono_opus_at_the_speech_rate_and_is_tagged(tmp_path):
    src = tmp_path / "episode.flac"
    ffmpeg("-f", "lavfi", "-i", TALK.format(d=610), "-ac", "2", str(src))
    s = Settings(audio_lossless="lossy", audio_format="opus", audio_quality="balanced")
    info = probe_audio(src)
    plan = plan_entry(_entry(src, info), s)
    assert plan.convert and plan.speech and "48 kbps mono (speech)" in plan.reason
    out = audio.convert_audio(src, info, plan, s)
    try:
        got = probe_audio(out)
        assert got.channels == 1 and audio.made_by_wick(got) and "speech" in plan.outcome
        again = plan_entry(_entry(out, got), s)
        assert not again.convert and again.reason == "Already compressed with Wick"
    finally:
        out.unlink(missing_ok=True)
    assert not plan_entry(_entry(src, info), Settings(audio_lossless="lossy", audio_speech=False)).speech


def _big_png(path):
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(1)
    # (smooth enough to compress, saved without compression: about 750 KB that shrink a lot, losslessly)
    base = np.repeat(np.repeat(rng.integers(0, 255, (50, 50, 3), dtype=np.uint8), 10, 0), 10, 1)
    Image.fromarray(base).save(path, compress_level=0)
    return path


@needs_ffmpeg
def test_big_album_art_is_stored_smaller_and_the_sound_is_untouched(tmp_path):
    cover = _big_png(tmp_path / "cover.png")
    src = tmp_path / "song.mp3"
    ffmpeg("-f", "lavfi", "-i", MUSIC.format(d=20), "-i", str(cover), "-map", "0", "-map", "1", "-c:a",
           "libmp3lame", "-b:a", "96k", "-c:v", "copy", "-disposition:v:0", "attached_pic", "-id3v2_version", "3",
           str(src))
    info = probe_audio(src)
    s = Settings()                                                  # (compressed audio isn't re-encoded)
    plan = plan_entry(_entry(src, info), s)
    assert plan.convert and plan.route == "audio_art", plan.reason
    out = audio.convert_audio(src, info, plan, s)
    try:
        assert out.suffix == ".mp3" and out.stat().st_size < src.stat().st_size - 200_000
        assert audio.pcm_md5(src, "0:a:0") == audio.pcm_md5(out, "0:a:0")
        got = probe_audio(out)
        assert got.cover_index >= 0 and audio.made_by_wick(got)
        from PIL import Image

        from wick import tools

        art = tools.run([tools.require("ffmpeg"), "-v", "error", "-i", str(out), "-map", "0:v:0", "-c", "copy",
                         "-f", "image2pipe", "-"], text=False).stdout
        with Image.open(io.BytesIO(art)) as a, Image.open(cover) as b:
            assert a.tobytes() == b.convert(a.mode).tobytes()        # same pixels
    finally:
        out.unlink(missing_ok=True)


def test_jpeg_and_png_carry_the_mark_without_changing_a_pixel(tmp_path):
    from PIL import Image

    png = _big_png(tmp_path / "a.png")
    jpg = tmp_path / "a.jpg"
    with Image.open(png) as im:
        im.save(jpg, quality=90, exif=Image.Exif())
        pixels = im.tobytes()
    with Image.open(jpg) as im:
        jpeg_pixels = im.tobytes()
    for path, before in ((png, pixels), (jpg, jpeg_pixels)):
        assert not marker.has(path)
        assert marker.add(path) and marker.has(path) and not marker.add(path)
        with Image.open(path) as im:
            assert im.tobytes() == before
    s = Settings(image_format="keep")
    from wick.formats import JPEG, read_image_info

    entry = FileEntry(jpg, jpg.stat().st_size, JPEG, 0, image=read_image_info(jpg, JPEG), picked=True)
    assert plan_entry(entry, s).reason == "Already compressed with Wick"


@needs_ffmpeg
def test_every_picture_is_kept_and_stored_smaller(tmp_path):
    front, back = _big_png(tmp_path / "front.png"), tmp_path / "back.png"
    from PIL import Image

    Image.new("RGB", (900, 900), (30, 60, 90)).save(back, compress_level=0)
    src = tmp_path / "album.flac"
    ffmpeg("-f", "lavfi", "-i", MUSIC.format(d=5), "-i", str(front), "-i", str(back), "-map", "0", "-map", "1",
           "-map", "2", "-c:v", "copy", "-disposition:v", "attached_pic", "-metadata:s:v:0", "comment=Cover (front)",
           "-metadata:s:v:1", "comment=Cover (back)", str(src))
    info = probe_audio(src)
    assert len(audio.pictures_of(info)) == 2
    plan = plan_entry(_entry(src, info), Settings(audio_lossless="flac"))
    out = audio.convert_audio(src, info, plan, Settings(audio_lossless="flac"))
    try:
        got = probe_audio(out)
        pics = audio.pictures_of(got)
        assert len(pics) == 2 and {p[2].get("comment") for p in pics} == {"Cover (front)", "Cover (back)"}
        assert "album art stored smaller" in plan.outcome
    finally:
        out.unlink(missing_ok=True)


@needs_ffmpeg
def test_upsampled_hi_res_is_told_from_the_real_thing(tmp_path):
    fake, real = tmp_path / "fake.flac", tmp_path / "real.flac"
    # CD audio made 96 kHz: nothing above 22 kHz. Real hi-res: some sound up there, however quiet.
    ffmpeg("-f", "lavfi", "-i", "anoisesrc=c=pink:r=44100:d=40:a=0.3", "-ar", "96000", "-sample_fmt", "s32",
           str(fake))
    ffmpeg("-f", "lavfi", "-i", "anoisesrc=c=pink:r=96000:d=40:a=0.3", "-sample_fmt", "s32", str(real))
    assert audio.upsampled_from(fake, probe_audio(fake)) == 48000      # (the 96 kHz family's base)
    assert audio.upsampled_from(real, probe_audio(real)) == 0
    s = Settings(audio_lossless="flac")
    plan = plan_entry(_entry(fake, probe_audio(fake)), s)
    assert plan.audio_rate == 48000 and "upsampled" in plan.reason
    out = audio.convert_audio(fake, probe_audio(fake), plan, s)
    try:
        assert probe_audio(out).sample_rate == 48000
    finally:
        out.unlink(missing_ok=True)
    assert plan_entry(_entry(fake, probe_audio(fake)), Settings(audio_lossless="flac",
                                                                audio_fake_hires=False)).audio_rate == 0


def _pad_id3(path, pad: int) -> None:
    """Grow the ID3v2 tag at the start of ``path`` by ``pad`` zero bytes (as some taggers leave it)."""
    data = path.read_bytes()
    assert data[:3] == b"ID3"
    size = sum((b & 0x7F) << (7 * (3 - i)) for i, b in enumerate(data[6:10]))
    new = size + pad
    head = data[:6] + bytes((new >> (7 * (3 - i))) & 0x7F for i in range(4))
    path.write_bytes(head + data[10:10 + size] + b"\0" * pad + data[10 + size:])


@needs_ffmpeg
@pytest.mark.parametrize("ext", [".mp3", ".flac"])
def test_big_tag_padding_is_removed_and_nothing_else_changes(tmp_path, ext):
    src = tmp_path / f"song{ext}"
    ffmpeg("-f", "lavfi", "-i", MUSIC.format(d=10), "-metadata", "title=Song", "-metadata", "artist=Band",
           *(["-c:a", "libmp3lame", "-b:a", "96k", "-id3v2_version", "3"] if ext == ".mp3" else []), str(src))
    if ext == ".mp3":
        _pad_id3(src, 300_000)
    else:                                      # (a FLAC with a big PADDING block, as some taggers write)
        data = bytearray(src.read_bytes())
        pos = 4
        while not data[pos] & 0x80:
            pos += 4 + int.from_bytes(data[pos + 1:pos + 4], "big")
        data[pos] &= 0x7F
        end = pos + 4 + int.from_bytes(data[pos + 1:pos + 4], "big")
        block = bytes([0x81]) + (300_000).to_bytes(3, "big") + b"\0" * 300_000
        src.write_bytes(bytes(data[:end]) + block + bytes(data[end:]))
    assert audio.padding(src) >= 300_000
    s = Settings(audio_lossless="keep", audio_lossy="reencode")      # (both off would leave audio alone)
    info = probe_audio(src)
    plan = plan_entry(_entry(src, info), s)
    assert plan.convert and plan.route == "audio_padding", plan.reason
    out = audio.convert_audio(src, info, plan, s)
    try:
        assert out.stat().st_size < src.stat().st_size - 290_000 and audio.padding(out) < 10_000
        assert probe_audio(out).tags == info.tags
    finally:
        out.unlink(missing_ok=True)


@needs_ffmpeg
def test_m4a_keeps_its_album_art(tmp_path):
    cover = _big_png(tmp_path / "cover.png")
    src = tmp_path / "song.m4a"
    ffmpeg("-f", "lavfi", "-i", MUSIC.format(d=10), "-i", str(cover), "-map", "0", "-map", "1", "-c:a", "aac",
           "-b:a", "96k", "-c:v", "copy", "-disposition:v:0", "attached_pic", str(src))
    info = probe_audio(src)
    s = Settings(audio_lossy="reencode", audio_format="aac")          # (96k AAC: already efficient)
    plan = plan_entry(_entry(src, info), s)
    assert plan.route == "audio_art", plan.reason
    out = audio.convert_audio(src, info, plan, s)
    try:
        assert len(audio.pictures_of(probe_audio(out))) == 1
    finally:
        out.unlink(missing_ok=True)
