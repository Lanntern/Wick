"""History, Watch Folders and Settings."""

import os
from pathlib import Path

from PIL import Image

from .conftest import _wait


def test_watch_folder_uses_its_preset(win, tmp_path):
    from PySide6.QtCore import Qt

    watch = win.watch
    folder = tmp_path / "Inbox"
    folder.mkdir()
    watch._add_item(str(folder))
    watch._save()
    item = watch.folders.item(watch.folders.count() - 1)
    watch.folders.clearSelection()
    item.setSelected(True)
    watch.preset.setCurrentIndex(watch.preset.findData("Screenshots"))
    watch._preset_chosen(watch.preset.currentIndex())
    assert "Screenshots" in item.text() and item.data(Qt.UserRole) == str(folder)

    s = watch.settings_for(str(folder / "sub" / "new.png"))
    assert s.image_format == "jxl" and s.video_codec == "none"
    other = watch.settings_for(str(tmp_path / "elsewhere.png"))
    assert other.to_dict() == win.compress.panel.settings().to_dict()

    # Survives a restart of the page (stored in settings).
    assert "Screenshots" in win.qsettings.value("watch_presets", "", str)
    watch.folders.setCurrentItem(item)
    watch.remove_folder()
    assert str(folder) not in win.qsettings.value("watch_presets", "", str)


def test_settings_page_data_and_diagnostics(win, no_dialogs):
    from PySide6.QtWidgets import QApplication

    from wick import index

    page = win.app_settings
    win.go_to(page)
    QApplication.processEvents()
    page._index_toggled(False)
    assert not index._enabled
    page._index_toggled(True)
    assert index._enabled
    page.history_keep.setCurrentIndex(page.history_keep.findData(30))
    page.history_keep.activated.emit(page.history_keep.currentIndex())
    assert win.qsettings.value("history_days", 0, int) == 30
    win.qsettings.setValue("history_days", 0)

    page._copy_diagnostics()
    text = QApplication.clipboard().text()
    assert text.startswith("Wick ") and "Tools:" in text


def test_pages_built_on_demand_and_history_rows_on_expand(win):
    from PySide6.QtWidgets import QApplication

    assert win.built("converter") is not None or "converter" in win._placeholders
    page = win.app_settings                      # builds on first use
    assert win.built("app_settings") is page and win.pages.indexOf(page) >= 0
    win.go_to(win.history)
    QApplication.processEvents()
    tree = win.history.tree
    runs = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).data(0, 0x0100)]
    if runs:
        top = runs[0]
        assert top.childCount() == 0             # file rows only appear when the run is opened
        top.setExpanded(True)
        assert top.childCount() > 0


def test_language_choice_shows_with_only_my_languages(win):
    panel = win.compress.panel
    panel.set_file_kinds(None)        # (an earlier test's file list may have hidden the video options)
    panel.video_codec.setCurrentIndex(panel.video_codec.findData("auto"))
    panel.video_tracks.setCurrentIndex(panel.video_tracks.findData("all"))
    assert not panel.languages_btn.isVisibleTo(panel)
    panel.video_tracks.setCurrentIndex(panel.video_tracks.findData("mine"))
    assert panel.languages_btn.isVisibleTo(panel) and panel.languages_btn.text()
    s = panel.settings()
    assert s.video_tracks == "mine" and s.video_codec == "auto"
    panel.video_tracks.setCurrentIndex(panel.video_tracks.findData("all"))


