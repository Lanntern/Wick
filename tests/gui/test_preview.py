"""The preview window: quality choices, Save As, compressing from it, closing while it works."""

import time

import pytest
from PIL import Image

from .conftest import _wait


def test_image_preview_dialog(win, tmp_path):
    from wick.gui.preview import PreviewDialog
    from wick.scanner import scan
    from wick.settings import Settings

    Image.effect_noise((320, 240), 30).convert("RGB").save(tmp_path / "p.png")
    s = Settings(image_format="webp", png_mode="lossy", image_quality=80, min_image_kb=0)
    entry = scan([tmp_path], s).entries[0]
    dlg = PreviewDialog(entry, s, win)
    assert _wait(lambda: dlg.worker is not None and dlg.worker.isFinished(), 120)
    _wait(lambda: "smaller" in dlg.info.text() or "larger" in dlg.info.text(), 5)
    assert "smaller" in dlg.info.text() or "larger" in dlg.info.text(), dlg.info.text()
    assert dlg.view.before is not None and dlg.view.after is not None
    # Split and Side by Side are both always one click away; the line's slider only shows for Split.
    assert dlg.mode.tabText(dlg.mode.currentIndex()) == "Split" and not dlg.split.isHidden()
    dlg.mode.setCurrentIndex(1)
    assert dlg.view.side_by_side and dlg.split.isHidden()
    from PySide6.QtCore import QPointF

    dlg.view._zoom_at(QPointF(10, 10), 2.0)          # zoom works side by side too
    assert not dlg.view._fit
    dlg.mode.setCurrentIndex(0)
    assert not dlg.view.side_by_side and not dlg.split.isHidden() and dlg.split.isEnabled()
    assert dlg.save_as.isEnabled() and dlg.compress.isEnabled()
    dlg.reject()


def test_preview_quality_can_always_be_chosen(win, tmp_path):
    """Smart quality used to lock the preview's quality. Now: its targets, or a Custom value."""
    from wick.gui.preview import PreviewDialog
    from wick.scanner import scan
    from wick.settings import Settings

    Image.effect_noise((200, 150), 30).convert("RGB").save(tmp_path / "p.png")
    s = Settings(image_format="webp", png_mode="lossy", smart_quality=True, smart_target="high", min_image_kb=0)
    entry = scan([tmp_path], s).entries[0]
    dlg = PreviewDialog(entry, s, win)
    assert _wait(lambda: dlg.worker is not None and dlg.worker.isFinished(), 120)
    assert dlg.quality.isEnabled() and dlg.quality.currentText() == "High" and dlg.value.isHidden()
    assert dlg.mode.minimumSizeHint().width() >= dlg.mode.sizeHint().width() - 2   # never squeezed into arrows
    dlg.quality.setCurrentIndex(dlg.quality.findData("custom"))
    dlg._quality_picked(0)
    assert not dlg.value.isHidden()
    _wait(lambda: dlg.worker.isFinished(), 120)
    dlg.value.setValue(42)
    chosen = dlg._chosen_settings()
    assert not chosen.smart_quality and chosen.image_quality == 42
    win.compress.panel.load(s)
    dlg.quality_chosen.connect(lambda st: win.compress._use_quality(st, video=False))
    dlg._use()                                       # "Use for All Files" reaches the options
    applied = win.compress.panel.settings()
    assert not applied.smart_quality and applied.image_quality == 42


def test_preview_says_lossless_instead_of_a_quality(win, tmp_path):
    from wick.gui.preview import PreviewDialog
    from wick.scanner import scan
    from wick.settings import Settings

    Image.effect_noise((200, 150), 30).convert("RGB").save(tmp_path / "p.jpg", quality=95)
    s = Settings(image_format="jxl", jpeg_mode="lossless", min_image_kb=0)
    entry = scan([tmp_path], s).entries[0]
    dlg = PreviewDialog(entry, s, win)
    assert not dlg.lossless_note.isHidden() and dlg.quality.isHidden() and dlg.use.isHidden()
    assert _wait(lambda: dlg.worker is not None and dlg.worker.isFinished(), 120)
    dlg.reject()


def test_preview_save_as_keeps_the_original(win, tmp_path, no_dialogs, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from wick.gui.preview import PreviewDialog
    from wick.settings import Settings

    Image.effect_noise((320, 240), 30).convert("RGB").save(tmp_path / "p.png")
    s = Settings(image_format="webp", png_mode="lossy", image_quality=80, min_image_kb=0, output_mode="replace",
                 require_savings=False, video_codec="none")
    win.compress.panel.load(s)
    win.compress.clear()
    win.compress.add_paths([str(tmp_path / "p.png")])
    assert _wait(lambda: not win.compress.busy() and win.compress.model.rows, 60)
    entry = win.compress.model.rows[0].entry
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(tmp_path / "out" / "x"), "")))
    dlg = PreviewDialog(entry, win.compress.panel.settings(), win)
    dlg.save_as_requested.connect(lambda st, d: win.compress.compress_one(entry, st, d))
    assert _wait(lambda: dlg.save_as.isEnabled(), 120)
    dlg.save_as.click()
    assert _wait(lambda: not win.compress.busy(), 120)
    assert (tmp_path / "out" / "x.webp").exists()
    assert (tmp_path / "p.png").exists()            # Save As never replaces the original, even in Replace mode
    _wait(lambda: win.compress.job_status.title.text().startswith("Done"), 10)   # (the result arrives as an event)
    assert win.compress.job_status.title.text() == "Done: x.webp"


@pytest.mark.parametrize("extension", ["gif", "mp4"])
@pytest.mark.parametrize("action", ["close", "reject"])
def test_close_busy_preview_never_waits_on_ui_thread(win, tmp_path, monkeypatch, extension, action):
    import threading

    from wick.formats import ImageInfo, VIDEO
    from wick.gui.preview import PreviewDialog, PreviewThread
    from wick.probe import VideoInfo
    from wick.scanner import FileEntry
    from wick.settings import Settings

    path = tmp_path / f"preview.{extension}"
    if extension == "gif":
        Image.new("RGB", (32, 24), "red").save(path)
    else:
        path.write_bytes(b"test video header")
    entry = FileEntry(path, path.stat().st_size, VIDEO if extension == "mp4" else "gif", 0,
                      image=ImageInfo(32, 24),
                      video=VideoInfo("h264", 32, 24, "yuv420p", 24, 2, 1000, 0), picked=True)
    release, finished = threading.Event(), threading.Event()
    monkeypatch.setattr(PreviewThread, "run", lambda self: release.wait(2))
    dialog = PreviewDialog(entry, Settings(video_encoder="cpu"), win)
    dialog.show()
    worker = dialog.worker
    worker.finished.connect(finished.set)
    try:
        assert _wait(worker.isRunning, 1)
        started = time.monotonic()
        getattr(dialog, action)()
        assert time.monotonic() - started < 0.3
        assert not dialog.isVisible() and worker.cancel.is_set()
    finally:
        release.set()
        assert _wait(finished.is_set, 3)
        dialog.deleteLater()
