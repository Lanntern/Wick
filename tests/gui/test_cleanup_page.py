"""The Clean Up page: its tools, lists, groups, app compression and folder sizes."""



from .conftest import _wait


def test_cleanup_keeps_unticked_items_and_switches_kept_copy(win, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt

    import wick.gui.cleanup_section as ct
    from wick.cleanup.actions import Outcome

    monkeypatch.setattr(ct, "inform", lambda *a, **k: None)
    for folder in ("a", "b", "c", "d"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "photo.bin").write_bytes(b"x" * 300_000)
    sec = next(s for s in win.cleanup.sections if s.tool.key == "duplicates")
    sec.folders = [tmp_path]
    sec.extra_min.set_bytes(1024)
    sec.scan()
    assert _wait(lambda: not sec.busy() and sec.result is not None, 60)
    rows = sec._items()
    assert len(rows) == 3 and all(it.checkState(0) == Qt.Checked for it, _ in rows)

    # Untick one, act on another: the unticked one must stay unticked afterwards.
    (keep_it, kept), (_, acted) = rows[0], rows[1]
    keep_it.setCheckState(0, Qt.Unchecked)
    sec._applied([Outcome(acted, True, "Moved to the Recycle Bin", acted.size)], None)
    states = {c.path: it.checkState(0) for it, c in sec._items()}
    assert states[kept.path] == Qt.Unchecked and acted.path not in states

    # Keep a different copy: it leaves the removable list and the old keeper joins it.
    old_keeper = kept.keeper
    target = next(c for _, c in sec._items() if c.path != kept.path)
    sec.keep_instead(target)
    paths = {c.path for _, c in sec._items()}
    assert target.path not in paths and old_keeper in paths
    assert all(c.keeper == target.path for _, c in sec._items())
    assert {c.path: it.checkState(0) for it, c in sec._items()}[kept.path] == Qt.Unchecked


def test_duplicate_groups_open_and_rows_can_be_chosen(win, tmp_path, monkeypatch):
    """Groups open by themselves; the header arrow closes and opens them all; a group's box ticks its removable
    copies but never the copy that stays; a right-click acts on just the chosen file; nothing done is said so."""
    from PySide6.QtCore import QPoint, Qt

    import wick.gui.cleanup_section as cs
    from wick.cleanup.actions import Outcome
    from wick.cleanup.common import Candidate, ScanResult

    sec = next(s for s in win.cleanup.sections if s.tool.key == "duplicates")
    sec.folders = [tmp_path]
    keeper = tmp_path / "keep.jpg"
    items = [Candidate(tmp_path / f"g{g}" / f"copy{k}.jpg", 1000, group=str(g), keeper=keeper, selected=True)
             for g in range(60) for k in range(2)]
    sec._scanned(ScanResult("duplicates", items, []))
    groups = sec._groups()
    assert len(groups) == 60 and all(g.isExpanded() for g in groups)      # (40+ groups used to stay closed)
    header = sec.tree.header()
    assert sec._all_open() is True and header._expander_width(0) > 0
    sec.open_all(False)
    assert not any(g.isExpanded() for g in groups) and sec._all_open() is False
    header.mousePressEvent(type("E", (), {"button": lambda s: Qt.LeftButton,
                                          "position": lambda s: QPoint(2, 5).toPointF(), "accept": lambda s: None})())
    assert all(g.isExpanded() for g in groups)                              # the header arrow opens them all

    g = groups[0]
    keep_row = next(g.child(i) for i in range(g.childCount()) if not g.child(i).flags() & Qt.ItemIsUserCheckable)
    copies = [g.child(i) for i in range(g.childCount()) if g.child(i).flags() & Qt.ItemIsUserCheckable]
    assert g.flags() & Qt.ItemIsUserCheckable and g.checkState(0) == Qt.Checked     # a box, showing its rows
    g.setCheckState(0, Qt.Unchecked)
    assert all(c.checkState(0) == Qt.Unchecked for c in copies)
    assert keep_row.data(0, Qt.CheckStateRole) is None                      # the copy that stays: never ticked
    copies[0].setCheckState(0, Qt.Checked)
    assert g.checkState(0) == Qt.PartiallyChecked                           # one of its copies chosen
    g.setCheckState(0, Qt.Checked)
    assert all(c.checkState(0) == Qt.Checked for c in copies) and keep_row.data(0, Qt.CheckStateRole) is None
    assert len(sec._items(True)) == len(items) and keeper not in [c.path for _, c in sec._items()]

    ran: list = []
    monkeypatch.setattr(cs, "ask", lambda *a, **k: True)
    monkeypatch.setattr(cs._Apply, "start", lambda self: ran.append([c.path.name for c in self.items]))
    sec.run_action(cs.actions.TRASH, only=[items[0]])
    assert ran == [["copy0.jpg"]]                                          # just that file, whatever is ticked
    sec.worker = None

    shown: list = []
    monkeypatch.setattr(cs, "inform", lambda _w, title, *_a, **_k: shown.append(title))
    sec._applied([Outcome(items[1], False, "In use")], None)
    assert shown == ["Nothing was changed"]                                 # not "Freed 0 B from 0 items"


def test_cleanup_hides_what_isnt_suggested(win, tmp_path):
    from wick.cleanup.common import Candidate, ScanResult

    win.go_to(win.cleanup)
    sec = win.cleanup.sections[0]
    ok = Candidate(tmp_path / "a.zip", 1000)
    risky = Candidate(tmp_path / "b.zip", 2000, warning="still in use", selected=False)
    sec.hide_unsuggested.setChecked(True)
    sec._scanned(ScanResult(sec.tool.key, [ok, risky]))
    shown = [c.path.name for _, c in sec._items()]
    assert shown == ["a.zip"] and not sec.hide_unsuggested.isHidden()
    assert "1 not suggested, hidden" in sec.summary.text()
    sec.hide_unsuggested.setChecked(False)
    assert sorted(c.path.name for _, c in sec._items()) == ["a.zip", "b.zip"]
    sec.hide_unsuggested.setChecked(True)


def test_folder_sizes_open_level_by_level(win, tmp_path, no_dialogs, monkeypatch):
    from PySide6.QtCore import Qt

    from wick.cleanup.folders import find_folder_sizes
    from wick.gui import cleanup_section
    from wick.gui.cleanup_section import ROLE_ITEM

    monkeypatch.setattr(cleanup_section, "choose", lambda *a, **k: False)

    (tmp_path / "Big" / "Deep").mkdir(parents=True)
    (tmp_path / "Small").mkdir()
    (tmp_path / "AppData").mkdir()
    (tmp_path / "Big" / "Deep" / "movie.bin").write_bytes(b"x" * 300_000)
    (tmp_path / "Big" / "notes.txt").write_bytes(b"x" * 1000)
    (tmp_path / "Small" / "a.txt").write_bytes(b"x" * 5000)
    (tmp_path / "AppData" / "cache.bin").write_bytes(b"x" * 50_000)
    win.go_to(win.cleanup)
    sec = next(s for s in win.cleanup.sections if s.tool.key == "folders")
    sec.folders = [tmp_path]
    sec._scanned(find_folder_sizes([tmp_path]))
    root = sec.tree.topLevelItem(0)
    assert root.isExpanded()
    names = [root.child(i).text(0) for i in range(root.childCount())]
    assert names == ["Big", "AppData", "Small"]                         # biggest first
    appdata = root.child(1)
    assert not appdata.flags() & Qt.ItemIsUserCheckable                 # apps need it: can't be selected
    big = root.child(0)
    assert big.childCount() == 0
    big.setExpanded(True)                                               # rows made when it opens
    assert [big.child(i).text(0) for i in range(big.childCount())] == ["Deep", "notes.txt"]
    big.setCheckState(0, Qt.Checked)
    big.child(0).setCheckState(0, Qt.Checked)                          # inside a chosen folder: not counted twice
    assert [c.path.name for _, c in sec._items(True)] == ["Big"]
    assert "1 selected" in sec.summary.text()
    big.child(0).setCheckState(0, Qt.Unchecked)
    big.setCheckState(0, Qt.Unchecked)
    big.child(0).setCheckState(0, Qt.Checked)                          # remove Big/Deep
    sec.run_action("trash")
    assert _wait(lambda: not sec.busy(), 30)
    assert not (tmp_path / "Big" / "Deep").exists()
    root = sec.tree.topLevelItem(0)
    big = next(root.child(i) for i in range(root.childCount()) if root.child(i).text(0) == "Big")
    assert big.isExpanded() and [big.child(i).text(0) for i in range(big.childCount())] == ["notes.txt"]
    assert big.data(0, ROLE_ITEM).size == 1000
    assert root.child(0).text(0) == "AppData"                           # sizes went down: order follows


def test_cleanup_results_are_visible_before_scan_finishes(win, tmp_path):
    import threading

    from wick.cleanup import actions
    from wick.cleanup.common import Candidate, ScanResult
    from wick.gui.cleanup_section import Tool, ToolSection

    release = threading.Event()

    def scan(_section, _progress, cancel):
        result = ScanResult("stream-test")
        result.items.append(Candidate(tmp_path / "found.txt", 1))
        while not release.wait(0.05) and not cancel.is_set():
            pass
        return result

    tool = Tool("stream-test", "Streaming test", "Test", lambda: [tmp_path], scan,
                [(actions.TRASH, "Remove", "primary")])
    section = ToolSection(tool, win.cleanup)
    section.scan()
    try:
        assert _wait(lambda: section.tree.topLevelItemCount() == 1, 2)
        assert section.busy()
        assert "so far" in section.summary.text()
        assert not section.action_buttons[actions.TRASH].isEnabled()
    finally:
        release.set()
        assert _wait(lambda: not section.busy(), 3)
        section.deleteLater()


def test_folder_scope_shows_paths_and_edits_are_staged(win, tmp_path):
    from wick.gui.folders import FolderManager

    original = win.home.roots
    roots = [tmp_path / "one", tmp_path / "two"]
    try:
        win.home.roots = roots
        win.home._show_folders()
        assert str(roots[0]) in win.home.folder_label.text()
        assert str(roots[1]) in win.home.folder_label.toolTip()
        dialog = FolderManager(win.home, roots, lambda: [], win.qsettings)
        assert dialog.list.item(0).text() == str(roots[0])
        dialog.list.setCurrentRow(0)
        dialog.remove()
        assert dialog.paths() == [roots[1]]
        assert win.home.roots == roots       # a cancelled edit must not change the active scope
        dialog.reject()
        dialog.deleteLater()
    finally:
        win.home.roots = original
        win.home._show_folders()


def test_app_algorithm_names_and_checkbox_file_types(win):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    from wick.cleanup import appcompress
    from wick.gui.app_controls import FileTypePicker, algorithm_picker

    group, combo = algorithm_picker("xpress4k")
    assert [combo.itemText(i) for i in range(combo.count())] == [v[0] for v in appcompress.ALGORITHMS.values()]
    combo.setCurrentIndex(combo.findData("lzx"))
    assert any(label.text() == appcompress.ALGORITHMS["lzx"][1] for label in group.findChildren(QLabel))
    picker = FileTypePicker(".mp4,.custom")
    assert picker.values() == [".custom", ".mp4"]
    item = next(picker.types.item(i) for i in range(picker.types.count()) if picker.types.item(i).text() == ".mp4")
    item.setCheckState(Qt.Unchecked)
    assert picker.values() == [".custom"]
    picker.custom.setText("pak")
    picker.add_custom()
    assert picker.values() == [".custom", ".pak"]
    picker.deleteLater()
    group.deleteLater()


def test_app_table_separates_sizes_savings_and_settings(win, tmp_path, monkeypatch, no_dialogs):
    from wick.cleanup.common import Candidate, ScanResult
    from wick.gui.filelist import SORT_ROLE

    page = win.cleanup
    section = page.section(next(i for i, tool in enumerate(page._tools) if tool.key == "appcompress"))
    candidate = Candidate(tmp_path / "Game", 40, extra={
        "disk": 60, "logical": 100, "before_first": None, "algorithm": "xpress4k",
        "state": "Not compressed", "estimate_source": "rough estimate", "skip_extensions": []})
    section._scanned(ScanResult("appcompress", [candidate]))
    assert section.tree.columnCount() == 9 and section.tree.isColumnHidden(3)
    row = section.tree.topLevelItem(0)
    assert row.data(2, SORT_ROLE) == 60 and row.data(4, SORT_ROLE) == 40
    assert row.text(5) == "Rough" and row.text(6) == "XPRESS4K"
    assert row.text(7) == "Not compressed" and row.text(8) == ""
    candidate.extra["before_first"] = 100
    section._scanned(ScanResult("appcompress", [candidate]))
    assert not section.tree.isColumnHidden(3)
    original = section.app_algorithm.currentIndex()
    wanted = "xpress8k" if section.app_algorithm.currentData() == "lzx" else "lzx"
    section.app_algorithm.setCurrentIndex(section.app_algorithm.findData(wanted))
    assert candidate.extra["algorithm"] == wanted and candidate.extra["estimate_pending"]
    assert section.tree.topLevelItem(0).text(4) == "–"
    assert section.tree.topLevelItem(0).text(5) == "Rescan"
    section.app_algorithm.setCurrentIndex(original)
    candidate.group = "Games"
    other = Candidate(tmp_path / "Other", 20, group="Games", extra={"disk": 80, "before_first": None})
    section._scanned(ScanResult("appcompress", [candidate, other]))
    group = section.tree.topLevelItem(0)
    assert group.text(3) == "–"      # do not sum a partial historical baseline
    assert group.text(4) == "–"      # do not present incomplete estimates as a group total

    from types import SimpleNamespace

    from wick.cleanup import actions

    # A second compression must replace a previous decompression's status.
    candidate.extra.update(before_this_run=60, disk_after=40, last_action="Decompressed")
    with monkeypatch.context() as patch:
        patch.setattr(section, "worker", SimpleNamespace(action=actions.COMPACT, isRunning=lambda: False))
        section._applied([SimpleNamespace(item=candidate, ok=True, freed=20, message="Compressed")], None)
    assert candidate.extra["state"] == "Compressed"


def test_app_algorithm_changes_keep_controls_in_place(win):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QCheckBox, QLabel

    page = win.cleanup
    index = next(i for i, tool in enumerate(page._tools) if tool.key == "appcompress")
    section = page.section(index)
    old_page, old_size, old_tab = win.pages.currentWidget(), win.size(), page.tabs.currentIndex()
    old_algorithm = section.app_algorithm.currentIndex()
    try:
        win.go_to(page)
        page.tabs.setCurrentIndex(index)
        assert not section.tool.note
        assert not any(box.text() == "Start Wick with Windows" for box in section.findChildren(QCheckBox))
        for width in (1000, 1500):
            win.resize(width, 1000)
            group = section.app_algorithm.parentWidget()

            def placed(group=group):
                return (group.geometry(), section.app_skip.geometry(), section.scan_btn.geometry(),
                        section.tree.mapTo(section, section.tree.rect().topLeft()))

            def settled():
                """The layout once it has stopped changing (a resize is applied over several event-loop turns
                on Windows: a scroll bar appearing late moved the button after a fixed wait)."""
                last = placed()
                for _ in range(40):
                    QTest.qWait(100)
                    now = placed()
                    if now == last:
                        return now
                    last = now
                return last

            baseline = settled()
            for choice in range(section.app_algorithm.count()):
                section.app_algorithm.setCurrentIndex(choice)
                assert settled() == baseline
                reason = group.findChildren(QLabel)[0]
                assert reason.text() == section.app_algorithm.toolTip()
    finally:
        section.app_algorithm.setCurrentIndex(old_algorithm)
        page.tabs.setCurrentIndex(old_tab)
        win.resize(old_size)
        win.go_to(old_page)


def test_clean_up_offers_only_this_systems_tools_and_matches_other_pages(win):
    from wick.gui.cleanup_tools import tools

    win.go_to(win.cleanup)
    cu = win.cleanup
    hidden = [i for i, t in enumerate(cu._tools) if t.available()]
    assert hidden, "this test needs a tool that can't run here (Windows', Mac's or both)"
    for i in hidden:
        assert not cu.tabs.isTabVisible(i) and cu.picker.view().isRowHidden(i)
    # Ctrl+Tab goes around only the tools offered here.
    seen = set()
    for _ in range(len(cu._tools) + 2):
        cu.step(1)
        seen.add(cu.tabs.currentIndex())
    assert seen == {i for i in range(len(cu._tools)) if i not in hidden}
    # A link straight to one still opens it, saying why it can't run (instead of "Press Scan").
    key = cu._tools[hidden[0]].key
    win.open_cleanup_tool(key)
    section = cu.stack.currentWidget()
    assert cu.picker.currentIndex() == hidden[0] and not section.scan_btn.isEnabled()
    assert "can't run here" in section.placeholder.message
    # The picker shows the tool on screen, and the list says what to do once, not twice.
    win.open_cleanup_tool("large")
    assert cu.picker.currentText() == "Large Files" and cu.stack.currentWidget().summary.isHidden()
    # "Scan Several Tools" is a page action, in the header like other pages' actions.
    assert any(cu.action_bar.itemAt(i).widget().text() == "Scan Several Tools…" for i in range(cu.action_bar.count()))
    assert len(tools()) == len(cu._tools)
