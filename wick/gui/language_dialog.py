"""Choose "my languages": the audio and subtitle tracks in these are kept when the others are left out."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem

from .. import languages
from .style import Size, Type
from .widgets import button, dialog_buttons, dialog_layout, text


class LanguageDialog(QDialog):
    def __init__(self, chosen: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("My Languages")
        self.resize(Size.INSPECTOR_W, Size.ROW * 12)  # (a starting size: a set minimum would override the layout's)
        lay = dialog_layout(self, "My Languages", "Audio and subtitle tracks in these languages are kept, along "
                            "with each video's main soundtrack and tracks with no language set.")
        detected = languages.detect()
        lay.addWidget(text("Found on this computer: " + languages.describe(detected) + " (from its display "
                           "languages and keyboard layouts).", Type.FOOTNOTE, secondary=True, wrap=True))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Find a language")
        self.filter.setAccessibleName("Find a language")
        self.filter.textChanged.connect(self._filter)
        lay.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setAccessibleName("Languages")
        lay.addWidget(self.list, 1)
        # Chosen and detected languages first, then the rest alphabetically.
        first = list(dict.fromkeys(chosen + detected))
        rest = sorted((k for k in languages.LANGUAGES if k not in first), key=languages.name)
        for key in first + rest:
            it = QListWidgetItem(languages.name(key))
            it.setData(Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if key in chosen else Qt.Unchecked)
            self.list.addItem(it)
        reset = button("Use This PC's Languages", "", None, "Choose the languages found on this computer")
        reset.clicked.connect(lambda: self._set(detected))
        lay.addLayout(dialog_buttons(self, ("Use These Languages", self._accept), left=(reset,)))
        self.detected = detected

    def _set(self, keys: list[str]) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in keys else Qt.Unchecked)

    def _filter(self, query: str) -> None:
        q = query.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def chosen(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _accept(self) -> None:
        if not self.chosen():
            self.filter.setFocus()
            self.filter.setPlaceholderText("Choose at least one language")
            return
        self.accept()


__all__ = ["LanguageDialog"]
