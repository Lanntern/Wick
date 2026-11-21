"""The first-start question: smaller files, or faster? (wick/tradeoff.py)"""

from __future__ import annotations

from PySide6.QtWidgets import QButtonGroup, QDialog, QRadioButton

from .. import tradeoff
from .style import Space, Type
from .widgets import dialog_buttons, dialog_layout, text


class TradeoffDialog(QDialog):
    """``choice()`` after it closes: the answer (the suggestion if it was closed without one)."""

    def __init__(self, parent=None, suggested: tuple[str, str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Smaller files, or faster?")
        self.suggested, why = suggested or tradeoff.suggest()
        lay = dialog_layout(self, "Smaller files, or faster?",
                            "Wick can work harder to make files a little smaller. You can change this later in the "
                            "compression options.")
        self.group = QButtonGroup(self)
        for c in tradeoff.CHOICES:
            radio = QRadioButton(c.title)
            radio.setAccessibleDescription(c.about)
            radio.setProperty("choice", c.key)
            self.group.addButton(radio)
            lay.addWidget(radio)
            about = c.about + (f" Suggested: {why}" if c.key == self.suggested else "")
            note = text(about, Type.FOOTNOTE, secondary=True, wrap=True)
            note.setContentsMargins(Space.XL, 0, 0, Space.XS)
            lay.addWidget(note)
            radio.setChecked(c.key == self.suggested)
        lay.addLayout(dialog_buttons(self, primary=("Use This", self.accept), cancel=None))
        self.setMinimumWidth(Space.XXL * 13)

    def choice(self) -> str:
        checked = self.group.checkedButton()
        return str(checked.property("choice")) if checked is not None else self.suggested


__all__ = ["TradeoffDialog"]
