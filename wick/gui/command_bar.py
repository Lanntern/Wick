"""Ctrl+K: type to go to any page or Clean Up tool, or run a common action."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from .style import Space, Type
from .widgets import text


@dataclass
class Command:
    title: str
    where: str                       # shown beside it: "Page", "Clean Up", "Action"
    run: Callable[[], None]
    words: str = ""                  # other words it's found by


def matches(commands: list[Command], query: str) -> list[Command]:
    """Commands whose title or words contain every word typed, the ones starting with it first."""
    words = query.lower().split()
    if not words:
        return list(commands)
    hits = [c for c in commands if all(w in f"{c.title} {c.where} {c.words}".lower() for w in words)]
    return sorted(hits, key=lambda c: (not c.title.lower().startswith(words[0]), c.title.lower()))


class CommandBar(QDialog):
    def __init__(self, commands: list[Command], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Go to or run")
        self.setAccessibleName("Command bar")
        self.commands = commands
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
        lay.setSpacing(Space.S)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Type a page, tool or action")
        self.field.setAccessibleName("Search pages, tools and actions")
        self.list = QListWidget()
        self.list.setAccessibleName("Matching pages, tools and actions")
        self.empty = text("Nothing matches.", Type.FOOTNOTE, secondary=True)
        lay.addWidget(self.field)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.empty)
        self.field.textChanged.connect(self._filter)
        self.field.returnPressed.connect(self._run_current)
        self.list.itemActivated.connect(lambda _item: self._run_current())
        self.field.installEventFilter(self)
        self.resize(480, 380)
        self._filter("")

    def _filter(self, query: str) -> None:
        self.list.clear()
        for c in matches(self.commands, query):
            item = QListWidgetItem(f"{c.title}    ·  {c.where}")
            item.setData(Qt.UserRole, c)
            self.list.addItem(item)
        self.empty.setVisible(self.list.count() == 0)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, ev):  # noqa: N802 - arrow keys move through the list while typing
        from PySide6.QtCore import QEvent

        if obj is self.field and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Down, Qt.Key_Up):
            row = self.list.currentRow() + (1 if ev.key() == Qt.Key_Down else -1)
            if 0 <= row < self.list.count():
                self.list.setCurrentRow(row)
            return True
        return super().eventFilter(obj, ev)

    def _run_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        command: Command = item.data(Qt.UserRole)
        self.accept()
        command.run()


__all__ = ["Command", "CommandBar", "matches"]
