"""The one folder walk every scan uses (wick/walk.py)."""

import os
import threading

import pytest

from wick import walk


def _tree(root):
    for d in ("b/inner", "a", "c"):
        (root / d).mkdir(parents=True)
    for f in ("top.txt", "a/one.txt", "b/two.txt", "b/inner/three.txt", "c/four.txt"):
        (root / f).write_text("x")


def test_every_folder_once_in_name_order_with_its_files(tmp_path):
    _tree(tmp_path)
    got = [(f.path.relative_to(tmp_path).as_posix(), sorted(e.name for e in f.files()))
           for f in walk.folders([tmp_path], ordered=True)]
    assert got == [(".", ["top.txt"]), ("a", ["one.txt"]), ("b", ["two.txt"]), ("b/inner", ["three.txt"]),
                   ("c", ["four.txt"])]
    # Shared "seen": a second walk over the same folders lists nothing again.
    seen: set[str] = set()
    assert len(list(walk.folders([tmp_path], seen=seen))) == 5
    assert list(walk.folders([tmp_path / "b"], seen=seen)) == []


def test_skipping_and_choosing_which_folders_to_enter(tmp_path):
    _tree(tmp_path)
    names = []
    for f in walk.folders([tmp_path], enter=lambda e: e.name != "c", ordered=True):
        names.append(f.path.name)
        if f.path.name == "b":
            f.skip()                       # (not into b/inner)
    assert names == [tmp_path.name, "a", "b"]


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_links_are_never_followed(tmp_path):
    _tree(tmp_path)
    (tmp_path / "a" / "loop").symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / "a" / "linked.txt").symlink_to(tmp_path / "top.txt")
    folders = list(walk.folders([tmp_path]))
    assert len(folders) == 5                                      # (the loop isn't entered)
    a = next(f for f in folders if f.path.name == "a")
    assert sorted(e.name for e in a.files()) == ["one.txt"]       # (a link to a file isn't a file of its own)
    assert walk.is_link(next(e for e in a.entries if e.name == "loop"))


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs a folder this user can't read")
def test_unreadable_folders_are_reported_and_passed_over(tmp_path):
    _tree(tmp_path)
    (tmp_path / "b").chmod(0)
    try:
        missed = []
        names = [f.path.name for f in walk.folders([tmp_path], unreadable=missed.append)]
        assert missed == [tmp_path / "b"] and "b" not in names and "c" in names
    finally:
        (tmp_path / "b").chmod(0o755)


def test_a_cancel_stops_between_folders(tmp_path):
    _tree(tmp_path)
    cancel = threading.Event()
    seen = []
    for f in walk.folders([tmp_path], cancel=cancel, ordered=True):
        seen.append(f.path.name)
        cancel.set()
    assert seen == [tmp_path.name]
