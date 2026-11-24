"""The Compress page: jobs from start to end, the file list and its columns, options, progress, pausing, resuming and
results."""

import time
from pathlib import Path

from PIL import Image

from .conftest import _wait


def test_compress_job_end_to_end(win, tmp_path, no_dialogs):
    from wick.settings import Settings

    folder = tmp_path / "Bilder åäö"
    folder.mkdir()
    img = Image.effect_noise((480, 360), 40).convert("RGB")
    img.save(folder / "photo.jpg", quality=95)
    Image.linear_gradient("L").resize((400, 400)).convert("RGB").save(folder / "shot.png")

    s = Settings(image_format="keep", jpeg_mode="lossless", png_mode="lossless", output_mode="suffix",
                 min_image_kb=0, require_savings=False, video_codec="none")
    win.compress.panel.load(s)
    win.compress.clear()
    win.compress.add_paths([str(folder)])
    assert _wait(lambda: not win.compress.busy() and win.compress.model.rows, 60)
    assert len(win.compress.model.rows) == 2

    win.compress.start_job(confirm=False)
    assert _wait(lambda: not win.compress.busy() and all(r.result for r in win.compress.model.rows), 120)
    _wait(lambda: win.compress.job_status.title.text().startswith("Done"), 10)   # (the result arrives as an event)
    outputs = sorted(p.name for p in folder.iterdir())
    assert any("_compressed" in n for n in outputs), outputs
    assert (folder / "photo.jpg").exists()  # suffix mode keeps the originals
    status = win.compress.job_status
    assert not status.isHidden() and status.title.text().startswith("Done: 2 of 2 files compressed")
    assert "Saved in" in status.detail.text() and "kept" in status.detail.text()
    # Clear goes back to the start: empty list, the drop zone, and the outcome card gone.
    assert win.compress.clear_btn.isEnabled()
    win.compress.clear_btn.click()
    assert not win.compress.model.rows and status.isHidden() and win.compress.stack.currentIndex() == 0
    assert not win.compress.clear_btn.isEnabled()


def test_one_file_next_to_original_says_what_happened(win, tmp_path, no_dialogs):
    """"Next to originals" used to make the Compress button do nothing (an error inside its handler)."""
    from wick.settings import Settings

    Image.effect_noise((320, 240), 40).convert("RGB").save(tmp_path / "one.png")
    win.compress.panel.load(Settings(image_format="webp", png_mode="lossy", output_mode="beside", min_image_kb=0,
                                     require_savings=False, video_codec="none"))
    win.compress.clear()
    win.compress.add_paths([str(tmp_path / "one.png")])
    assert _wait(lambda: not win.compress.busy() and win.compress.model.rows, 60)
    steps = []
    win.compress.compress_btn.click()
    assert win.compress.job_thread is not None and win.compress.busy()
    win.compress.job_thread.file_step.connect(lambda _e, m: steps.append(m))
    assert _wait(lambda: not win.compress.busy(), 120)
    _wait(lambda: win.compress.job_status.show_btn.isHidden() is False, 5)
    status = win.compress.job_status
    assert status.title.text() == "Done: one.webp", status.title.text()
    assert f"Saved in {tmp_path}" in status.detail.text() or "Saved in ~" in status.detail.text()
    assert not status.show_btn.isHidden() and (tmp_path / "one.webp").exists() and (tmp_path / "one.png").exists()


def test_a_failure_says_at_which_step(win, tmp_path, no_dialogs, monkeypatch):
    from wick import engine, steps
    from wick.settings import Settings

    def broken(*_a, **_k):
        steps.report("Encoding WebP")
        raise RuntimeError("encoder crashed")
    monkeypatch.setattr(engine, "convert_image", broken)
    Image.effect_noise((64, 48), 40).convert("RGB").save(tmp_path / "bad.png")
    win.compress.panel.load(Settings(image_format="webp", png_mode="lossy", output_mode="suffix", min_image_kb=0,
                                     require_savings=False, video_codec="none"))
    win.compress.clear()
    win.compress.add_paths([str(tmp_path / "bad.png")])
    assert _wait(lambda: not win.compress.busy() and win.compress.model.rows, 60)
    assert win.compress.start_job(confirm=False)
    assert _wait(lambda: not win.compress.busy(), 60)
    _wait(lambda: win.compress.job_status.copy_btn.isHidden() is False, 5)
    status = win.compress.job_status
    assert status.title.text() == "Couldn't finish bad.png"
    assert "While encoding WebP: encoder crashed" in status.detail.text()
    assert not status.copy_btn.isHidden() and not status.log_btn.isHidden()
    assert "step: Encoding WebP" in status._details
    row = win.compress.model.rows[0]
    assert "(while encoding WebP)" in win.compress.model._status_text(row)


