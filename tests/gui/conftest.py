"""The window the GUI tests drive (headless on Linux): a fresh one for each test file, shared by its tests."""

import os
import time

import pytest

pytest.importorskip("PySide6")
if os.name != "nt":
    # Headless Linux/macOS CI. Windows runners have a desktop session, and Qt's offscreen
    # backend there lacks the system fonts (text measures far too wide).
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



def _wait(cond, timeout=60.0) -> bool:
    from PySide6.QtWidgets import QApplication

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            # A finished thread's last signals (its results) may still be queued: deliver them too.
            for _ in range(5):
                QApplication.processEvents()
                time.sleep(0.01)
            return True
        time.sleep(0.02)
    return cond()


def _widest(widget) -> str:
    """The chain of widest descendants, to say which control makes a page too wide."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea, QWidget

    chain = []
    while True:
        if isinstance(widget, QScrollArea) and widget.widget() is not None:
            widget = widget.widget()        # look at the content, not the scroll bars
        kids = [c for c in widget.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
                if not c.isWindow() and not c.isHidden()]
        if not kids:
            break
        widget = max(kids, key=lambda c: c.minimumSizeHint().width())
        name = widget.accessibleName() or widget.objectName() or getattr(widget, "text", lambda: "")()
        chain.append(f"{type(widget).__name__}({name!r}, {widget.minimumSizeHint().width()})")
    return " > ".join(chain)


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    ini = tmp_path_factory.mktemp("qsettings") / "settings.ini"
    os.environ["WICK_SETTINGS_FILE"] = str(ini)
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    qs = QSettings(str(ini), QSettings.IniFormat)
    qs.setValue("home/auto_analyze", False)  # don't scan the real Pictures folder
    qs.setValue("welcome_done", True)        # (the welcome has its own test)
    qs.setValue("tradeoff/asked", True)      # (so has the speed-or-size question)
    qs.sync()
    del qs

    from wick.gui.main_window import MainWindow
    from wick.gui.theme import apply_theme

    apply_theme("light")  # as the app does: sets the UI font and stylesheet, which drive the layout
    w = MainWindow()
    w.show()
    _wait(lambda: w.isVisible(), 5)
    yield w
    w._quitting = True
    w.close()
    app.processEvents()
    os.environ.pop("WICK_SETTINGS_FILE", None)


@pytest.fixture
def no_dialogs(monkeypatch):
    """Answer every message box with the default action instead of blocking."""
    shown = []
    import importlib
    import pkgutil

    import wick.gui as gui

    def fake_inform(_parent, text, info=""):
        shown.append(text)

    def fake_ask(_parent, text, info="", action="OK", destructive=False):
        shown.append(text)
        return True

    # Every page module that imported the helpers (not only the ones a test happens to use).
    mods = [importlib.import_module(f"wick.gui.{m.name}") for m in pkgutil.iter_modules(gui.__path__)]
    for mod in mods:
        for name, fn in (("inform", fake_inform), ("ask", fake_ask), ("warn", fake_inform)):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, fn)
    return shown
