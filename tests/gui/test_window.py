"""The main window as a whole: every page at any size, the sidebar, tray, taskbar and title bar, keyboard use, dialogs,
notices and the command bar."""

import os
import time
from pathlib import Path

import pytest
from PIL import Image

from .conftest import _wait, _widest


def test_every_page_opens_at_any_size(win):
    from PySide6.QtWidgets import QApplication

    from wick.gui.style import text_scale

    k = text_scale()  # with larger system text, the same layout needs a proportionally larger window
    for width, height in ((int(1320 * k), int(840 * k)), (int(900 * k), int(640 * k)), (int(1920 * k), 1200)):
        win.resize(width, height)
        for i in range(win.pages.count()):
            win.sidebar.list.setCurrentRow(i)
            for _ in range(3):                  # (a page that rearranges itself for the width settles in a turn)
                QApplication.processEvents()
            page = win.pages.currentWidget()
            assert page is win.pages.widget(i)
            assert page.isVisible()
            # Nothing is wider than the window (no clipped layouts), and page content fits the width it's
            # given (it adapts: rows wrap, toolbars compact) instead of needing to scroll sideways.
            assert page.minimumSizeHint().width() <= max(width, win.minimumWidth()), \
                f"text scale {k:.2f}: {_widest(page)}"
            from wick.gui.widgets import _BodyScroll

            for sc in page.findChildren(_BodyScroll):
                if sc.isVisible():
                    assert sc.widget().width() <= sc.viewport().width() + 1, \
                        f"text scale {k:.2f}, {width}px: {_widest(sc)}"


def test_jump_list_and_tray_open_pages(win):
    """--open=TARGET (taskbar/Start jump list, tray menu) shows that page, in this window or a running one."""
    for target, attr in (("history", "history"), ("cleanup", "cleanup"), ("settings", "app_settings"),
                         ("compress", "compress")):
        win.receive_paths([], show=False, open_target=target)
        assert win.pages.currentWidget() is getattr(win, attr), target
    win.receive_paths([], show=False, open_target="no-such-page")         # ignored
    assert win.pages.currentWidget() is win.compress


def test_support_link_and_one_time_prompt(win):
    from wick.gui import support

    win.sidebar.support_btn.click()                                         # any time, from the sidebar
    dlg = win._support_dialog
    assert dlg.isVisible() and not dlg.isModal() and dlg.star.text() and dlg.donate.text()
    dlg.close()
    qs = win.qsettings
    qs.setValue(support.FIRST_SEEN, 0.0)
    qs.setValue(support.SAVED_MOST, 11 * 1024 ** 3)
    qs.remove(support.ASKED)
    win.show_window()
    win._maybe_ask_support()
    assert win._support_dialog.isVisible() and qs.value(support.ASKED, False, bool)
    win._support_dialog.close()
    shown = win._support_dialog
    win._maybe_ask_support()                                                # asked once only
    assert win._support_dialog is shown
    qs.remove(support.SAVED_MOST)
    qs.remove(support.FIRST_SEEN)


def test_taskbar_mirrors_the_job(win):
    """The taskbar button follows a job panel: moving, paused, finished or failed; its Pause and Stop buttons
    press the panel's own. On Windows the real taskbar calls must succeed too."""
    from pathlib import Path
    from types import SimpleNamespace

    from wick.engine import DONE, FAILED
    from wick.gui import taskbar as tbmod

    tb, panel = win.taskbar, win.compress.job_status
    panel.start(2, 2000)
    assert tb._jobs[id(panel)][1] == "indeterminate" and tb.active_panel() is panel
    tb.clicked(tbmod.PAUSE_ID)                                    # the Pause button in the taskbar preview
    assert panel.pause_btn.isChecked() and tb._jobs[id(panel)][1] == "paused"
    tb.clicked(tbmod.PAUSE_ID)
    assert not panel.pause_btn.isChecked()
    entry = SimpleNamespace(path=Path("a.jpg"), size=1000)
    ok = SimpleNamespace(entry=entry, status=DONE, output=None, message="", step="", new_size=500, saved=500)
    panel.finish([ok])
    assert id(panel) not in tb._jobs and tb.active_panel() is None
    panel.start(1, 1000)
    bad = SimpleNamespace(entry=entry, status=FAILED, output=None, message="boom", step="Encoding", new_size=0,
                          saved=0)
    panel.finish([bad])
    assert tb._jobs[id(panel)][1] == "error"                      # red until the window is looked at
    tb.clear_error()
    assert not tb._jobs
    if os.name == "nt":
        assert tb._list is not None and not tb._failed, tb.error  # ITaskbarList3 answered
    panel.hide()