def test_presets_apply_and_reach_the_panel(win):
    panel = win.compress.panel
    assert panel.apply_preset("For sharing")
    s = panel.settings()
    assert (s.image_format, s.resize_long_edge, s.metadata, s.video_max_height) == ("webp", 2560, "strip_private",
                                                                                    1080)
    assert panel.preset.currentData() == "For sharing"
    assert not panel.apply_preset("No such preset")

    # Explorer's "Compress with <preset>" goes through receive_paths.
    win.receive_paths([], show=False, preset="Photo archive")
    assert panel.settings().image_format == "jxl" and panel.settings().jpeg_mode == "lossless"


def test_preview_offers_compress_only_when_the_result_would_be_kept(win, tmp_path, no_dialogs, monkeypatch):
    """A result that wouldn't be kept (larger, or below the minimum saving) can't be "compressed" (it would do
    nothing), but Save As still saves it: the person chose that file."""
    from PySide6.QtWidgets import QFileDialog

    from wick.gui.preview import PreviewDialog
    from wick.settings import Settings

    Image.effect_noise((320, 240), 30).convert("RGB").save(tmp_path / "p.png")
    s = Settings(image_format="webp", png_mode="lossy", image_quality=80, min_image_kb=0, output_mode="beside",
                 require_savings=True, min_savings_pct=99.5, video_codec="none")
    win.compress.panel.load(s)
    win.compress.clear()
    win.compress.add_paths([str(tmp_path / "p.png")])
    assert _wait(lambda: not win.compress.busy() and win.compress.model.rows, 60)
    entry = win.compress.model.rows[0].entry
    dlg = PreviewDialog(entry, win.compress.panel.settings(), win)
    assert _wait(lambda: dlg.save_as.isEnabled(), 120)
    assert not dlg.compress.isEnabled() and "minimum" in dlg.compress.toolTip()
    dlg._describe(entry.size * 2, (320, 240), "WebP")
    assert not dlg.compress.isEnabled() and "larger" in dlg.compress.toolTip()
    dlg._describe(entry.size // 1000, (320, 240), "WebP")
    assert dlg.compress.isEnabled()
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(tmp_path / "out" / "x"), "")))
    dlg.save_as_requested.connect(lambda st, d: win.compress.compress_one(entry, st, d))
    dlg.save_as.click()
    assert _wait(lambda: not win.compress.busy(), 120)
    assert (tmp_path / "out" / "x.webp").exists() and (tmp_path / "p.png").exists()


def test_options_panel_fits_its_column(win):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QLabel, QScrollArea, QSpinBox

    win.resize(1440, 900)
    win.go_to(win.compress)
    win.compress.inspector.setVisible(True)
    panel = win.compress.panel
    panel.apply_preset("For sharing")  # long preset name and description
    for sec in panel.sections:
        sec.toggle.setChecked(True)
    for _ in range(5):
        QApplication.processEvents()
    scroll = panel.parentWidget()
    while scroll is not None and not isinstance(scroll, QScrollArea):
        scroll = scroll.parentWidget()
    viewport = scroll.viewport() if scroll is not None else win.compress.inspector
    limit = viewport.width()
    widgets = [w for cls in (QAbstractButton, QComboBox, QSpinBox, QLabel) for w in panel.findChildren(cls)]
    assert widgets
    for w in widgets:
        if w.isVisible():
            right = w.mapTo(viewport, QPoint(w.width(), 0)).x()
            assert right <= limit, f"{type(w).__name__} {w.accessibleName() or getattr(w, 'text', lambda: '')()!r}"


