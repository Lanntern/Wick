"""JPEG extras: gain maps and other pictures after the photo kept, camera previews and EXIF thumbnails left out,
colour profiles made compact, Motion Photos rebuilt."""

import io
import struct
from pathlib import Path

import pytest

from wick import jpegparts
from wick.settings import Settings

from .conftest import ffmpeg, have


def _photo(size=(640, 480), seed=0):
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (size[1] // 8, size[0] // 8, 3), dtype=np.uint8)
    return Image.fromarray(np.repeat(np.repeat(base, 8, 0), 8, 1))


def _with_extra(path: Path, kind: str) -> Path:
    """A JPEG with a second picture after it (Pillow writes the MPF index), marked as a gain map (XMP hdrgm) or a
    camera preview (MPF type 0x010002)."""
    _photo().save(path, "MPO", save_all=True, append_images=[_photo((320, 240), 1)], quality=95)
    data = bytearray(path.read_bytes())
    base, endian, entries = jpegparts._mpf(bytes(data))
    if kind == "previews":
        rec = entries[1][3]
        struct.pack_into(endian + "I", data, rec, 0x010002)
    else:
        xmp = b'http://ns.adobe.com/xap/1.0/\0<x:xmpmeta><rdf:Description xmlns:hdrgm="http://ns.adobe.com/' \
              b'hdr-gain-map/1.0/" hdrgm:Version="1.0"/></x:xmpmeta>'
        seg = b"\xff\xe1" + struct.pack(">H", len(xmp) + 2) + xmp
        data = bytearray(data[:2] + seg + data[2:])
    path.write_bytes(bytes(data))
    return path


def _plan(path, s):
    from wick.formats import JPEG, read_image_info
    from wick.plan import plan_entry
    from wick.scanner import FileEntry

    info = read_image_info(path, JPEG)
    return info, plan_entry(FileEntry(path, path.stat().st_size, JPEG, 0, image=info, picked=True), s)


def _pictures(data: bytes) -> list[bytes]:
    """Every picture the MPF index points to, as stored."""
    base, endian, entries = jpegparts._mpf(data)
    out = [data[:entries[0][1]]]
    for _t, size, offset, _r in entries[1:]:
        out.append(data[base + offset:base + offset + size])
    return out


def test_a_gain_map_survives_and_the_index_points_at_it(tmp_path):
    from PIL import Image

    from wick.images import convert_image

    src = _with_extra(tmp_path / "hdr.jpg", "gainmap")
    original_extra = _pictures(src.read_bytes())[1]
    s = Settings(image_format="keep", jpeg_mode="lossless")
    info, plan = _plan(src, s)
    assert info.extras == "gainmap" and info.frames == 1 and plan.route == "jpeg_opt"
    out = convert_image(src, "jpeg", info, plan, s)
    try:
        data = out.read_bytes()
        pics = _pictures(data)
        assert pics[1] == original_extra and pics[1][:2] == b"\xff\xd8"
        assert jpegparts.primary_end(data) == len(pics[0])        # the index's size for the photo is right
        with Image.open(io.BytesIO(pics[0])) as a, Image.open(src) as b:
            assert a.tobytes() == b.tobytes()
    finally:
        out.unlink(missing_ok=True)
    _info, to_jxl = _plan(src, Settings(image_format="jxl"))
    assert to_jxl.route.startswith("jpeg") and "kept as JPEG to keep its HDR" in to_jxl.reason


def test_camera_previews_are_left_out(tmp_path):
    from wick.images import convert_image

    src = _with_extra(tmp_path / "camera.jpg", "previews")
    s = Settings(image_format="keep", jpeg_mode="lossless")
    info, plan = _plan(src, s)
    assert info.extras == "previews"
    out = convert_image(src, "jpeg", info, plan, s)
    try:
        data = out.read_bytes()
        assert jpegparts.primary_end(data) == len(data) and jpegparts._mpf(data) is None
        assert len(data) < src.stat().st_size * 0.8
    finally:
        out.unlink(missing_ok=True)
    kept = convert_image(src, "jpeg", info, plan, Settings(image_format="keep", jpeg_mode="lossless",
                                                           image_extras=False))
    try:
        assert len(_pictures(kept.read_bytes())) == 2
    finally:
        kept.unlink(missing_ok=True)


def _exif_with_thumbnail() -> bytes:
    """EXIF (TIFF) with a Make tag in IFD0 and a 2 KB thumbnail in IFD1, laid out as cameras write it."""
    thumb = io.BytesIO()
    _photo((160, 120), 3).save(thumb, "JPEG", quality=90)
    thumb = thumb.getvalue()
    make = b"Camera\0"
    ifd0 = 8
    ifd0_size = 2 + 12 + 4
    make_at = ifd0 + ifd0_size
    ifd1 = make_at + len(make)
    ifd1_size = 2 + 2 * 12 + 4
    thumb_at = ifd1 + ifd1_size
    out = b"II*\0" + struct.pack("<I", ifd0)
    out += struct.pack("<H", 1) + struct.pack("<HHII", 0x010F, 2, len(make), make_at) + struct.pack("<I", ifd1)
    out += make
    out += struct.pack("<H", 2) + struct.pack("<HHII", 0x0201, 4, 1, thumb_at) + \
        struct.pack("<HHII", 0x0202, 4, 1, len(thumb)) + struct.pack("<I", 0)
    return out + thumb


def test_the_exif_thumbnail_is_left_out_and_the_rest_kept():
    from PIL import Image

    exif = _exif_with_thumbnail()
    slim = jpegparts.exif_without_thumbnail(exif)
    assert len(slim) < len(exif) - 1500
    buf = io.BytesIO()
    _photo((64, 64)).save(buf, "JPEG", exif=b"Exif\0\0" + slim)
    with Image.open(buf) as im:
        assert im.getexif()[0x010F] == "Camera"
    assert jpegparts.exif_without_thumbnail(slim) == slim        # (nothing left to take)


def test_an_srgb_profile_in_disguise_becomes_compact():
    import pikepdf
    from PIL import ImageCms

    srgb = (Path(pikepdf.__file__).parent / "pdfa" / "data" / "sRGB.icc").read_bytes()
    compact = jpegparts.compact_icc(srgb)
    assert compact is not None and len(compact) < len(srgb) - 1024
    assert jpegparts.compact_icc(compact) is None
    lab = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
    assert jpegparts.compact_icc(lab) is None


@pytest.mark.skipif(not have("ffmpeg", "ffprobe"), reason="ffmpeg not installed")
def test_a_motion_photo_is_rebuilt_smaller_with_its_clip(tmp_path):
    from PIL import Image

    from wick import motionphoto
    from wick.images import convert_image

    clip = tmp_path / "clip.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=320x240:d=2", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "10",
           str(clip))
    clip_bytes = clip.read_bytes()
    xmp = (b'http://ns.adobe.com/xap/1.0/\0<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
           b'GCamera:MotionPhoto="1" GCamera:MicroVideoOffset="' + str(len(clip_bytes)).encode() + b'">'
           b'<Container:Directory><rdf:Seq><rdf:li><Container:Item Item:Semantic="Primary" Item:Mime="image/jpeg"/>'
           b'</rdf:li><rdf:li><Container:Item Item:Semantic="MotionPhoto" Item:Mime="video/mp4" Item:Length="'
           + str(len(clip_bytes)).encode() + b'"/></rdf:li></rdf:Seq></Container:Directory></rdf:Description>'
           b'</rdf:RDF></x:xmpmeta>')
    photo = io.BytesIO()
    _photo().save(photo, "JPEG", quality=95, optimize=False)
    jpeg = photo.getvalue()
    jpeg = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(xmp) + 2) + xmp + jpeg[2:]
    src = tmp_path / "PXL_20240101_MP.jpg"
    src.write_bytes(jpeg + clip_bytes)
    assert motionphoto.clip_length(src.read_bytes()) == len(clip_bytes)
    s = Settings(image_format="keep", jpeg_mode="lossless", video_encoder="cpu")   # (x265: a known size)
    info, plan = _plan(src, s)
    assert info.motion and plan.convert and plan.route == "motion_photo", plan.reason
    out = convert_image(src, "jpeg", info, plan, s)
    try:
        data = out.read_bytes()
        n = motionphoto.clip_length(data)
        assert n and n < len(clip_bytes) and len(data) < src.stat().st_size
        with Image.open(io.BytesIO(data[:-n])) as a, Image.open(io.BytesIO(jpeg)) as b:
            assert a.tobytes() == b.tobytes()
        assert "clip re-encoded as HEVC" in plan.outcome
    finally:
        out.unlink(missing_ok=True)


