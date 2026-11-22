"""Scheduling and job-page helpers: biggest files first, videos alongside photos on a graphics card, fewer jobs at
once on slow drives and while a game is in front."""

import threading
import time

from wick import drives, engine
from wick.settings import Settings


def test_files_start_biggest_first(tmp_path, monkeypatch):
    from wick.formats import JPEG
    from wick.plan import Plan
    from wick.scanner import FileEntry

    monkeypatch.setattr(engine, "default_workers", lambda s: 1)
    started = []

    def processor(entry, plan, s, writer, cancel, progress):
        started.append(entry.size)
        return engine.FileResult(entry, engine.SKIPPED, "test")

    items = []
    for i, size in enumerate((10, 300, 20, 5000, 7)):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(b"x")
        items.append((FileEntry(p, size, JPEG, 0), Plan(True, "jpeg_opt", "", ".jpg")))
    engine.run_job(items, Settings(), tmp_path, processor=processor)
    assert started == sorted(started, reverse=True)


def test_slow_drives_and_graphics_cards_change_the_schedule(tmp_path, monkeypatch):
    import wick.video as video

    monkeypatch.setattr(Settings, "image_workers", lambda self: self.workers or 8)
    monkeypatch.setattr(video, "parallel_videos", lambda s: 1)
    monkeypatch.setattr(drives, "slow", lambda paths: "")
    monkeypatch.setattr(video, "choose_encoder", lambda codec, pref: "libsvtav1")
    plan = engine.schedule(Settings(), [tmp_path], True, True)
    assert (plan.image_workers, plan.overlap) == (8, False)
    monkeypatch.setattr(video, "choose_encoder", lambda codec, pref: "av1_nvenc")
    plan = engine.schedule(Settings(), [tmp_path], True, True)
    assert (plan.image_workers, plan.overlap) == (7, True)
    assert not engine.schedule(Settings(), [tmp_path], True, False).overlap
    monkeypatch.setattr(drives, "slow", lambda paths: "network")
    plan = engine.schedule(Settings(), [tmp_path], True, False)
    assert plan.image_workers == drives.SLOW_PARALLEL and "network share" in plan.notes[0]
    assert engine.schedule(Settings(workers=6), [tmp_path], True, False).image_workers == 6   # (set by hand)


def test_a_local_folder_isnt_slow_or_a_network_share(tmp_path):
    assert not drives.network(tmp_path)
    assert drives.slow([tmp_path / "missing", tmp_path]) in ("", "hdd")


def test_the_gate_halves_while_something_is_full_screen():
    busy = [False]
    gate = engine.Gate(4, busy=lambda: busy[0])
    gate.CHECK_EVERY = 0.0
    assert gate.allowed() == 4
    busy[0] = True
    assert gate.allowed() == 2
    peak = [0]
    lock = threading.Lock()

    def work():
        with gate:
            with lock:
                peak[0] = max(peak[0], gate.active)
            time.sleep(0.05)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 2 and gate.active == 0
    assert engine.Gate(1, busy=lambda: True).allowed() == 1


def test_photos_and_videos_overlap_when_the_card_encodes(tmp_path, monkeypatch):
    from wick.formats import JPEG, VIDEO
    from wick.plan import Plan
    from wick.scanner import FileEntry

    monkeypatch.setattr(engine, "schedule", lambda s, paths, i, v: engine.Schedule(1, 1, True))
    running, overlapped = set(), []

    def processor(entry, plan, s, writer, cancel, progress):
        running.add(entry.kind)
        time.sleep(0.2)
        if len(running) > 1:
            overlapped.append(True)
        running.discard(entry.kind)
        return engine.FileResult(entry, engine.SKIPPED, "test")

    items = []
    for i, kind in enumerate((JPEG, VIDEO)):
        p = tmp_path / f"{i}.bin"
        p.write_bytes(b"x")
        items.append((FileEntry(p, 1, kind, 0), Plan(True, "x", "", ".x")))
    results, _ = engine.run_job(items, Settings(), tmp_path, processor=processor)
    assert len(results) == 2 and overlapped


def test_a_file_that_wouldnt_shrink_is_skipped_next_time(tmp_path, monkeypatch):
    from wick import index
    from wick.formats import JPEG
    from wick.plan import Plan
    from wick.scanner import FileEntry

    monkeypatch.setattr(index, "path", lambda: tmp_path / "index.sqlite")
    monkeypatch.setattr(index, "_conn", None, raising=False)
    index.set_enabled(True)
    p = tmp_path / "a.jpg"
    p.write_bytes(b"x" * 100)
    st = p.stat()
    calls = []

    def fake(entry, plan, s, writer, cancel, progress):
        calls.append(entry.path)
        return engine.FileResult(entry, engine.SKIPPED, "Result would be larger; original kept", not_smaller=True)

    monkeypatch.setattr(engine, "process_file", fake)
    item = (FileEntry(p, st.st_size, JPEG, st.st_mtime), Plan(True, "jpeg_opt", "", ".jpg"))
    engine.run_job([item], Settings(), tmp_path)
    results, _ = engine.run_job([item], Settings(), tmp_path)
    assert len(calls) == 1 and "Tried before" in results[0].message
    engine.run_job([item], Settings(workers=3), tmp_path)                 # (speed settings: still known)
    assert len(calls) == 1
    engine.run_job([item], Settings(image_effort=9), tmp_path)            # other settings: tried again
    assert len(calls) == 2
    picked = (FileEntry(p, st.st_size, JPEG, st.st_mtime, picked=True), item[1])
    engine.run_job([picked], Settings(), tmp_path)                        # chosen by itself: tried again
    assert len(calls) == 3