def test_lists_sort_select_all_and_menu(win, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeWidget

    from wick.gui.filelist import SortItem, add_header_checkbox, file_menu, make_sortable, set_sort_keys

    tree = QTreeWidget()
    tree.setHeaderLabels(["Name", "Size"])
    header = make_sortable(tree)
    for name, size in (("b.png", 2048), ("a.png", 10), ("c.png", 1_000_000)):
        it = SortItem([name, f"{size} B"])
        set_sort_keys(it, {1: size})
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(0, Qt.Unchecked)
        tree.addTopLevelItem(it)
    tree.sortByColumn(1, Qt.DescendingOrder)   # by size, not by the text "1000000 B" < "2048 B"
    assert [tree.topLevelItem(i).text(0) for i in range(3)] == ["c.png", "b.png", "a.png"]
    tree.sortByColumn(0, Qt.AscendingOrder)
    assert tree.topLevelItem(0).text(0) == "a.png"

    changed = []
    add_header_checkbox(tree, 0, lambda: changed.append(1))
    column, state, toggle = header._check
    assert state() == Qt.Unchecked
    toggle()
    assert state() == Qt.Checked and changed
    tree.topLevelItem(0).setCheckState(0, Qt.Unchecked)
    assert state() == Qt.PartiallyChecked
    toggle()
    assert state() == Qt.Checked

    f = tmp_path / "x.txt"
    f.write_text("x")
    labels = [a.text() for a in file_menu(win, [f], [("Extra", lambda: None)]).actions() if a.text()]
    assert labels[:3] == ["Open", labels[1], "Copy Path"] and "Extra" in labels

    # Compress table: the header checkbox drives the model.
    h = win.compress.table.horizontalHeader()
    assert h._check is not None


def test_saved_so_far_hidden_until_something_saved(win):
    sb = win.sidebar
    sb.set_saved(None)
    assert not sb.foot_value.isVisibleTo(sb)
    sb.set_saved("1.2 GB")
    assert sb.foot_value.isVisibleTo(sb) or sb.compact
    win.history.refresh()


def test_window_resize_does_not_freeze_columns(win):
    """Resizing the window with the mouse held resizes the last (stretching) column: that isn't the person
    choosing widths, so columns keep fitting their content."""
    from PySide6.QtWidgets import QTreeWidget

    from wick.gui.filelist import make_sortable

    tree = QTreeWidget()
    tree.setHeaderLabels(["Name", "Folder", "Size"])
    make_sortable(tree)
    tree._fitter._resized(2)                         # (last column, as the window grows)
    tree.header()._dragging = True
    tree._fitter._resized(2)
    assert not tree._fitter.user_sized
    tree._fitter._resized(0)                         # a real drag on a column edge
    assert tree._fitter.user_sized


def test_columns_fit_long_names_when_there_is_room(win):
    from PySide6.QtWidgets import QApplication, QTreeWidget

    from wick.gui.filelist import SortItem, filling, make_sortable

    tree = QTreeWidget()
    tree.setHeaderLabels(["Name", "Folder", "Size"])
    make_sortable(tree)
    tree.resize(1400, 400)
    tree.show()
    long_name = "A very long file name that would normally be cut off in the first column.jpeg"
    with filling(tree):
        tree.addTopLevelItem(SortItem([long_name, "Pictures", "1 MB"]))
    _wait(lambda: False, 0.2)
    QApplication.processEvents()
    assert tree.columnWidth(0) >= tree.fontMetrics().horizontalAdvance(long_name)
    tree.resize(500, 400)
    _wait(lambda: False, 0.2)
    assert tree.columnWidth(0) + tree.columnWidth(1) <= 500
    tree.close()


def test_options_only_for_the_kinds_of_files_listed(win):
    panel = win.compress.panel
    sec = {s.toggle.text(): s for s in panel.sections}
    panel.set_file_kinds({"video"})
    assert not sec["Images"].isVisibleTo(panel) and not sec["Audio"].isVisibleTo(panel)
    assert sec["Videos"].isVisibleTo(panel)
    panel.set_file_kinds(None)
    assert sec["Images"].isVisibleTo(panel) and sec["Audio"].isVisibleTo(panel)

    # A kind that's switched off keeps only its on/off control.
    panel.video_codec.setCurrentIndex(panel.video_codec.findData("none"))
    sec["Videos"].toggle.setChecked(True)
    assert not panel.video_quality.isVisibleTo(panel) and panel.video_codec.isVisibleTo(panel)
    panel.video_codec.setCurrentIndex(panel.video_codec.findData("av1"))
    assert panel.video_quality.isVisibleTo(panel)


def test_after_column_says_size_and_change():
    from wick.gui.file_model import _pct

    assert _pct(231, 149) == "−35%" and _pct(100, 110) == "+10%"


def test_columns_keep_a_minimum_width():
    """Dragging a column narrow stops where its heading still fits, and short values (sizes) stay whole."""
    from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

    from wick.gui.filelist import make_sortable

    QApplication.instance() or QApplication([])
    tree = QTreeWidget()
    tree.setHeaderLabels(["Name", "Size", "Details"])
    for i in range(20):
        QTreeWidgetItem(tree, [f"A rather long file name number {i}.jpg", "120.0 MB", "Lossless, reversible"])
    tree.resize(900, 400)
    tree.show()
    header = make_sortable(tree)
    QApplication.processEvents()
    fm = tree.fontMetrics()
    header.resizeSection(1, 5)                      # what a drag to the left edge asks for
    assert header.sectionSize(1) >= fm.horizontalAdvance("120.0 MB")
    header.resizeSection(0, 5)
    assert header.sectionSize(0) >= max(header.MIN_TEXT, fm.horizontalAdvance("Name"))
    assert header.sectionSize(0) < fm.horizontalAdvance("A rather long file name number 10.jpg")  # long text may elide
    tree.close()


def test_dragging_a_column_edge_moves_that_edge(win, tmp_path):
    """The Compress list: the dragged edge follows the pointer, the columns always fill the list exactly,
    hidden columns leave no gap, and no drag pushes the last column out of sight."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from wick.gui.file_model import C_NAME, C_STATUS

    for i in range(3):
        Image.effect_noise((120, 90), 40).convert("RGB").save(tmp_path / f"p{i}.png")
    t = win.compress
    win.go_to(t)
    t.clear()
    t.add_paths([str(tmp_path)])
    assert _wait(lambda: not t.busy() and t.model.rows, 60)
    for _ in range(5):
        QApplication.processEvents()
    h = t.table.horizontalHeader()
    vp = h.viewport()
    shown = [h.logicalIndex(v) for v in range(h.count()) if not h.isSectionHidden(h.logicalIndex(v))]

    def edge(c):
        return h.sectionViewportPosition(c) + h.sectionSize(c)

    def total():
        return sum(h.sectionSize(c) for c in shown)

    assert total() == vp.width()
    for c in shown[1:]:   # no gap left by hidden columns
        prev = shown[shown.index(c) - 1]
        assert h.sectionViewportPosition(c) == edge(prev)

    def drag(col, dx):
        x, y = edge(col) - 1, h.height() // 2
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, QPoint(x, y))
        for step in range(1, 9):
            QTest.mouseMove(vp, QPoint(x + dx * step // 8, y))
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, QPoint(x + dx, y))
        QApplication.processEvents()

    for col, dx in ((C_NAME, 30), (C_NAME, -20)):
        before = edge(col)
        drag(col, dx)
        assert edge(col) - before == dx, (col, dx, edge(col) - before)
        assert total() == vp.width()
    drag(C_NAME, 3000)                                            # far too far: stops in time
    assert h.sectionSize(C_STATUS) >= h.min_width(C_STATUS) and total() == vp.width()


def test_pause_button_and_battery_pause(win, tmp_path, no_dialogs):
    from wick.settings import Settings

    for i in range(4):
        Image.effect_noise((200, 150), 40).convert("RGB").save(tmp_path / f"p{i}.png")
    t = win.compress
    t.panel.load(Settings(image_format="webp", png_mode="lossy", output_mode="suffix", min_image_kb=0,
                          require_savings=False, video_codec="none"))
    t.clear()
    t.add_paths([str(tmp_path)])
    assert _wait(lambda: not t.busy() and t.model.rows, 60)
    win.qsettings.setValue("pause_on_battery", True)
    win.on_battery = True                        # unplugged before the job starts: it waits
    assert t.start_job(confirm=False)
    assert t.job_thread.pause.is_set() and "battery" in t.job_status.detail.text().lower()
    win.on_battery = False
    win.power_changed.emit(False)                # plugged in: it continues
    assert not t.job_thread.pause.is_set()
    t.job_status.pause_btn.click()               # the user pauses: stays paused even on mains power
    assert t.job_thread.pause.is_set() and t.job_status.pause_btn.text() == "Resume"
    win.power_changed.emit(False)
    assert t.job_thread.pause.is_set()
    t.job_status.pause_btn.click()
    assert not t.job_thread.pause.is_set()
    assert _wait(lambda: not t.busy(), 120)
    win.qsettings.setValue("pause_on_battery", False)


def test_an_unfinished_job_can_be_resumed(win, tmp_path, no_dialogs):
    from wick import pending
    from wick.settings import Settings

    files = []
    for i in range(3):
        f = tmp_path / f"r{i}.png"
        Image.effect_noise((200, 150), 40).convert("RGB").save(f)
        files.append(str(f))
    s = Settings(image_format="webp", png_mode="lossy", output_mode="suffix", min_image_kb=0, require_savings=False,
                 video_codec="none")
    pending.start("compress", s.to_dict(), str(tmp_path), files)   # as if the app closed after the first file
    pending.done("compress", files[0])
    t = win.compress
    t._check_pending()
    assert not t.resume_banner.isHidden() and "2 of 3" in t.resume_banner.detail.text()
    t.resume_banner.resume_btn.click()
    assert _wait(lambda: t.job_thread is not None and not t.busy() and t.model.rows
                 and all(r.result for r in t.model.rows), 120)
    assert sorted(p.name for p in tmp_path.glob("*_compressed.webp")) == ["r1_compressed.webp", "r2_compressed.webp"]
    assert pending.load("compress") is None                       # done: nothing left to resume


def test_clicking_a_row_ticks_it_and_type_selection(win, tmp_path, no_dialogs):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from wick.gui.file_model import C_CHECK, C_NAME
    from wick.settings import Settings

    for i in range(2):
        Image.effect_noise((200, 150), 30).convert("RGB").save(tmp_path / f"p{i}.png")
    Image.effect_noise((200, 150), 30).convert("RGB").save(tmp_path / "j.jpg", quality=95)
    c = win.compress
    c.panel.load(Settings(image_format="webp", png_mode="lossy", jpeg_mode="lossy", min_image_kb=0, video_codec="none",
                          require_savings=False))
    c.clear()
    c.add_paths([str(tmp_path)])
    assert _wait(lambda: not c.busy() and len(c.model.rows) == 3, 60)
    win.go_to(c)
    _wait(lambda: c.table.isVisible(), 5)
    row0 = c.proxy.index(0, C_NAME)
    before = c.proxy.index(0, C_CHECK).data(Qt.CheckStateRole)
    rect = c.table.visualRect(row0)
    QTest.mouseClick(c.table.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())   # on the name, not the box
    after = c.proxy.index(0, C_CHECK).data(Qt.CheckStateRole)
    assert Qt.CheckState(after) != Qt.CheckState(before)
    QTest.mouseClick(c.table.viewport(), Qt.LeftButton, Qt.ControlModifier, rect.center())   # Ctrl: select only
    assert Qt.CheckState(c.proxy.index(0, C_CHECK).data(Qt.CheckStateRole)) == Qt.CheckState(after)
    # Right-click "Deselect All PNG Files": every PNG, and only PNGs.
    from wick.formats import type_label

    for r in c.model.rows:
        r.checked = True
    pngs = [r for r in c.model.rows if type_label(r.entry.path, r.entry.kind) == "PNG"]
    for r in pngs:
        c.model.setData(c.model.index(c.model.rows.index(r), C_CHECK), Qt.Unchecked, Qt.CheckStateRole)
    assert [r.entry.path.suffix for r in c.model.rows if r.checked] == [".jpg"]


def test_date_columns_can_be_shown_and_are_remembered(win, tmp_path):
    from wick.gui.file_model import C_CREATED, C_MODIFIED
    from wick.gui.main_window import open_qsettings

    c = win.compress
    h = c.table.horizontalHeader()
    assert c.table.isColumnHidden(C_CREATED) and c.table.isColumnHidden(C_MODIFIED)   # off until wanted
    h.set_column_shown(C_MODIFIED, True)
    assert C_MODIFIED not in h.user_hidden
    assert str(C_MODIFIED) not in [str(x) for x in (open_qsettings().value("columns/compress/hidden") or [])]
    Image.effect_noise((64, 48), 30).convert("RGB").save(tmp_path / "d.png")
    c.clear()
    c.add_paths([str(tmp_path / "d.png")])
    assert _wait(lambda: not c.busy() and c.model.rows, 60)
    text = c.model.data(c.model.index(0, C_MODIFIED))
    assert len(text) == 16 and text[4] == "-" and text[7] == "-"        # 2026-11-06 14:05
    h.set_column_shown(C_MODIFIED, False)
    assert C_MODIFIED in h.user_hidden


def test_shift_click_ticks_a_range(win, monkeypatch):
    """Click one row, Shift-click another: both and every row between get the first row's state."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

    from wick.gui.filelist import click_to_check

    tree = QTreeWidget()
    items = [QTreeWidgetItem(tree, [f"f{i}"]) for i in range(6)]
    for it in items:
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(0, Qt.Unchecked)
    click_to_check(tree)
    tree.show()
    c = tree._click_to_check
    monkeypatch.setattr(QApplication, "keyboardModifiers", staticmethod(lambda: Qt.NoModifier))
    c._tree_pressed(items[1], 0)
    c._tree_clicked(items[1], 0)                              # a plain click ticks row 1
    monkeypatch.setattr(QApplication, "keyboardModifiers", staticmethod(lambda: Qt.ShiftModifier))
    c._tree_clicked(items[4], 0)                              # Shift-click row 4: rows 1-4 ticked
    assert [it.checkState(0) == Qt.Checked for it in items] == [False, True, True, True, True, False]


def test_compress_list_remove_and_turn_on(win, tmp_path, no_dialogs):
    """Files can be taken off the Compress list (and stay off when the folder is scanned again); a kind whose
    compression is off can be switched on from the list."""
    from wick.settings import Settings

    folder = tmp_path / "pics"
    folder.mkdir()
    for i in range(3):
        Image.effect_noise((200, 150), 30 + i).convert("RGB").save(folder / f"p{i}.png")
    page = win.compress
    page.panel.load(Settings(image_format="none", min_image_kb=0))
    page.clear()
    page.add_paths([str(folder)])
    assert _wait(lambda: not page.busy() and len(page.model.rows) == 3, 60)
    assert all(r.plan.reason == "Image conversion is off" for r in page.model.rows)
    page._turn_on("Image")
    assert _wait(lambda: not page.busy() and all(r.plan.convert for r in page.model.rows), 60)

    by_name = {r.entry.path.name: r for r in page.model.rows}
    by_name["p1.png"].checked = False
    page.remove_rows([by_name["p0.png"]])
    assert sorted(r.entry.path.name for r in page.model.rows) == ["p1.png", "p2.png"]
    assert by_name["p1.png"] in page.model.rows and by_name["p1.png"].checked is False   # ticks are kept
    page.start_scan()                                        # scanned again: the removed file stays off
    assert _wait(lambda: not page.busy() and page.model.rows, 60)
    assert sorted(r.entry.path.name for r in page.model.rows) == ["p1.png", "p2.png"]
    page.clear()
    assert not page.removed


def test_pausing_freezes_loading_bar_and_resuming_restarts_it(win):
    from PySide6.QtWidgets import QApplication
    from types import SimpleNamespace

    from wick.gui.jobstatus import JobStatus

    status = JobStatus()
    status.start(1, 100)
    status.file_started(SimpleNamespace(path=Path("pending.mp4"), size=100))
    status.show()
    QApplication.processEvents()
    assert status.bar._tick.isActive()
    status.set_paused("Paused by you")
    assert not status.bar._tick.isActive() and status.bar._paused_at is not None
    status.set_paused("")
    assert status.bar._tick.isActive() and status.bar._paused_at is None
    status._tick.stop()
    status.close()
    status.deleteLater()


def test_progress_keeps_stage_percentage_and_estimates(win, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from wick.gui import jobstatus

    clock = [100.0]
    monkeypatch.setattr(jobstatus.time, "monotonic", lambda: clock[0])
    status = jobstatus.JobStatus(parent=win)
    entry = SimpleNamespace(path=tmp_path / "clip.mp4", size=100)
    status.start(1, 100)
    status.file_started(entry)
    status.file_step(entry, "Encoding AV1")
    clock[0] = 108
    status.file_progress(entry, 0.5)
    assert "Encoding AV1" in status.detail.text() and "50%" in status.detail.text()
    assert "left in this stage" in status.detail.text()
    status.file_step(entry, "Checking the result")
    assert "50%" not in status.detail.text() and status.bar.maximum() == 0
    clock[0] = 125
    status._render()
    assert "no progress update" in status.detail.text()
    status._tick.stop()
    status.close()
    status.deleteLater()


def test_sidebar_activity_dashboard_handles_unknown_and_paused_progress(win):
    from wick.activity import Activity

    task = Activity("test", "Test conversion", "Encoding sample")
    win.sidebar.set_activity([task])
    assert not win.sidebar.activity_box.isHidden()
    assert win.sidebar.activity_bar.maximum() == 0
    assert "Encoding sample" in win.sidebar.activity_btn.accessibleDescription()
    task.fraction, task.paused = 0.5, True
    win.sidebar.set_activity([task])
    assert win.sidebar.activity_bar.value() == 500
    assert win.sidebar.activity_bar._paused_at is not None
    win._activity_monitor.show()
    assert win._activity_monitor.dialog.isVisible()
    assert win._activity_monitor.table.columnCount() == 5
    win._activity_monitor.dialog.close()
    win.sidebar.set_activity([])


def test_app_compression_algorithms_and_explorer_folder_route(win, tmp_path):
    from wick.cleanup import appcompress

    win.receive_paths([str(tmp_path)], open_target="apps")
    page = win.cleanup
    section = page.section(page.tabs.currentIndex())
    assert section.tool.key == "appcompress"
    assert tmp_path in section.folders
    assert not section.busy()                # Explorer adds for review; it never compresses automatically
    assert {section.app_algorithm.itemData(i) for i in range(section.app_algorithm.count())} == set(appcompress.ALGORITHMS)


def test_community_results_fit_columns_and_sort_numerically(win, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from wick.cleanup import game_results
    from wick.gui.game_results import GameResultsDialog

    data = {
        20: {"name": "Game with a long name", "modes": {
            mode: {"ratio": 0.92, "samples": 131} for mode in game_results.MODES}},
        3: {"name": "Another game", "modes": {"xpress4k": {"ratio": 0.45, "samples": 1}}},
        100: {"name": "No samples yet", "modes": {}},
    }
    monkeypatch.setattr(game_results, "load", lambda: data)
    dialog = GameResultsDialog(win)
    try:
        dialog.show()
        dialog.resize(820, 440)
        QTest.qWait(100)
        assert dialog.table.verticalHeader().isHidden()
        dialog.table.sortItems(1, Qt.AscendingOrder)
        assert [dialog.table.item(i, 1).text() for i in range(3)] == ["3", "20", "100"]
        assert dialog.table.item(0, 2).text() == "55%"
        assert dialog.table.item(0, 2).toolTip() == "55% saved (1 sample)"
        assert dialog.table.item(1, 2).toolTip() == "8% saved (131 samples)"
        for column in range(2, 6):
            item = dialog.table.item(1, column)
            assert dialog.table.fontMetrics().horizontalAdvance(item.text()) < dialog.table.columnWidth(column) - 8
        assert dialog.table.horizontalHeader().length() <= dialog.table.viewport().width()
        dialog.table.sortItems(2, Qt.DescendingOrder)
        assert dialog.table.item(0, 1).text() == "3"
        dialog.search.setText("Game with")
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 1).text() == "20"
        assert all(dialog.table.item(0, column).text() == "8%" for column in range(2, 6))
    finally:
        dialog.close()
        dialog.deleteLater()


def test_undo_from_the_result_bar_and_turn_on_banner(win, tmp_path, no_dialogs):
    """After a job the result bar offers Undo (asks, then removes the results); files skipped because their
    kind is off are named above the list with a button that turns it on."""
    from wick.settings import Settings

    folder = tmp_path / "pics"
    folder.mkdir()
    Image.effect_noise((480, 360), 40).convert("RGB").save(folder / "photo.jpg", quality=95)
    page = win.compress
    page.panel.load(Settings(image_format="none", min_image_kb=0))
    page.clear()
    page.add_paths([str(folder)])
    assert _wait(lambda: not page.busy() and len(page.model.rows) == 1, 60)
    assert not page.off_banner.isHidden() and "1 picture is skipped" in page.off_text.text()
    page.off_buttons.itemAt(0).widget().click()                       # Turn On Pictures
    assert _wait(lambda: not page.busy() and page.model.rows and page.model.rows[0].plan.convert, 60)
    assert page.off_banner.isHidden()

    page.panel.load(Settings(image_format="keep", jpeg_mode="lossless", output_mode="suffix", min_image_kb=0,
                             require_savings=False))
    assert _wait(lambda: not page.busy() and page.model.rows and page.model.rows[0].plan.convert, 60)
    page.start_job(confirm=False)
    assert _wait(lambda: not page.busy() and all(r.result for r in page.model.rows), 120)
    _wait(lambda: page.job_status.title.text().startswith("Done"), 10)
    made = [p for p in folder.iterdir() if "_compressed" in p.name]
    assert made and not page.job_status.undo_btn.isHidden()
    page.job_status.undo_btn.click()                                  # (the confirmation is answered Yes)
    assert _wait(lambda: not any(p.exists() for p in made), 60)
    assert (folder / "photo.jpg").exists() and page.job_status.isHidden()


def test_compress_page_says_where_the_space_comes_from_and_what_will_happen(win, tmp_path):
    """The list appears while details are read; the breakdown by kind filters the list; the settings line
    says what will be done."""
    import shutil
    import subprocess

    from wick.settings import Settings

    folder = tmp_path / "mixed"
    folder.mkdir()
    for i in range(3):
        Image.effect_noise((300, 200), 30 + i).convert("RGB").save(folder / f"p{i}.png")
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2", "-pix_fmt",
                        "yuv420p", "-c:v", "libx264", "-crf", "5", str(folder / "clip.mp4")], check=True)
    page = win.compress
    page.panel.load(Settings(min_image_kb=0, min_video_mb=0))
    page.clear()
    page.add_paths([str(folder)])
    assert _wait(lambda: not page.busy() and len(page.model.rows) >= 3, 90)
    assert page.model.pending_count() == 0
    assert "Pictures to JPEG XL" in page.settings_line.text()
    if (folder / "clip.mp4").exists() and any(r.entry.kind == "video" and r.plan.convert for r in page.model.rows):
        assert not page.breakdown.isHidden() and "group:video" in page.breakdown.text()
        page._show_group("group:video")
        assert page.proxy.rowCount() == 1 and "Show all files" in page.breakdown.text()
        page._show_group("group:all")
        assert page.proxy.rowCount() == 4
    page.clear()


