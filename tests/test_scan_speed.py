"""Scanning with fewer looks at the disk: repeated frames only looked for when they change the plan, the file-details
cache kept fresh and saved as it fills, one open per picture, Live Photos paired from names already listed."""

import builtins
import time
from pathlib import Path

import pytest
from PIL import Image

from wick import index, scanner
from wick.formats import HEIC, JPEG, PNG, VIDEO
from wick.probe import VideoInfo
from wick.scanner import FileEntry
from wick.settings import Settings


def test_repeated_frames_are_looked_for_while_scanning_only_where_they_change_the_plan():
    def video(codec):
        return VideoInfo(codec, 1920, 1080, "yuv420p", 30.0, 60.0, 8_000_000, 0)

    assert scanner.cadence_at_scan(video("hevc")) and scanner.cadence_at_scan(video("av1"))
    assert not scanner.cadence_at_scan(video("h264")) and not scanner.cadence_at_scan(video("mpeg2video"))


def test_hdr_details_come_from_the_stream_information_when_its_there(monkeypatch):
    from wick import probe, tools

    def no_second_run(*a, **k):
        raise AssertionError("ffprobe ran again")

    monkeypatch.setattr(tools, "run", no_second_run)
    side = [{"side_data_type": "Mastering display metadata", "red_x": "34000/50000", "max_luminance": "10000000/10000"},
            {"side_data_type": "Content light level metadata", "max_content": 1000, "max_average": 400}]
    got = probe._mastering(Path("film.mkv"), 0, side)
    assert got["red_x"] == pytest.approx(0.68) and got["max_luminance"] == 1000 and got["max_content"] == 1000


def test_the_details_cache_keeps_files_that_are_still_being_scanned(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(p)
    st = p.stat()
    info = scanner.read_image_info(p, PNG)
    index.store([(p, st.st_size, st.st_mtime, PNG, info)])
    db = index._db()
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 1          # NORMAL: no flush on every save
    key = index._key(p)
    db.execute("UPDATE files SET seen=? WHERE path=?", (time.time() - 400 * 86400, key))
    db.commit()
    assert index.lookup(p, st.st_size, st.st_mtime, PNG) is not None
    index.refresh_seen()
    assert index.prune(365) == 0
    assert index.lookup(p, st.st_size, st.st_mtime, PNG) is not None


def test_file_details_are_saved_while_the_scan_runs(tmp_path, monkeypatch):
    for i in range(450):
        Image.new("RGB", (4, 4), (i % 256, 0, 0)).save(tmp_path / f"{i}.png")
    saves = []
    monkeypatch.setattr(index, "store", lambda items: saves.append(len(items)))
    monkeypatch.setattr(index, "lookup", lambda *a: None)
    scanner.scan([tmp_path], Settings(min_image_kb=0))
    assert len(saves) >= 3 and sum(saves) == 450


def test_a_picture_is_opened_once_while_scanning(tmp_path, monkeypatch):
    jpg, png = tmp_path / "photo.jpg", tmp_path / "shot.jpg"
    Image.new("RGB", (64, 48), (200, 10, 10)).save(jpg, quality=90)
    Image.new("RGB", (32, 32)).save(png, format="PNG")                    # (a PNG named .jpg)
    monkeypatch.setattr(index, "lookup", lambda *a: None)
    opened = []
    real_open = builtins.open

    def counting_open(file, *a, **k):
        if str(file).startswith(str(tmp_path)):
            opened.append(Path(file).name)
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", counting_open)
    entries = [FileEntry(jpg, jpg.stat().st_size, JPEG, 0, tmp_path), FileEntry(png, png.stat().st_size, JPEG, 0)]
    scanner.probe_entries(entries)
    assert sorted(opened) == ["photo.jpg", "shot.jpg"]
    assert (entries[0].image.width, entries[0].image.height) == (64, 48) and entries[0].image.extras is not None
    assert entries[1].kind == PNG and entries[1].real_ext == ".png" and entries[1].image.width == 32


def test_live_photos_are_paired_from_the_names_already_listed(tmp_path, monkeypatch):
    folder = tmp_path / "DCIM"
    photo, clip, alone = folder / "IMG_1.HEIC", folder / "IMG_1.MOV", folder / "IMG_2.JPG"
    cloud_photo = folder / "IMG_3.HEIC"
    entries = [FileEntry(photo, 1, HEIC, 0, tmp_path), FileEntry(clip, 1, VIDEO, 0, tmp_path),
               FileEntry(alone, 1, JPEG, 0, tmp_path), FileEntry(folder / "IMG_3.MOV", 1, VIDEO, 0, tmp_path)]

    def no_disk(self, *a, **k):
        raise AssertionError(f"looked at the disk for {self}")

    monkeypatch.setattr(Path, "is_file", no_disk)
    scanner.mark_live_photos(entries, [cloud_photo])
    assert [e.live_pair for e in entries] == [True, True, False, True]
    monkeypatch.undo()

    # Chosen by itself (not found in a folder): its partner is looked up.
    folder.mkdir()
    photo.write_bytes(b"x")
    clip.write_bytes(b"x")
    single = [FileEntry(photo, 1, HEIC, 0)]
    scanner.mark_live_photos(single)
    assert single[0].live_pair


def test_files_the_scan_cant_use_get_no_further_look(tmp_path, monkeypatch):
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "data.bin").write_bytes(b"x")
    Image.new("RGB", (4, 4)).save(tmp_path / "a.png")
    looked = []
    real = scanner.is_hidden
    monkeypatch.setattr(scanner, "is_hidden", lambda e: looked.append(e.name) or real(e))
    result = scanner.scan([tmp_path], Settings(min_image_kb=0), probe=False)
    assert [e.path.name for e in result.entries] == ["a.png"] and looked == ["a.png"]
