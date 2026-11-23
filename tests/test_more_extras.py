"""SVG cleanup, pictures inside ZIP files, and duplicate subtitle tracks."""

import io
import zipfile

import pytest

from wick.settings import Settings

from .conftest import ffmpeg, have

INKSCAPE_SVG = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!-- Created with Inkscape (http://www.inkscape.org/) -->
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
   xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
   xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:cc="http://creativecommons.org/ns#"
   width="200" height="100" viewBox="0 0 200 100" inkscape:version="1.3" sodipodi:docname="logo.svg">
  <sodipodi:namedview id="namedview1" pagecolor="#ffffff" inkscape:zoom="2.5" inkscape:cx="100" inkscape:cy="50">
    <inkscape:grid id="grid1" type="xygrid" />
  </sodipodi:namedview>
  <metadata id="metadata1">
    <rdf:RDF><cc:Work rdf:about=""><dc:format>image/svg+xml</dc:format><dc:title>Logo</dc:title></cc:Work></rdf:RDF>
  </metadata>
  <g inkscape:label="Layer 1" inkscape:groupmode="layer" id="layer1">
    <rect x="10" y="10" width="80" height="80" fill="#3070c0" inkscape:transform-center-x="3" />
    <circle cx="150" cy="50" r="30" fill="#e04040" sodipodi:nodetypes="cccc" />
    <text x="20" y="95" font-size="8"><tspan>Hello</tspan>
      <tspan>world</tspan></text>
  </g>
</svg>
""" + "<!-- " + "padding " * 2000 + "-->\n"


def test_an_inkscape_drawing_is_cleaned_and_checked_in_the_worker(tmp_path):
    from wick import sandbox
    from wick.engine import _clean_svg

    if not sandbox.available():
        pytest.skip("decoder worker not available")
    src = tmp_path / "logo.svg"
    src.write_text(INKSCAPE_SVG)
    out = _clean_svg(src)
    try:
        cleaned = out.read_text()
        for gone in ("inkscape:", "sodipodi:", "<metadata", "<!--"):
            assert gone not in cleaned, gone
        assert out.stat().st_size < src.stat().st_size / 2 and sandbox.svg_same(src, out)
    finally:
        out.unlink(missing_ok=True)
    other = tmp_path / "other.svg"
    other.write_text(INKSCAPE_SVG.replace("#e04040", "#40e040"))
    assert not sandbox.svg_same(src, other)
    broken = tmp_path / "broken.svg"
    broken.write_text("<svg" + " " * 20000)
    from wick.images import NotWorthIt

    with pytest.raises(NotWorthIt):
        _clean_svg(broken)


def test_svgs_and_zips_are_planned_when_their_options_are_on(tmp_path):
    from wick.formats import DOCUMENT, SVG
    from wick.plan import plan_entry
    from wick.scanner import FileEntry, _kind

    (tmp_path / "logo.svg").write_text(INKSCAPE_SVG)
    (tmp_path / "photos.zip").write_bytes(b"PK")
    s = Settings(archive_pictures=True)
    assert _kind(tmp_path / "logo.svg", s) == SVG and _kind(tmp_path / "photos.zip", s) == DOCUMENT
    assert _kind(tmp_path / "photos.zip", Settings()) is None                     # (off by default)
    svg = tmp_path / "logo.svg"
    plan = plan_entry(FileEntry(svg, svg.stat().st_size, SVG, 0, picked=True), s)
    assert plan.convert and plan.route == "svg"


def _png(seed: int) -> bytes:
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    img = np.repeat(np.repeat(rng.integers(0, 255, (20, 30, 3), dtype=np.uint8), 20, 0), 20, 1)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG", compress_level=0)
    return buf.getvalue()


def test_pictures_inside_a_zip_shrink_and_everything_else_stays(tmp_path):
    from PIL import Image

    from wick import documents

    src = tmp_path / "holiday.zip"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_STORED) as z:
        z.writestr("day1/a.png", _png(1))
        z.writestr("day1/notes.txt", "Beach")
        z.writestr("b.png", _png(2))
    out = documents.optimize_office(src)
    try:
        with zipfile.ZipFile(src) as a, zipfile.ZipFile(out) as b:
            assert a.namelist() == b.namelist() and b.read("day1/notes.txt") == b"Beach"
            for name in ("day1/a.png", "b.png"):
                assert len(b.read(name)) < len(a.read(name))
                with Image.open(io.BytesIO(a.read(name))) as x, Image.open(io.BytesIO(b.read(name))) as y:
                    assert x.convert("RGB").tobytes() == y.convert("RGB").tobytes()
    finally:
        out.unlink(missing_ok=True)
    locked = tmp_path / "locked.zip"
    data = bytearray(src.read_bytes())
    data[data.find(b"PK\x01\x02") + 8] |= 0x1        # (the encrypted flag, in the central directory)
    locked.write_bytes(bytes(data))
    with pytest.raises(documents.Unsupported):
        documents.optimize_office(locked)


@pytest.mark.skipif(not have("ffmpeg", "ffprobe"), reason="ffmpeg not installed")
def test_duplicate_subtitle_tracks_are_left_out(tmp_path):
    from wick import video
    from wick.probe import probe_video

    srt = "1\n00:00:00,500 --> 00:00:01,500\nHello there\n\n2\n00:00:01,600 --> 00:00:02,500\nBye\n"
    (tmp_path / "a.srt").write_text(srt)
    (tmp_path / "b.srt").write_text(srt.replace("Hello there", "<i>Hello there</i>"))   # (same lines, styled)
    (tmp_path / "c.srt").write_text(srt.replace("Bye", "Goodbye"))
    src = tmp_path / "film.mkv"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=160x120:d=3", "-i", "a.srt", "-i", "b.srt", "-i", "c.srt",
           "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:s", "srt", "-metadata:s:s", "language=eng", "film.mkv", cwd=tmp_path)
    info = probe_video(src)
    video.find_duplicate_subtitles(src, info)
    assert [t.duplicate for t in info.subtitle_streams()] == [False, True, False]
    s = Settings(video_dup_audio=True)
    keep_a, keep_s, _dropped = video.select_tracks(info, s)
    assert len(keep_s) == 2 and "1 duplicate subtitle track left out" in video.tracks_note(info, s)
    assert len(video.select_tracks(info, Settings())[1]) == 3