def _boxes(*boxes: tuple[bytes, bytes]) -> bytes:
    return b"".join(struct.pack(">I", 8 + len(body)) + kind + body for kind, body in boxes)


def test_heic_hdr_and_multi_picture_photos_are_left_alone(tmp_path):
    from wick.formats import HEIC, ImageInfo, heif_extras
    from wick.plan import plan_entry
    from wick.scanner import FileEntry

    ftyp = (b"ftyp", b"heic\0\0\0\0mif1heic")
    apple = (b"meta", b"\0\0\0\0" + _boxes((b"iprp", b"auxCurn:com:apple:photo:2020:aux:hdrgainmap\0")))
    iso = (b"meta", b"\0\0\0\0" + _boxes((b"iinf", b"infe\x02\x00\x00\x00\x00\x05\x00\x00tmap")))
    data = (b"mdat", b"tmap" * 100)                        # (picture data that happens to hold the letters)
    cases = {"apple-meta-last.heic": (ftyp, data, apple), "iso.heic": (ftyp, iso, data),
             "plain.heic": (ftyp, (b"meta", b"\0\0\0\0"), data)}
    for name, boxes in cases.items():
        (tmp_path / name).write_bytes(_boxes(*boxes))
    assert heif_extras(tmp_path / "apple-meta-last.heic") == "gainmap"      # (meta after the picture data)
    assert heif_extras(tmp_path / "iso.heic") == "gainmap"
    assert heif_extras(tmp_path / "plain.heic") == ""
    hdr = tmp_path / "iso.heic"
    info = ImageInfo(4000, 3000, lossless_source=False, extras="gainmap")
    for fmt in ("keep", "jxl", "avif"):
        plan = plan_entry(FileEntry(hdr, 5_000_000, HEIC, 0, image=info, picked=True), Settings(image_format=fmt))
        assert not plan.convert and "HDR" in plan.reason
    info.extras = "other"
    plan = plan_entry(FileEntry(hdr, 5_000_000, HEIC, 0, image=info, picked=True), Settings())
    assert not plan.convert and "several views" in plan.reason


def test_a_samsung_trailer_alone_isnt_a_motion_photo(tmp_path):
    from wick.formats import has_embedded_video

    jpeg = io.BytesIO()
    _photo((64, 64)).save(jpeg, "JPEG")
    plain = tmp_path / "samsung.jpg"
    plain.write_bytes(jpeg.getvalue() + b"\0" * 64 + b"Image_UTC_Data1700000000000SEFHxxxxSEFT")
    assert not has_embedded_video(plain)
    motion = tmp_path / "motion.jpg"
    motion.write_bytes(jpeg.getvalue() + b"MotionPhoto_Data" + b"\0" * 64 + b"SEFT")
    assert has_embedded_video(motion)