def test_the_scan_lists_files_before_reading_them(tmp_path):
    from PIL import Image

    from wick.scanner import scan

    for i in range(5):
        Image.new("RGB", (64 + i, 64), (i, 0, 0)).save(tmp_path / f"p{i}.png")
    events = []
    result = scan([tmp_path], Settings(min_image_kb=0), listed=lambda es: events.append(("listed", len(es),
                                                                                       [e.info for e in es])),
                  ready=lambda es: events.append(("ready", len(es), None)))
    assert events[0][0] == "listed" and events[0][1] == 5
    assert sum(n for kind, n, _ in events if kind == "ready") == 5
    assert all(e.image is not None for e in result.entries)


def test_the_list_fills_in_as_details_arrive(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from wick.formats import ImageInfo, PNG
    from wick.gui.file_model import FileFilterProxy, FileTableModel
    from wick.scanner import FileEntry

    entries = [FileEntry(tmp_path / f"{i}.png", 100_000 * (i + 1), PNG, 0) for i in range(3)]
    model = FileTableModel()
    proxy = FileFilterProxy()
    proxy.setSourceModel(model)
    model.show_listed(entries)
    assert model.pending_count() == 3 and proxy.rowCount() == 3          # (shown, though nothing planned yet)
    assert model.totals()[0] == 0
    s = Settings(min_image_kb=0)
    entries[0].image = ImageInfo(640, 480, lossless_source=True)
    model.details_read([entries[0]], s)
    assert model.pending_count() == 2 and model.rows[0].plan.convert
    model.rows[0].checked = False                                         # (changed while scanning)
    for e in entries[1:]:
        e.image = ImageInfo(640, 480, lossless_source=True)
    model.finish_listed(entries[::-1], s)
    assert model.pending_count() == 0 and [r.entry for r in model.rows] == entries[::-1]
    assert model.rows[2].checked is False and all(r.checked for r in model.rows[:2])


def test_time_left_waits_then_estimates_smoothly():
    from wick.eta import TimeLeft, say

    t = TimeLeft()
    assert t.update(10, 1000, 2) is None                   # too early
    assert t.update(5, 1000, 20) is None                   # too little done
    first = t.update(100, 1000, 10)
    assert first == 90
    jumped = t.update(150, 1000, 11)                       # a big file finished: no wild jump
    raw = 11 * 850 / 150
    assert raw * 0.67 <= jumped <= first
    assert t.update(1000, 1000, 50) == 0
    assert say(None) == "" and say(30) == "less than a minute left"
    assert say(4 * 60 + 10) == "about 4 min left" and say(80 * 60) == "about 1 h 20 min left"
    assert say(2 * 3600 + 60) == "about 2 h left"


def test_failures_come_with_the_fix():
    from wick import fixes
    from wick.cfa import SHORT
    from wick.tools import ToolMissing

    msgs = ["[WinError 32] The process cannot access the file because it is being used by another process",
            "[Errno 28] No space left on device; original kept", f"{SHORT} (allow Wick in Windows Security…)",
            str(ToolMissing("cjxl")), "[Errno 28] No space left on device; original kept", "Something odd"]
    got = fixes.suggest(msgs)
    assert [g[0] for g in got] == ["space", "protected", "in_use", "tool"] and got[0][3] == 2
    assert fixes.suggest(["Unexpected"]) == []






def test_summaries_say_what_happens_where_space_comes_from_and_why_files_were_left(tmp_path):
    from wick import summary
    from wick.formats import JPEG, VIDEO
    from wick.plan import Plan
    from wick.scanner import FileEntry

    line = summary.describe_settings(Settings())
    assert line.startswith("Pictures to JPEG XL (identical pixels)") and "Videos to AV1 or HEVC" in line
    assert line.endswith("Originals to the Recycle Bin")
    only = summary.describe_settings(Settings(output_mode="suffix"), {"video"})
    assert "Pictures" not in only and only.endswith("next to the originals")
    assert "left alone" in summary.describe_settings(Settings(image_format="none"), {"image"})

    items = [(FileEntry(tmp_path / "v.mkv", 1_000_000_000, VIDEO, 0), Plan(True, est_size=400_000_000)),
             (FileEntry(tmp_path / "a.jpg", 5_000_000, JPEG, 0), Plan(True, est_size=4_000_000)),
             (FileEntry(tmp_path / "b.jpg", 5_000_000, JPEG, 0), Plan(True, est_size=4_000_000)),
             (FileEntry(tmp_path / "c.pdf", 1_000, "document", 0), Plan(True, est_size=1_000))]
    parts = summary.savings_breakdown(items)
    assert [p[0] for p in parts] == ["video", "image"] and parts[1][1] == 2
    assert summary.say_breakdown(parts)[1].endswith("from 2 pictures")

    groups = summary.group_skips(["Would only save 1.2% (40 KB); needs 5%; original kept",
                                  "Result would be larger; original kept",
                                  "Tried before with these settings: it wouldn't get smaller",
                                  "HDR photo: kept as it is", "File no longer exists"])
    assert groups[0] == ("already as small as they'll get", 3)
    assert ("HDR photo: kept as it is", 1) in groups and ("changed or moved since the scan", 1) in groups


def test_helper_processes_end_with_the_app():
    """The pool's workers hold both ends of their job queue: they only stop when told to, so shutdown ends them
    (the app exits with os._exit, which skips the pool's own atexit)."""
    import os

    from wick import _workers, procpool

    if os.environ.get("WICK_NO_PROCESSES"):
        return
    procpool.call(_workers.set_low_priority, False)
    pool = procpool._pool
    if pool is None:                                       # (helper processes unavailable here)
        return
    workers = list(pool._processes.values())
    assert workers
    procpool.shutdown()
    for p in workers:
        p.join(10)
        assert not p.is_alive()
