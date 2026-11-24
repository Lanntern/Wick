"""What's in "Everything else": the space a drive's file browser doesn't explain (wick/systemspace.py), each item
with what it is and, where it's safe, what can be done about it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QFrame, QVBoxLayout

from .. import systemspace
from ..formats import human
from .dialogs import ask, inform
from .style import Space, Type
from .widgets import Card, button, dialog_buttons, dialog_layout, hbox, text


class _Measure(QThread):
    done = Signal(object)

    def __init__(self, drive: Path, parent=None):
        super().__init__(parent)
        self.drive = drive

    def run(self) -> None:
        try:
            self.done.emit(systemspace.measure(self.drive))
        except Exception:  # noqa: BLE001 - shown as "nothing found"
            from ..log import ignored
            ignored("gui.hidden_space._Measure")
            self.done.emit([])


class HiddenSpaceDialog(QDialog):
    """Lists the hidden space on ``drive``; ``open_tool(key)`` opens a Clean Up tool."""

    def __init__(self, drive: Path, open_tool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Everything else")
        self.drive, self.open_tool = drive, open_tool
        lay = dialog_layout(self, "What else takes space",
                            "Space used by Windows and apps outside your folders. Programs and Windows itself take "
                            "most of it." if systemspace.IS_WINDOWS else
                            "Space used by the system and apps outside your folders. Installed programs take most "
                            "of it.")
        self.list = QVBoxLayout()
        self.list.setSpacing(Space.S)
        self.status = text("Measuring…", Type.FOOTNOTE, secondary=True)
        self.list.addWidget(self.status)
        lay.addLayout(self.list)
        lay.addStretch(1)
        lay.addLayout(dialog_buttons(self, cancel="Close"))
        self.resize(560, 420)
        self._measure()

    def _measure(self) -> None:
        self.worker = _Measure(self.drive, self)
        self.worker.done.connect(self._show)
        self.worker.start()

    def _show(self, items) -> None:
        while self.list.count():
            w = self.list.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if not items:
            self.list.addWidget(text("Nothing large found outside your folders.", Type.BODY, secondary=True))
            return
        for h in items:
            card = Card(padding=Space.M, spacing=Space.XS)
            size = human(h.size) if h.size else "Size known to administrators only"
            head = hbox(text(h.title, Type.BODY, bold=True), None, text(size, Type.BODY, secondary=True))
            card.lay.addLayout(head)
            card.lay.addWidget(text(h.about, Type.FOOTNOTE, secondary=True, wrap=True))
            if h.action:
                act = button(h.action_label, "")
                act.clicked.connect(lambda _=False, item=h: self._act(item))
                card.lay.addLayout(hbox(act, None))
            card.setFrameShape(QFrame.NoFrame)
            self.list.addWidget(card)

    def _act(self, h: systemspace.Hidden) -> None:
        if h.action == systemspace.HIBERNATION:
            if not ask(self, "Turn off hibernation?",
                       f"This frees {human(h.size)}. The PC can't hibernate any more and Fast startup turns off, so "
                       "starting up may take a few seconds longer. Windows asks for administrator permission. You "
                       "can turn it back on with powercfg /hibernate on.", "Turn Off"):
                return
            if systemspace.turn_off_hibernation():
                inform(self, "Hibernation is off", f"{human(h.size)} is free again.")
                self._measure()
            else:
                inform(self, "Hibernation is still on", "Windows didn't allow the change.")
        elif h.action == systemspace.RESTORE_POINTS:
            try:
                subprocess.Popen(["SystemPropertiesProtection.exe"])
            except OSError as e:
                inform(self, "System Protection didn't open", str(e))
        elif h.action == systemspace.VIRTUAL_DISKS:
            self.accept()
            self.open_tool("devclean")
        elif h.action == systemspace.COMMAND:
            from PySide6.QtGui import QGuiApplication

            QGuiApplication.clipboard().setText(h.command)
            inform(self, "Command copied",
                   f"Paste it into a terminal to run it:\n\n{h.command}" + (
                       "\n\nIt asks for your password, because it changes system files." if "sudo" in h.command
                       else ""))


def show(drive: str, open_tool, parent=None) -> None:
    d = HiddenSpaceDialog(Path(drive if sys.platform != "win32" or drive.endswith("\\") else drive + "\\"), open_tool,
                          parent)
    d.exec()


__all__ = ["HiddenSpaceDialog", "show"]
