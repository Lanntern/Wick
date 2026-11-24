"""The Metadata page: results streamed in batches, stopping."""

from pathlib import Path

from .conftest import _wait


def test_metadata_streams_bounded_batches_without_recounting_the_table(win, monkeypatch):
    import threading

    from PySide6.QtCore import Qt, QTimer

    from wick import metaclean
    from wick.gui import metadata_scan
    from wick.scanner import FileEntry, ScanResult

    page = win.metadata
    win.go_to(page)
    page.clear()
    old_filter = page.show_filter.currentIndex()
    page.show_filter.setCurrentIndex(0)
    release = threading.Event()
    beats = []
    timer = QTimer(page, interval=5)
    timer.timeout.connect(lambda: beats.append(True))
    selected_calls = []
    original_selected = page.selected

    def selected():
        selected_calls.append(True)
        return original_selected()

    def fake_scan(paths, settings, progress, cancel, **kwargs):
        assert kwargs["probe"] is False
        result = ScanResult(list(paths))
        for n in range(4096):
            if n == 128:
                while not release.wait(0.01) and not cancel.is_set():
                    pass
            if cancel.is_set():
                result.cancelled = True
                break
            entry = FileEntry(Path(paths[0]) / f"{n}.png", 100, "png", 0)
            result.entries.append(entry)
            kwargs["found"](entry)
        return result

    monkeypatch.setattr(metadata_scan, "scan", fake_scan)
    monkeypatch.setattr(metaclean, "summarize", lambda _: metaclean.MetaSummary(camera="Test camera"))
    monkeypatch.setattr(page, "selected", selected)
    timer.start()
    try:
        page.add_paths(["synthetic-metadata-folder"])
        assert _wait(lambda: len(page.entries) >= 32, 10)
        assert page.busy() and page.scan_thread.isRunning()
        assert page.scan_thread.batches.maxsize == 4
        assert not page.run_btn.isEnabled()
        assert page.paths
        release.set()
        assert _wait(lambda: not page.busy(), 30)
        assert len(page.entries) == page.tree.topLevelItemCount() == 4096
        assert page.selection_count() == page._cameras == 4096
        assert len(selected_calls) < 10  # table size no longer multiplies work for every arriving row
        assert len(beats) > 10
        assert page.run_btn.isEnabled()
        page.tree.header()._check[2]()  # header select-none uses blocked itemChanged signals
        assert page.selection_count() == 0
        page.tree.header()._check[2]()
        assert page.selection_count() == 4096
        page.show_filter.setCurrentIndex(page.show_filter.findData("gps"))
        assert page.selection_count() == 0
        assert page._header_state() == Qt.Unchecked
    finally:
        release.set()
        timer.stop()
        page.stop()
        assert _wait(lambda: not page.busy(), 10)
        page.clear()
        page.show_filter.setCurrentIndex(old_filter)


def test_metadata_stop_releases_a_producer_waiting_for_ui_capacity(win, monkeypatch):

    from wick import metaclean
    from wick.gui import metadata_scan
    from wick.scanner import FileEntry, ScanResult
    from wick.settings import Settings

    def fake_scan(paths, settings, progress, cancel, **kwargs):
        for n in range(10000):
            if cancel.is_set():
                break
            kwargs["found"](FileEntry(Path(f"{n}.png"), 1, "png", 0))
        return ScanResult([])

    monkeypatch.setattr(metadata_scan, "scan", fake_scan)
    monkeypatch.setattr(metaclean, "summarize", lambda _: metaclean.MetaSummary())
    worker = metadata_scan.MetadataScan([], Settings())
    worker.start()
    try:
        assert _wait(lambda: worker.batches.full(), 5)
        assert worker.isRunning()
    finally:
        worker.cancel.set()
        assert worker.wait(2000)


def test_metadata_stop_cancels_an_active_probe(win, monkeypatch, tmp_path):
    import sys

    from wick import metaclean, tools
    from wick.gui import metadata_scan
    from wick.scanner import FileEntry, ScanResult
    from wick.settings import Settings

    ready = tmp_path / "probe-started"
    script = "from pathlib import Path;import sys,time;Path(sys.argv[1]).touch();time.sleep(60)"

    def summarize(_path):
        tools.run([sys.executable, "-c", script, str(ready)], timeout=90)
        return metaclean.MetaSummary()

    def scan(paths, settings, progress, cancel, **kwargs):
        kwargs["found"](FileEntry(Path("probe.mp4"), 1, "video", 0))
        return ScanResult([])

    monkeypatch.setattr(metadata_scan, "scan", scan)
    monkeypatch.setattr(metaclean, "summarize", summarize)
    worker = metadata_scan.MetadataScan([], Settings())
    worker.start()
    try:
        assert _wait(ready.exists, 10)
    finally:
        worker.cancel.set()
        assert worker.wait(3000)
    assert not worker.error
