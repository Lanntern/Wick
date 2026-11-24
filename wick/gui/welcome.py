"""The first time Wick opens: what it does, what it never does, and a first look for savings."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from .. import APP_NAME
from .main_window import app_icon
from .settings_panel import BIN_NAME
from .style import Size, Space, Type, icon_pixmap, tokens
from .widgets import button, text

PROMISES = (
    ("lock", "Your files stay on this computer",
     "Wick doesn't need an account and never uploads your files. It only goes online to check for a newer version, "
     "weekly by default."),
    ("eye", "Nothing changes until you say so",
     "Scans only look. Every result is checked against its original before anything is replaced."),
    ("undo", "Everything can be undone",
     f"Originals go to the {BIN_NAME} and can be put back from the history."),
)


class WelcomeDialog(QDialog):
    """Accepted: look for savings now (Home's analysis). Rejected: not now."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Welcome to {APP_NAME}")
        self.setMinimumWidth(Space.XXL * 16)
        tk = tokens()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.XXL, Space.XXL, Space.XXL, Space.XL)
        lay.setSpacing(Space.L)

        logo = QLabel()

        logo.setPixmap(app_icon().pixmap(Size.ICON_L * 3 // 2, Size.ICON_L * 3 // 2))
        logo.setAccessibleName(f"{APP_NAME} icon")
        lay.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        title = text(f"Welcome to {APP_NAME}", Type.TITLE1, bold=True, wrap=True)   # (large text: two lines)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        intro = text("Makes your photos, videos, music and documents smaller without changing how they look or "
                     "sound, and clears out what can be rebuilt.", Type.BODY, secondary=True, wrap=True)
        intro.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(intro)
        lay.addSpacing(Space.S)

        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.L)
        grid.setVerticalSpacing(Space.L)
        badge = Size.ICON_M * 2
        for row, (name, head, body) in enumerate(PROMISES):
            mark = QLabel()
            mark.setFixedSize(badge, badge)
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mark.setPixmap(icon_pixmap(name, tk.accent_text, Size.ICON_M))
            mark.setStyleSheet(f"background: {tk.accent_soft}; border-radius: {badge // 2}px;")
            mark.setAccessibleName(head)
            grid.addWidget(mark, row, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(Space.XXS)
            col.addWidget(text(head, Type.HEADLINE, bold=True))
            col.addWidget(text(body, Type.BODY, secondary=True, wrap=True))
            grid.addLayout(col, row, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        lay.addSpacing(Space.M)

        buttons = QHBoxLayout()
        buttons.setSpacing(Space.S)
        buttons.addStretch(1)
        later = button("Not Now", "", None, "Start using Wick; Home can look for savings any time")
        later.clicked.connect(self.reject)
        go = button("Scan", "primary", None, "Look through your folders for files that could be smaller")
        go.clicked.connect(self.accept)
        go.setDefault(True)
        buttons.addWidget(go)
        buttons.addWidget(later)
        lay.addLayout(buttons)
        go.setFocus()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        """Exactly as tall as its text needs at its width (wrapped lines are only known once it's shown)."""
        self.resize(self.width(), self.layout().totalHeightForWidth(self.width()))


__all__ = ["WelcomeDialog", "PROMISES"]