def test_tray_menu(win):
    if win.tray is None:
        pytest.skip("no system tray here")
    actions = {a.text(): a for a in win._tray_menu.actions() if a.text()}
    assert win._tray_menu.defaultAction() is actions["Open Wick"]
    assert {"Compress Files…", "Convert Files…", "Clean Up", "History", "Watch Folders", "Check for Updates",
            "About Wick", "Quit"} <= set(actions)
    actions["History"].trigger()
    assert win.pages.currentWidget() is win.history


def test_keyboard_basics(win, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    for i in range(3):
        Image.new("RGB", (64 + i, 48), "teal").save(tmp_path / f"k{i}.png")
    conv = win.converter
    conv.clear()
    conv.add_paths([str(tmp_path)])
    assert _wait(lambda: not conv.busy() and conv.tree.topLevelItemCount() == 3, 30)

    # Ctrl+3 opens Convert with the focus in its list, and a row highlighted.
    for w in QApplication.topLevelWidgets():   # earlier tests may have left another window active
        if w is not win and w.isVisible() and w.isWindow():
            w.close()
    win.raise_()
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 2000)
    win.sidebar.list.setFocus()
    QTest.keyClick(win.sidebar.list, Qt.Key_3, Qt.ControlModifier)
    _wait(lambda: win.pages.currentWidget() is conv and conv.tree.hasFocus(), 5)
    from PySide6.QtWidgets import QApplication as _A
    assert win.pages.currentWidget() is conv, (win.pages.currentWidget(), _A.focusWidget(), _A.activeWindow())
    assert conv.tree.hasFocus(), (_A.focusWidget(), conv.tree.isVisible(), conv.tree.model().rowCount())
    assert conv.tree.selectedItems()

    ticked = lambda: sum(conv.tree.topLevelItem(i).checkState(0) == Qt.Checked for i in range(3))  # noqa: E731
    QTest.keyClick(conv.tree, Qt.Key_A, Qt.ControlModifier | Qt.ShiftModifier)
    assert ticked() == 0
    QTest.keyClick(conv.tree, Qt.Key_A, Qt.ControlModifier)
    assert ticked() == 3
    QTest.keyClick(conv.tree, Qt.Key_Space)                      # Space toggles the current row
    assert ticked() == 2

    # The context menu opens for the current row from the keyboard.
    opened = []

    from wick.gui import filejob_page

    original_menu = filejob_page.file_menu

    def closing_menu(*args, **kwargs):
        menu = original_menu(*args, **kwargs)

        def shown():
            opened.append([a.text() for a in menu.actions()])
            # Close this exact menu after its nested event loop starts. The active popup can be a
            # different widget (or None) on headless runners, so a global single-shot can miss it.
            QTimer.singleShot(0, menu.close)

        menu.aboutToShow.connect(shown)
        return menu

    monkeypatch.setattr(filejob_page, "file_menu", closing_menu)
    conv.tree._keyboard.menu_for_current()
    assert opened and "Copy Path" in opened[0]


