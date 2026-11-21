"""The decoder worker (wick/sandbox.py, wick/_decoders.py): files Wick didn't make are decoded in a separate
process with fewer rights, and whatever it sends back is checked before use."""

import os
import time
from pathlib import Path

import pytest
from PIL import Image

from wick import sandbox

from .conftest import have


@pytest.fixture(autouse=True)
def fresh_workers():
    sandbox.shutdown()
    yield
    sandbox.shutdown()


def test_pictures_come_back_as_checked_raw_pixels(tmp_path):
    Image.new("RGB", (800, 600), (10, 200, 30)).save(tmp_path / "a.jpg")
    got = sandbox.rgba("thumbnail", str(tmp_path / "a.jpg"), "jpeg", 128, max_pixels=128 * 128, timeout=60)
    assert got is not None
    w, h, pixels = got
    assert (w, h) == (128, 96) and len(pixels) == w * h * 4
    assert pixels[:3] in (bytes([10, 200, 30]), bytes([10, 200, 31]), bytes([11, 200, 30]), bytes([10, 199, 30]))
    assert sandbox.available() and len(sandbox._all) == 1       # (a worker did it, not Wick)
    applied = next(iter(sandbox._all)).applied
    assert "error" not in applied
    if os.name == "nt":
        assert applied.get("integrity") == "low" and applied.get("job") is True
    elif os.uname().sysname == "Linux":
        assert applied.get("no_new_privs") is True


def test_workers_are_reused_and_a_reply_larger_than_allowed_is_refused(tmp_path):
    Image.new("RGB", (400, 400), (200, 10, 30)).save(tmp_path / "b.png")
    assert sandbox.rgba("display_image", str(tmp_path / "b.png"), 10**6, max_pixels=10**6, timeout=60)
    first = next(iter(sandbox._all))
    t = time.monotonic()
    assert sandbox.rgba("display_image", str(tmp_path / "b.png"), 10**6, max_pixels=10**6, timeout=60)
    assert time.monotonic() - t < 5 and sandbox._all == {first}
    # The worker sends 400×400 RGBA; the caller allows less: refused, and that worker isn't trusted again.
    with pytest.raises(sandbox.DecoderFailed):
        sandbox.call("display_image", str(tmp_path / "b.png"), 10**6, timeout=60, max_bytes=1000)
    assert first not in sandbox._all


def test_a_decoder_that_crashes_or_hangs_never_takes_wick_with_it(tmp_path):
    w = sandbox._borrow()
    w.proc.kill()                        # (a decoder a crafted file crashed)
    w.proc.join()
    sandbox._give_back(w)
    found = sandbox.broken_check(tmp_path / "x.jpg", 10, False)
    assert found is not None and found[0] == "damaged" and "decoder stop" in found[1]
    assert w not in sandbox._all

    with pytest.raises(sandbox.DecoderCrashed):
        sandbox.call("_test_sleep", 30, timeout=1)            # (one that hangs)
    assert not sandbox._all                                   # (ended, not kept)
    assert sandbox.similar_fingerprint(tmp_path / "missing.jpg") is None   # the next call gets a new worker


def test_broken_and_similar_checks_run_in_a_worker(tmp_path):
    (tmp_path / "bad.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    assert sandbox.broken_check(tmp_path / "bad.jpg", 104, False)[0] == "unreadable"
    pic = Image.radial_gradient("L").resize((300, 200)).convert("RGB")
    pic.save(tmp_path / "g.png")
    fp = sandbox.similar_fingerprint(tmp_path / "g.png")
    assert fp is not None and fp[1:] == (300, 200)
    sharp, gray = sandbox.similar_inspect(tmp_path / "g.png", 64)
    assert sharp > 0 and gray.size == (64, 64) and gray.mode == "L"


def test_the_worker_cannot_change_your_files(tmp_path):
    """Windows: the worker runs at low integrity, so it can read your folders but not write to them."""
    if os.name != "nt":
        pytest.skip("integrity levels are Windows'")
    header, _ = sandbox.call("_test_write", str(tmp_path / "written.txt"), timeout=60)
    assert header["wrote"] is False and not (tmp_path / "written.txt").exists()
    header, _ = sandbox.call("_test_write", str(Path(sandbox.tmp_dir()) / "scratch.txt"), timeout=60)
    assert header["wrote"] is True                             # (its own temporary folder is writable)


@pytest.mark.skipif(not have("cjxl", "djxl"), reason="libjxl tools not installed")
def test_jpeg_xl_is_decoded_in_the_workers_own_temporary_folder(tmp_path):
    from wick import tools

    Image.new("RGB", (64, 48), (90, 60, 30)).save(tmp_path / "s.png")
    tools.run([tools.require("cjxl"), "s.png", "s.jxl", "-d", "0"], cwd=tmp_path)
    before = set(os.listdir(tmp_path))
    got = sandbox.rgba("display_image", str(tmp_path / "s.jxl"), 10**6, max_pixels=10**6, timeout=120)
    assert got is not None and got[:2] == (64, 48)
    assert set(os.listdir(tmp_path)) == before                 # nothing written next to the picture


def test_without_workers_the_same_work_happens_in_wick(tmp_path, monkeypatch):
    monkeypatch.setenv("WICK_NO_SANDBOX", "1")
    Image.new("RGB", (50, 50), (1, 2, 3)).save(tmp_path / "c.png")
    got = sandbox.rgba("thumbnail", str(tmp_path / "c.png"), "png", 32, max_pixels=32 * 32, timeout=60)
    assert got is not None and got[:2] == (32, 32) and not sandbox._all
