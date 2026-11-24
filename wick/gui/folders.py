"""Explicit scan scopes, edited as full paths before committing changes."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
)

from .style import Size
from .widgets import button, dialog_buttons, dialog_layout, hbox


def last_browse_dir(qs) -> str:
    """Where Add Folder / Add Files last were, on any page (else the home folder)."""
    import os

    last = str(qs.value("browse/last_dir", "", str)) if qs is not None else ""
    return last if last and os.path.isdir(last) else str(Path.home())


def remember_browse_dir(qs, picked: str) -> None:
    import os

    if qs is not None:
        qs.setValue("browse/last_dir", picked if os.path.isdir(picked) else os.path.dirname(picked))


def summary(paths, label=str):
    paths = list(paths)
    if not paths:
        return "No folders chosen"
    return label(paths[0]) + (f" (+{len(paths) - 1} more)" if len(paths) > 1 else "")


class FolderManager(QDialog):
    def __init__(self, parent, paths, defaults, settings, label=str, note="", read_only=False):
        super().__init__(parent)
        self.settings, self.defaults, self.label = settings, defaults, label
        self.setWindowTitle("Manage Folders")
        self.resize(Size.INSPECTOR_W * 2, Size.ROW * 12)
        layout = dialog_layout(self, "Folders to Scan", note or "These folders are included in scans. Changes take "
                                                                "effect when you save them.")
        self.list = QListWidget()
        self.list.setAccessibleName("Folders included in scans")
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.list)
        add = button("Add Folder…", "", "folder")
        add.clicked.connect(self.add)
        remove = button("Remove Selected")
        remove.clicked.connect(self.remove)
        reset = button("Reset to Defaults")
        reset.clicked.connect(lambda: self.fill(self.defaults()))
        for control in (add, remove, reset):
            control.setEnabled(not read_only)
        layout.addLayout(hbox(add, remove, None, reset))
        layout.addLayout(dialog_buttons(self, None, "Close") if read_only else
                         dialog_buttons(self, ("Save Folders", self.accept)))
        self.fill(paths)

    def paths(self):
        return [Path(self.list.item(i).data(Qt.UserRole)) for i in range(self.list.count())]

    def fill(self, paths):
        self.list.clear()
        for path in dict.fromkeys(paths):
            item = QListWidgetItem(self.label(path))
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            self.list.addItem(item)

    def add(self):
        initial = str(self.settings.value("folders/last_location", str(Path.home()), str))
        selected = QFileDialog.getExistingDirectory(self, "Include a folder", initial)
        if selected:
            path = Path(selected)
            self.settings.setValue("folders/last_location", str(path))
            self.fill([*self.paths(), path])

    def remove(self):
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
