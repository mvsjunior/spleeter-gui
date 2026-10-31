"""A titled section that opens and closes."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton, QVBoxLayout, QWidget


class Collapsible(QWidget):
    def __init__(self, title: str, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._content = content
        self._button = QToolButton()
        self._button.setText(title)
        self._button.setCheckable(True)
        self._button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._button.setAutoRaise(True)
        self._button.toggled.connect(self.set_open)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._button)
        layout.addWidget(content)
        self.set_open(False)

    @property
    def is_open(self) -> bool:
        return self._button.isChecked()

    def set_open(self, is_open: bool) -> None:
        self._button.setChecked(is_open)
        arrow = Qt.ArrowType.DownArrow if is_open else Qt.ArrowType.RightArrow
        self._button.setArrowType(arrow)
        self._content.setVisible(is_open)