def test_files_from_the_context_menu_can_be_compressed_without_the_window(win, tmp_path, monkeypatch):
    """Settings › "Compress right away": the files are scanned and compressed with the current options, a
    notification says so, and Undo / Show Files work from the notification."""
    from wick.settings import Settings

    folder = tmp_path / "quick"
    folder.mkdir()
    pic = folder / "shot.png"
    Image.effect_noise((200, 150), 40).convert("RGB").save(pic)
    notes = []
    monkeypatch.setattr(win, "tray_message", lambda msg, force=False, actions=(), launch="": notes.append(
        (msg, [a[1] for a in actions])))
    quits = []
    monkeypatch.setattr(win, "quick_done", lambda: quits.append(True))
    page = win.compress
    page.panel.load(Settings(min_image_kb=0, image_format="jxl", output_mode="suffix"))
    page.clear()
    monkeypatch.setattr(win, "isVisible", lambda: False)
    monkeypatch.setattr(win, "isActiveWindow", lambda: False)
    monkeypatch.setattr(page, "window", lambda: win)
    win.receive_paths([str(pic)], show=False)
    assert page.quick
    assert _wait(lambda: quits, 90), notes
    assert notes[0][0].startswith("Compressing 1 file")
    assert "undo-last" in notes[-1][1] and "show-files" in notes[-1][1]
    assert win._last_run and win._last_outputs and win._last_outputs[0].exists()
    page.clear()