def test_update_check_setting_and_notice(win):
    from wick import updates
    from wick.gui.widgets import HelpTip

    page = win.app_settings
    assert page.update_frequency.currentData() == "weekly"  # the default
    page.update_frequency.setCurrentIndex(page.update_frequency.findData("monthly"))
    page.update_frequency.activated.emit(page.update_frequency.currentIndex())
    assert updates.frequency(page.qs) == "monthly"            # saved as soon as it's chosen
    page.update_frequency.setCurrentIndex(page.update_frequency.findData("weekly"))
    page.update_frequency.activated.emit(page.update_frequency.currentIndex())
    tips = [t for t in page.findChildren(HelpTip) if t.help_text == updates.HELP]
    assert tips and tips[0].accessibleDescription() == updates.HELP
    win.go_to(page)
    tips[0].click()                                         # a popover, not a tooltip: it stays and wraps
    assert tips[0].is_open()
    pop = tips[0]._popover
    assert pop.width() <= 400 * 2 and pop.body.text() == updates.HELP and pop.body.height() > pop.body.fontMetrics().height()
    assert pop.sticky
    pop.close()
    # Hovering shows the same card without taking the focus; leaving closes it; clicking while hovered keeps it.
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication

    tip = tips[0]
    QApplication.sendEvent(tip, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    _wait(lambda: tip.is_open(), 2)
    assert not tip._popover.sticky and tip._popover.body.text() == updates.HELP
    QApplication.sendEvent(tip, QEvent(QEvent.Leave))
    _wait(lambda: not tip.is_open(), 2)
    QApplication.sendEvent(tip, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    _wait(lambda: tip.is_open(), 2)
    tip.click()
    assert tip.is_open() and tip._popover.sticky
    QApplication.sendEvent(tip, QEvent(QEvent.Leave))
    assert tip.is_open()                                    # pinned by the click: leaving doesn't close it
    tip._popover.close()
    win.sidebar.set_update("99.0", security=True)
    assert win.sidebar.update_btn.isVisibleTo(win.sidebar) and "security" in win.sidebar.update_btn.text().lower()
    win.sidebar.set_update(None)
    assert not win.sidebar.update_btn.isVisibleTo(win.sidebar)


def test_history_marks_what_cant_be_undone(win, tmp_path, no_dialogs):
    import time as _time

    from wick import journal

    w = journal.RunWriter(tmp_path, {"tool": "compress"})
    gone = tmp_path / "gone.png"                      # never in the bin: as if it had been emptied
    out = tmp_path / "gone.webp"
    out.write_bytes(b"x" * 10)
    w.add(journal.FileRecord(str(gone), str(out), 1000, 10, "png", "PNG → WebP", False, _time.time()))
    w.close()
    h = win.history
    win.go_to(h)
    h.refresh(force=True)
    assert _wait(lambda: any((r, str(gone)) in h.blocked for r in [w.run.run_id]), 30)
    top = next(h.tree.topLevelItem(i) for i in range(h.tree.topLevelItemCount())
               if h.tree.topLevelItem(i).data(0, 256)[1].run_id == w.run.run_id)
    assert "can't be undone" in top.text(4)
    h.tree.clearSelection()
    top.setSelected(True)
    h.undo_selected()                                 # nothing undoable: says why, changes nothing
    assert any("Nothing here can be undone" in m for m in no_dialogs)


def test_reports_from_a_job_and_from_history(win, tmp_path, no_dialogs, monkeypatch):
    import csv

    from PySide6.QtWidgets import QFileDialog

    import wick.gui.file_model as fm
    from wick.settings import Settings

    for i in range(2):
        Image.effect_noise((200, 150), 40).convert("RGB").save(tmp_path / f"q{i}.png")
    t = win.compress
    t.panel.load(Settings(image_format="webp", png_mode="lossy", output_mode="suffix", min_image_kb=0,
                          require_savings=False, video_codec="none"))
    t.clear()
    t.add_paths([str(tmp_path / "q0.png"), str(tmp_path / "q1.png")])
    assert _wait(lambda: not t.busy() and t.model.rows, 60)
    assert t.start_job(confirm=False)
    assert _wait(lambda: not t.busy(), 120)
    _wait(lambda: not t.job_status.report_btn.isHidden(), 10)
    out = tmp_path / "report"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(fm, "open_in_file_manager", lambda *a, **k: None)
    t.job_status.report_btn.click()
    with open(tmp_path / "report.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0][0] == "File" and sorted(r[0] for r in rows[1:]) == ["q0.png", "q1.png"]
    assert all(r[2] == "Done" and int(r[3]) > int(r[4]) and r[7].endswith(".webp") for r in rows[1:])

    win.go_to(win.history)
    win.history.refresh(force=True)
    out2 = tmp_path / "history.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out2), "")))
    win.history.tree.clearSelection()
    win.history.export_report()                      # nothing selected: everything in History
    with open(out2, encoding="utf-8-sig", newline="") as f:
        names = [r[0] for r in csv.reader(f)][1:]
    assert {"q0.png", "q1.png"} <= set(names)


def test_where_your_space_goes(win, tmp_path):
    from wick import space
    from wick.gui.space_card import SpaceCard

    b = space.Breakdown("C:", total=1000, free=300, sizes={space.PHOTOS: 200, space.VIDEOS: 100, space.CACHES: 50})
    assert b.used == 700 and b.other == 350
    card = SpaceCard()
    card.show_breakdown(b)
    labels = [w.text() for w in card._legend_widgets if hasattr(w, "text")]
    assert "Photos" in labels and "Everything else" in labels and "Music" not in labels   # empty ones left out
    assert "C: drive" in card.summary.text()
    win.home.space_card.act.emit(space.CACHES)              # "Clean Up" → Clean Up › Caches
    assert win.pages.currentWidget() is win.cleanup
    assert win.cleanup.tabs.tabText(win.cleanup.tabs.currentIndex()) == "Caches"


def test_monthly_check_only_when_on_and_due(win, monkeypatch):
    import time as _time

    from wick import overview, space

    calls, messages = [], []
    monkeypatch.setattr(win.home, "analyze", lambda background=False: calls.append(1))
    monkeypatch.setattr(win, "tray_message", lambda m, force=False: messages.append(m))
    win.qsettings.setValue("monthly_check", False)
    assert not win._monthly_due()                                   # off by default
    win.qsettings.setValue("monthly_check", True)
    win.qsettings.setValue("monthly/last", _time.time() - 5 * 86400)
    assert not win._monthly_due()                                   # not a month yet
    win.qsettings.setValue("monthly/last", 0.0)
    assert win._monthly_due() and calls == [1]
    ov = overview.Overview()
    ov.space = space.Breakdown("C:", 10 ** 12, 10 ** 11, sizes={space.CACHES: 2 * 1024 ** 3})
    win.home.analyzed.emit(ov)
    assert messages and "could be freed" in messages[0] and "Nothing has been changed" in messages[0]
    assert _time.time() - win.qsettings.value("monthly/last", 0.0, float) < 60
    messages.clear()
    win._monthly_running = True
    win.home.analyzed.emit(overview.Overview())                     # little to free: no notification
    assert not messages
    win.qsettings.setValue("monthly_check", False)


def test_history_can_be_deleted_and_turned_off(win, tmp_path, no_dialogs):
    import time as _time

    from wick import journal, pending
    from wick import log as applog

    def run(names):
        w = journal.RunWriter(tmp_path, {"tool": "compress"})
        for n in names:
            w.add(journal.FileRecord(str(tmp_path / n), str(tmp_path / (n + ".jxl")), 1000, 10, "jpeg", "x", False,
                                     _time.time()))
        w.close()
        return w.run.run_id

    journal.clear()
    a, b = run(["secret-a.jpg", "keep.jpg"]), run(["b.jpg"])
    h = win.history
    win.go_to(h)
    h.refresh(force=True)
    assert len(h.runs) == 2
    # One file's record (and name) goes; its run stays with the other file.
    journal.forget_files(a, {str(tmp_path / "secret-a.jpg")})
    assert "secret-a" not in (journal.runs_dir() / f"{a}.jsonl").read_text("utf-8")
    h.refresh(force=True)
    run_a = next(r for r in h.runs if r.run_id == a)
    assert [Path(f.original).name for f in run_a.files] == ["keep.jpg"]
    # Selected runs are deleted from the page; nothing selected deletes everything (after asking).
    h.tree.clearSelection()
    next(h.tree.topLevelItem(i) for i in range(h.tree.topLevelItemCount())
         if h.tree.topLevelItem(i).data(0, 256)[1].run_id == b).setSelected(True)
    h.delete_selected()
    assert [r.run_id for r in h.runs] == [a]
    h.tree.clearSelection()
    h.delete_selected()                                # nothing selected: nothing deleted
    assert [r.run_id for r in h.runs] == [a]
    applog.log.info("a line naming secret-a.jpg")
    h.delete_everything()                              # every run and the log (after asking)
    assert not h.runs and not list(journal.runs_dir().glob("*.jsonl"))
    assert not applog.log_file().exists() or "secret-a" not in applog.log_file().read_text("utf-8")
    h.reload()                                         # Refresh: reads again, rechecks the bin

    # Off: nothing about the files is written (runs, resume lists, log), and what's there can be deleted.
    run(["old.jpg"])
    page = win.app_settings
    try:
        page.history_on.setChecked(False)
        assert not journal.ENABLED and not list(journal.runs_dir().glob("*.jsonl"))    # (asked: deleted)
        assert not applog.log_file().exists()
        run(["private.jpg"])
        assert not list(journal.runs_dir().glob("*.jsonl"))
        pending.start("compress", {}, str(tmp_path), [str(tmp_path / "private.jpg")])
        assert pending.load("compress") is None
        applog.log.info("private.jpg")
        assert not applog.log_file().exists()
        h.refresh(force=True)
        win.go_to(h)
        assert "History is off" in h.placeholder.message
        assert not page.history_keep_row.isEnabled()
    finally:
        page.history_on.setChecked(True)
    assert journal.ENABLED
    run(["new.jpg"])
    assert list(journal.runs_dir().glob("*.jsonl"))


def test_developer_settings_hidden_until_switched_on(win, monkeypatch):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from wick.gui import style
    from wick.gui.about_dialog import AboutDialog
    from wick.gui.devmode import CLICKS

    monkeypatch.delenv("WICK_DEV", raising=False)
    win.qsettings.remove("dev/enabled")
    page = win.app_settings
    page.update_dev_section()
    assert page.dev_section.isHidden()
    d = AboutDialog(win)
    for _ in range(CLICKS):
        ev = QMouseEvent(QEvent.MouseButtonRelease, QPointF(2, 2), QPointF(2, 2), Qt.LeftButton, Qt.NoButton,
                         Qt.NoModifier)
        QApplication.sendEvent(d._version, ev)
    assert not page.dev_section.isHidden()
    page.fluent_icons.setChecked(True)      # (no icon font here: the app's own icons are still drawn)
    assert not style.icon("home").pixmap(20, 20).isNull()
    page.fluent_icons.setChecked(False)
    d._toggle_dev()
    assert page.dev_section.isHidden()
    d.deleteLater()


def test_settings_scrollbar_reaches_edge_and_top_transition_is_passive(win, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor

    from wick.gui import scroll_effects
    from wick.gui.style import Space, tokens

    previous, size = win.pages.currentWidget(), win.size()
    page = win.app_settings
    scroll = page.settings_scroll
    position = scroll.verticalScrollBar().value()
    try:
        win.resize(1100, 760)
        win.go_to(page)
        assert _wait(lambda: scroll.verticalScrollBar().maximum() > Space.XXL, 3)
        assert scroll.mapTo(page, QPoint(scroll.width(), 0)).x() == page.width()
        assert page.title.mapTo(page, QPoint(0, 0)).x() == Space.XXL
        scroll.verticalScrollBar().setValue(0)
        assert _wait(scroll.transition.isHidden, 1)
        scroll.verticalScrollBar().setValue(Space.XXL * 2)
        assert _wait(lambda: scroll.transition.isVisible() and bool(scroll.transition.levels), 3)
        edge = scroll.transition
        assert edge.testAttribute(Qt.WA_TransparentForMouseEvents) and edge.focusPolicy() == Qt.NoFocus
        image = edge.grab().toImage()
        assert image.pixelColor(image.width() // 2, 0).rgb() == QColor(tokens().window).rgb()
        if folder := os.environ.get("WICK_SHOTS_DIR"):
            Path(folder).mkdir(parents=True, exist_ok=True)
            page.grab().save(str(Path(folder) / "settings-scroll-transition.png"))
        with monkeypatch.context() as patch:
            patch.setattr(scroll_effects.style, "high_contrast_active", lambda: True)
            scroll.refresh_transition()
            assert edge.isHidden()
    finally:
        scroll.verticalScrollBar().setValue(position)
        win.resize(size)
        win.go_to(previous)