def test_about_dialog(win):
    from PySide6.QtCore import Qt

    from wick.about import CREDITS
    from wick.gui.about_dialog import AboutDialog

    d = AboutDialog(win)
    d.show()
    assert d.tree.topLevelItemCount() == len(CREDITS)
    assert any("FFmpeg" in d.tree.topLevelItem(i).text(0) for i in range(len(CREDITS)))
    # Deliver Esc to the dialog itself (window activation isn't reliable on CI desktops).
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    # (As in the app, where the modal dialog has the focus: leftover focus in the main window from earlier
    # tests would otherwise let that page's own Esc shortcut take the key.)
    if QApplication.focusWidget() is not None:
        QApplication.focusWidget().clearFocus()
    QApplication.sendEvent(d, QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not d.isVisible()


def test_title_bar_areas():
    """The app's own title bar: which part of the window drags, resizes, maximizes or is ordinary content."""
    from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QWidget

    from wick.gui import winframe as wf
    from wick.gui.style import Size

    QApplication.instance() or QApplication([])
    w = QMainWindow()
    w.setCentralWidget(QWidget())
    w.resize(800, 500)
    frame = wf.WindowFrame(w)
    in_band = QPushButton("Back", w.centralWidget())
    in_band.setGeometry(300, 4, 60, 30)
    frame.place()
    b = frame.buttons
    assert b.geometry().right() == w.width() - 1 and b.y() == 0
    mid = Size.TITLE_BAR // 2
    assert frame.area_at(400, mid) == wf.HTCAPTION                 # empty title strip: drag, Snap, menu
    assert frame.area_at(b.x() + b.max.x() + 20, mid) == wf.HTMAXBUTTON   # Snap Layouts flyout
    assert frame.area_at(b.x() + b.close.x() + 20, mid) == wf.HTCLIENT    # our close button gets the click
    assert frame.area_at(b.x() + b.min.x() + 20, mid) == wf.HTCLIENT
    assert frame.area_at(320, mid) == wf.HTCLIENT                  # a control in the strip stays clickable
    assert frame.area_at(400, Size.TITLE_BAR + 10) == wf.HTCLIENT  # page content
    assert frame.area_at(1, 1) == wf.HTTOPLEFT
    assert frame.area_at(1, 250) == wf.HTLEFT
    assert frame.area_at(799, 499) == wf.HTBOTTOMRIGHT
    assert frame.area_at(400, 2) == wf.HTTOP
    w.close()


def test_title_bar_on_windows(win):
    """Windows only (real desktop on CI): the whole window is client area, Windows hit-tests our strip and
    buttons, maximizing fills the work area exactly. Screenshots go to WICK_SHOTS_DIR if set."""
    if win.frame is None or not win.frame.native:
        pytest.skip("the app's own title bar is used on Windows")
    import ctypes
    from ctypes import wintypes
    from pathlib import Path

    from PySide6.QtWidgets import QApplication

    from wick.gui import winframe as wf
    from wick.gui.style import Size
    from wick.gui.theme import apply_theme

    user32 = ctypes.windll.user32
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    hwnd = int(win.winId())
    shots = os.environ.get("WICK_SHOTS_DIR")

    def rects():
        wr, cr = wintypes.RECT(), wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(wr))
        user32.GetClientRect(hwnd, ctypes.byref(cr))
        tl = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(tl))
        return wr, (tl.x, tl.y, tl.x + cr.right, tl.y + cr.bottom)

    def hit(lx, ly):
        dpr = win.devicePixelRatioF()
        _, (cx, cy, _, _) = rects()
        x, y = int(cx + lx * dpr), int(cy + ly * dpr)
        return user32.SendMessageW(hwnd, 0x0084, 0, (y & 0xFFFF) << 16 | (x & 0xFFFF))

    def shot(name):
        if not shots:
            return
        QApplication.processEvents()
        time.sleep(0.4)
        QApplication.processEvents()
        g = win.frameGeometry()
        pad = 24
        img = win.screen().grabWindow(0, g.x() - pad, g.y() - pad, g.width() + 2 * pad, g.height() + 2 * pad)
        Path(shots).mkdir(parents=True, exist_ok=True)
        img.save(str(Path(shots) / f"{name}.png"))

    win.showNormal()
    win.resize(1280, 800)
    win.raise_()
    win.activateWindow()
    _wait(lambda: win.isActiveWindow(), 5)
    wr, cr = rects()
    assert (wr.left, wr.top, wr.right, wr.bottom) == cr, "the whole window should be client area"
    b = win.frame.buttons
    mid = Size.TITLE_BAR // 2
    assert hit(win.width() // 2, mid) == wf.HTCAPTION
    assert hit(b.x() + b.max.x() + 20, mid) == wf.HTMAXBUTTON
    assert hit(b.x() + b.close.x() + 20, mid) == wf.HTCLIENT
    assert hit(1, 1) == wf.HTTOPLEFT
    assert hit(win.width() // 2, win.height() // 2) == wf.HTCLIENT
    shot("window-light")

    user32.SendMessageW(hwnd, 0x0112, 0xF030, 0)   # SC_MAXIMIZE, as the button does
    assert _wait(lambda: win.isMaximized(), 5)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    user32.MonitorFromWindow.restype = ctypes.c_void_p
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(ctypes.c_void_p(user32.MonitorFromWindow(hwnd, 2)), ctypes.byref(mi))
    work = (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom)
    _wait(lambda: rects()[1] == work, 3)
    assert rects()[1] == work, f"maximized client {rects()[1]} should be the work area {work}"
    assert hit(1, 1) == wf.HTCAPTION                # no resize edges when maximized
    assert b.max.accessibleName() == "Restore"
    shot("window-maximized")

    user32.SendMessageW(hwnd, 0x0112, 0xF120, 0)   # SC_RESTORE
    assert _wait(lambda: not win.isMaximized(), 5)
    apply_theme("dark")
    win.refresh_colors()
    shot("window-dark")
    apply_theme("light")
    win.refresh_colors()


def test_about_lists_versions(win):
    from wick.gui.about_dialog import AboutDialog

    dlg = AboutDialog(win)
    rows = {dlg.tree.topLevelItem(i).text(0): dlg.tree.topLevelItem(i).text(1)
            for i in range(dlg.tree.topLevelItemCount())}
    assert rows["Pillow"].split(".")[0].isdigit() and rows["Python"]
    dlg.close()


def test_welcome_once_then_a_first_look(win, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from wick.gui.welcome import WelcomeDialog

    looked = []
    monkeypatch.setattr(win.home, "analyze", lambda background=False: looked.append(True))
    win.qsettings.setValue("welcome_done", False)
    win._welcome()
    dlg = win._welcome_dialog
    assert isinstance(dlg, WelcomeDialog) and dlg.isVisible()
    texts = " ".join(lab.text() for lab in dlg.findChildren(__import__("PySide6.QtWidgets").QtWidgets.QLabel))
    assert "stay on this computer" in texts and "can be undone" in texts
    dlg.accept()
    QApplication.processEvents()
    assert looked and win.qsettings.value("welcome_done", False, bool)
    assert win.pages.currentWidget() is win.home


def test_icons_drawn_at_display_scale(win):
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon

    from wick.gui import style

    ic = style.icon("home")
    for scale in (1.0, 1.25, 2.5):
        pm = ic.pixmap(QSize(20, 20), scale)
        assert pm.width() == round(20 * scale) and pm.devicePixelRatio() == scale
    assert not ic.pixmap(QSize(16, 16), 1.0, QIcon.Disabled).isNull()
    assert style.icon_pixmap("home", "#fff", 16, 1.75).width() == 28


def test_every_control_has_a_name_for_screen_readers(win):
    """Everything Tab can reach on every page (and in About) has a name Narrator reads out, a dropdown's list and a
    number field's text box included; symbol-only buttons ("…") say what they do, and dropdowns are named rather
    than read out as just their current value."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QAccessible, QFocusEvent
    from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QApplication, QComboBox, QScrollArea, QScrollBar, QWidget

    from wick.gui.about_dialog import AboutDialog
    from wick.gui.main_window import LAZY_PAGES

    for name in LAZY_PAGES:
        getattr(win, name)
    assert win.cleanup.sections                 # (builds every Clean Up tool)
    about = AboutDialog(win)
    problems = []
    for root in (win, about):
        for w in root.findChildren(QWidget):
            w.ensurePolished()
        for w in root.findChildren(QWidget):          # (what Narrator sees when a list opens or a field is focused)
            if isinstance(w, QComboBox):
                QApplication.sendEvent(w.view(), QEvent(QEvent.Show))
            elif isinstance(w, QAbstractSpinBox) and w.lineEdit() is not None:
                QApplication.sendEvent(w.lineEdit(), QFocusEvent(QEvent.FocusIn))
        for w in root.findChildren(QWidget):
            if not (w.focusPolicy() & Qt.TabFocus) or isinstance(w, (QScrollBar, QScrollArea)):
                continue
            if w.objectName() == "updateLink":        # (named when it appears, with the version)
                continue
            iface = QAccessible.queryAccessibleInterface(w)
            label = (iface.text(QAccessible.Name) if iface else "") or ""
            what = f"{type(w).__name__} {w.objectName()!r} tip={w.toolTip()[:40]!r}"
            if not label.strip():
                problems.append("no name: " + what)
            elif isinstance(w, QComboBox) and not w.accessibleName():
                problems.append(f"named only by its value {label!r}: " + what)
            elif isinstance(w, QAbstractButton) and not any(ch.isalnum() for ch in label):
                problems.append(f"symbol only {label!r}: " + what)
    about.deleteLater()
    assert not problems, "\n".join(sorted(set(problems)))


def test_tasks_can_be_stopped_from_the_tasks_panel(win):
    """Every running task can be stopped from the panel; it shows "Stopping…" until it ends."""
    import threading

    from PySide6.QtCore import QThread

    from wick.activity import Activity

    class Slow(QThread):
        def __init__(self, parent):
            super().__init__(parent)
            self.cancel = threading.Event()

        def run(self):
            self.cancel.wait(20)

    page = win.home
    old = page.worker
    mon = win._activity_monitor
    page.worker = Slow(win)
    page.worker.start()
    try:
        mon.refresh()
        task = next(t for t in mon.tasks if t.name == "Home analysis")
        assert task.stop is not None
        mon.show()
        task = next(t for t in mon.tasks if t.key == task.key)        # (the panel read the tasks afresh)
        mon.table.selectRow(mon.tasks.index(task))
        assert mon.stop_btn.isEnabled()
        mon.stop_btn.click()
        assert page.worker.cancel.is_set() and task.key in mon.stopping
        row = next(i for i, t in enumerate(mon.tasks) if t.key == task.key)
        assert mon.table.item(row, 4).text() == "Stopping…"
        assert _wait(lambda: not page.worker.isRunning(), 10)
        mon.refresh()
        assert not any(t.name == "Home analysis" for t in mon.tasks) and not mon.stopping
        mon.stop_task(Activity("x", "Gone", "", stop=None))           # (nothing to ask: ignored)
    finally:
        page.worker.wait(5000)
        page.worker = old
        if mon.dialog is not None:
            mon.dialog.hide()


def test_background_errors_are_shown_and_quitting_never_destroys_a_running_task(win, monkeypatch):
    import threading
    import time

    from PySide6.QtCore import QObject, QThread

    from wick.gui import resilience

    shown = []
    monkeypatch.setattr("wick.gui.dialogs.warn", lambda _p, title, info="": shown.append((title, info)))
    if resilience._bridge is None:
        resilience._bridge = resilience._Bridge()
        resilience._bridge.error.connect(resilience._show_background_error)
    resilience._bridge.error.emit("ValueError", "bad file")
    assert _wait(lambda: shown, 5) and shown[0][0] == "A background task stopped with an error"

    holder = QObject()

    class Polite(QThread):
        def __init__(self, parent):
            super().__init__(parent)
            self.cancel = threading.Event()

        def run(self):
            self.cancel.wait(20)

    class Stubborn(QThread):
        def run(self):
            time.sleep(1.5)

    polite, stubborn = Polite(holder), Stubborn(holder)
    polite.start()
    stubborn.start()
    left = resilience.stop_all(holder, deadline=0.3)
    assert not polite.isRunning() and polite.cancel.is_set()          # asked, and stopped in time
    assert left == [stubborn] and stubborn.parent() is None           # kept alive, not destroyed with its owner
    del holder
    stubborn.wait(5000)
    resilience._stuck.remove(stubborn)


def test_dialogs_share_one_frame_and_button_row(win):
    """Every dialog: a heading, the standard margins, and on the right the main action (primary, Enter) then
    Cancel (Esc), in Windows' order; extra actions on the left."""
    from PySide6.QtWidgets import QDialog, QPushButton

    from wick.gui.style import Space
    from wick.gui.widgets import button, dialog_buttons, dialog_layout

    d = QDialog(win)
    lay = dialog_layout(d, "Folders to Scan", "These folders are included.")
    extra = button("Add Folder…")
    row = dialog_buttons(d, ("Save Folders", d.accept), left=(extra,))
    lay.addLayout(row)
    assert lay.contentsMargins().left() == Space.XL
    labels = [row.itemAt(i).widget().text() for i in range(row.count()) if row.itemAt(i).widget()]
    assert labels == ["Add Folder…", "Save Folders", "Cancel"]
    go = d._primary_button
    assert go.objectName() == "primary" and go.isDefault()
    cancel = next(b for b in d.findChildren(QPushButton) if b.text() == "Cancel")
    cancel.click()
    assert d.result() == QDialog.Rejected


def test_the_running_tasks_button_follows_the_sidebar_width(win):
    """A task already running when the sidebar narrows: the button shortens to the count (and back), instead of
    keeping text that no longer fits."""
    from wick.activity import Activity

    side = win.sidebar
    side.set_compact(False)
    side.set_activity([Activity("k", "Scan", "Reading")])
    assert side.activity_btn.text() == "1 task running"
    side.set_compact(True)
    assert side.activity_btn.text() == "1"
    side.set_compact(False)
    assert side.activity_btn.text() == "1 task running"
    side.set_activity([])
    side.set_compact(True)
    assert side.activity_box.isHidden()


def test_update_notice_for_a_package_manager_install(win, monkeypatch):
    """Installed with Scoop: the notice gives the update command (copied on request), not the download page."""
    from PySide6.QtGui import QDesktopServices, QGuiApplication

    from wick import updates
    from wick.gui import dialogs

    asked = []
    opened = []
    monkeypatch.setattr(updates, "install_source", lambda exe=None: "scoop")
    monkeypatch.setattr(dialogs, "choose", lambda parent, title, info, cancel, ok: asked.append((info, ok)) or True)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url))
    win._release = updates.Release("9.9.9", updates.PAGE, False, "")
    win._show_update()
    assert asked and "scoop update wick" in asked[0][0] and asked[0][1] == "Copy Command"
    assert QGuiApplication.clipboard().text() == "scoop update wick" and not opened

    monkeypatch.setattr(updates, "install_source", lambda exe=None: "")
    asked.clear()
    win._show_update()
    assert asked[0][1] == "Open Download Page" and opened


def test_qt_warnings_reach_the_log_without_flooding_it(win, caplog):
    import logging

    from PySide6.QtCore import qWarning

    from wick.gui import resilience

    resilience._log_qt_messages()
    with caplog.at_level(logging.INFO, logger="wick"):
        for _ in range(6):
            qWarning("a plugin didn't load")
    got = [r for r in caplog.records if "a plugin didn't load" in r.getMessage()]
    assert len(got) == resilience.QT_REPEATS_LOGGED and got[0].levelno == logging.WARNING
    assert "further repeats" in got[-1].getMessage()


def test_the_crash_notice_offers_a_bug_report(win, monkeypatch):
    from wick.gui import app, dialogs

    asked, reported = [], []
    monkeypatch.setattr(dialogs, "choose", lambda parent, title, info, default, other: asked.append(other) or True)
    monkeypatch.setattr(dialogs, "report_problem", lambda parent: reported.append(parent))
    app._mention_crash(win)
    assert asked == ["Report a Problem"] and reported == [win]


def test_everything_else_is_explained_with_safe_actions(win, monkeypatch):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QPushButton

    from wick import systemspace
    from wick.gui import hidden_space

    items = [systemspace.Hidden("journal", "System logs", 3 * 1024 ** 3, "Logs.", systemspace.COMMAND,
                                "Copy Command", "sudo journalctl --vacuum-time=4weeks"),
             systemspace.Hidden("virtual_disks", "Linux (WSL) and Docker disks", 5 * 1024 ** 3, "Disks.",
                                systemspace.VIRTUAL_DISKS, "Compact on Clean Up"),
             systemspace.Hidden("swap", "Swap file", 2 * 1024 ** 3, "Leave it.")]
    monkeypatch.setattr(systemspace, "measure", lambda drive: items)
    monkeypatch.setattr(hidden_space, "inform", lambda *a, **k: None)
    opened = []
    d = hidden_space.HiddenSpaceDialog(Path("/"), opened.append, win)
    assert _wait(lambda: not d.worker.isRunning() and d.list.count() == 3, 10)
    QApplication.processEvents()
    buttons = {b.text(): b for b in d.findChildren(QPushButton)}
    assert "Copy Command" in buttons and "Compact on Clean Up" in buttons
    buttons["Copy Command"].click()
    assert QGuiApplication.clipboard().text() == "sudo journalctl --vacuum-time=4weeks"
    buttons["Compact on Clean Up"].click()
    assert opened == ["devclean"]
    win.open_cleanup_tool("apps")
    assert win.cleanup._tools[win.cleanup.tabs.currentIndex()].key == "apps"


def test_an_almost_full_drive_is_mentioned_once_a_day(win, monkeypatch):
    import shutil as _shutil
    from collections import namedtuple

    from wick.cleanup import actions, caches
    from wick.cleanup.common import Candidate, ScanResult
    from wick.gui.main_window import MainWindow

    gb = 1024 ** 3
    assert MainWindow.almost_full(100 * gb, 9 * gb) and not MainWindow.almost_full(100 * gb, 11 * gb)
    assert MainWindow.almost_full(2000 * gb, 19 * gb) and not MainWindow.almost_full(2000 * gb, 25 * gb)

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(_shutil, "disk_usage", lambda p: Usage(100 * gb, 95 * gb, 5 * gb))
    messages = []
    monkeypatch.setattr(win, "tray_message", lambda m, force=False, **k: messages.append(m))
    for key in win.qsettings.allKeys():
        if key.startswith("low_space"):
            win.qsettings.remove(key)
    win.qsettings.setValue("low_space", "notify")
    assert win.check_low_space() and "almost full" in messages[-1]
    assert not win.check_low_space()                                   # not again today

    for key in win.qsettings.allKeys():
        if key.startswith("low_space/last"):
            win.qsettings.remove(key)
    win.qsettings.setValue("low_space", "clean")
    safe = Candidate(Path("/tmp/cache"), 3 * gb, selected=True)
    unsure = Candidate(Path("/tmp/other"), gb, selected=False)
    monkeypatch.setattr(caches, "find_caches", lambda *a, **k: ScanResult("caches", [safe, unsure]))
    applied = []
    monkeypatch.setattr(actions, "apply", lambda items, *a, **k: (applied.append(items) or ["x"], None))
    monkeypatch.setattr(actions, "empty_from_trash", lambda outcomes, *a, **k: (1, 3 * gb, []))
    messages.clear()
    assert win.check_low_space()
    assert _wait(lambda: bool(messages), 10)
    assert applied == [[safe]] and "cleared 3.0 GB" in messages[-1]
    win.qsettings.setValue("low_space", "notify")


def test_the_command_bar_finds_pages_tools_and_actions(win, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from wick.gui.command_bar import CommandBar, matches

    commands = win.commands()
    titles = [c.title for c in matches(commands, "dup")]
    assert titles and titles[0] == "Duplicates"
    assert [c.title for c in matches(commands, "hibernation")] == ["What Else Takes Space"]
    assert not matches(commands, "zzzz nothing")
    # Only tools that can run on this system are offered.
    from wick.gui.cleanup_tools import tools

    offered = {c.title for c in commands if c.where == "Clean Up"}
    assert all((t.tab in offered) == (not t.available()) for t in tools())
    bar = CommandBar(commands, win)
    bar.field.setText("large files")
    QApplication.processEvents()
    assert bar.list.currentItem().data(Qt.UserRole).title == "Large Files"
    bar._run_current()
    assert win.pages.currentWidget() is win.cleanup
    assert win.cleanup._tools[win.cleanup.tabs.currentIndex()].key == "large"
    bar.field.setText("history")
    bar._run_current()
    assert win.pages.currentWidget() is win.history


def test_speed_or_size_is_asked_once_and_sets_the_defaults(win, monkeypatch):
    from wick import tradeoff
    from wick.gui import tradeoff_dialog

    assert tradeoff.suggest(16, 32)[0] == "smaller" and tradeoff.suggest(4, 16)[0] == "faster"
    assert tradeoff.suggest(8, 16)[0] == "balanced" and "8 processor threads" in tradeoff.suggest(8, 16)[1]
    d = tradeoff_dialog.TradeoffDialog(win, suggested=("smaller", "Many cores."))
    assert d.choice() == "smaller"
    radios = {b.property("choice"): b for b in d.group.buttons()}
    radios["faster"].setChecked(True)
    assert d.choice() == "faster"

    shown = []

    class Fake:
        def __init__(self, parent):
            self.finished = type("S", (), {"connect": lambda _s, f: shown.append(f)})()

        def choice(self):
            return "smaller"

        def open(self):
            shown[-1](1)

    monkeypatch.setattr(tradeoff_dialog, "TradeoffDialog", Fake)
    win.qsettings.setValue("tradeoff/asked", False)
    win.ask_tradeoff()
    s = win.compress.panel.settings()
    assert s.image_effort == 9 and s.video_effort == "slow" and tradeoff.choice_of(s) == "smaller"
    assert win.qsettings.value("tradeoff/asked", False, bool)
    shown.clear()
    win.ask_tradeoff()                                       # once only
    assert not shown
    win.compress.panel.load(tradeoff.apply("balanced", win.compress.panel.settings()))


def test_page_titles_line_up_with_or_without_header_buttons(win):
    from wick.gui.style import Size

    for page in (win.home, win.app_settings, win.cleanup, win.history):
        assert page.title.minimumHeight() >= Size.CONTROL