def test_the_job_panel_shows_savings_fixes_and_a_before_and_after(win, tmp_path):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    from PIL import Image

    from wick.engine import DONE, FAILED, FileResult
    from wick.formats import JPEG
    from wick.gui.jobstatus import JobStatus
    from wick.scanner import FileEntry

    src, out = tmp_path / "a.jpg", tmp_path / "a-small.jpg"
    Image.effect_noise((300, 200), 40).convert("RGB").save(src, quality=95)
    Image.open(src).save(out, quality=60)
    panel = JobStatus()
    panel.show()
    panel.start(3, 3_000_000)
    a = FileEntry(src, src.stat().st_size, JPEG, 0)
    panel.file_started(a)
    done = FileResult(a, DONE, "Saved", out, out.stat().st_size)
    panel.file_finished(done)
    assert "saved" in panel.title.text() and panel.saved_so_far() == done.saved
    deadline = time.monotonic() + 30
    while panel.compare.isHidden() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert not panel.compare.isHidden() and panel.compare.pics["after"].pixmap() is not None
    assert "Before" in panel.compare.captions["before"].text()
    b = FileEntry(tmp_path / "b.jpg", 1000, JPEG, 0)
    busy = FileResult(b, FAILED, "[WinError 32] being used by another process; original kept")
    keys = []
    panel.fix_requested.connect(keys.append)
    panel.finish([done, busy])
    assert not panel.fix_text.isHidden() and "Close the app" in panel.fix_text.text()
    panel.fix_row.itemAt(0).widget().click()
    assert keys == ["in_use"]
    panel.start(1, 10)                                             # a new job starts clean
    assert panel.fix_text.isHidden() and panel.compare.isHidden()
    # (Its before-and-after pictures load in the background: let them land before the panel goes, so nothing
    # arrives later for a panel that's gone.)
    from PySide6.QtCore import QThreadPool

    QThreadPool.globalInstance().waitForDone(10_000)
    app.processEvents()
    from PySide6.QtCore import QCoreApplication, QEvent

    panel.close()
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_before_and_after_show_in_whichever_order_they_arrive(win, tmp_path):
    from PIL import Image

    from wick.engine import DONE, FileResult
    from wick.formats import JPEG
    from wick.gui.jobstatus import CompareStrip
    from wick.scanner import FileEntry

    png = tmp_path / "x.png"
    Image.new("RGB", (40, 30), "red").save(png)
    strip = CompareStrip()
    e = FileEntry(tmp_path / "a.jpg", 2000, JPEG, 0)
    strip._entry, strip.token = e, 7
    strip._result = FileResult(e, DONE, "Saved", tmp_path / "a.jxl", 1000)
    strip._got("after", 7, png.read_bytes())               # (the result's picture first)
    assert strip.isHidden()
    strip._got("before", 7, png.read_bytes())
    assert not strip.isHidden() and "50% smaller" in strip.note.text()
    strip._got("after", 6, png.read_bytes())               # (a stale one from an earlier file is ignored)
