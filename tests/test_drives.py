"""Spinning drives are read one file at a time; duplicates are hashed in parallel elsewhere (wick/drives.py)."""

from pathlib import Path

from wick import drives
from wick.cleanup import duplicates


def test_one_reader_when_any_file_is_on_a_spinning_drive(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    monkeypatch.setattr(drives, "spinning", lambda p: False)
    assert drives.readers([a, b], 4) == 4
    monkeypatch.setattr(drives, "spinning", lambda p: True)
    assert drives.readers([a, b], 4) == 1
    assert drives.readers([tmp_path / "missing"], 4) == 4              # (unreadable: no change)
    assert drives.spinning(tmp_path) in (True, False)                  # (asks the real system without failing)


def test_duplicates_found_the_same_way_in_parallel(tmp_path, monkeypatch):
    for i in range(6):
        (tmp_path / f"same{i}.bin").write_bytes(b"A" * 300_000)
    (tmp_path / "other.bin").write_bytes(b"A" * 299_999 + b"B")      # same size, differs at the end
    for readers in (1, 4):
        monkeypatch.setattr(drives, "readers", lambda paths, n, r=readers: r)
        result = duplicates.find_duplicates([tmp_path], min_size=1)
        dupes = {Path(c.path).name for c in result.items}
        assert len(result.items) == 5 and "other.bin" not in dupes


def test_blake3_is_used_when_installed():
    h = duplicates._hasher()
    h.update(b"wick")
    assert len(h.digest()) == 32
