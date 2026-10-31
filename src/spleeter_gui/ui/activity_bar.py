"""A busy indicator for when there is no percentage to show."""

from PySide6.QtCore import QRectF, QSize, QTimer
from PySide6.QtGui import QHideEvent, QPainter, QPaintEvent, QPalette, QShowEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

# QProgressBar's busy animation alone took 12% of a thread on this CPU; this takes far less.
_INTERVAL_MS = 500
_STEPS = 10
_BLOCK_FRACTION = 0.2


class ActivityBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._step = 0
        self._timer = QTimer(self)
        self._timer.setInterval(_INTERVAL_MS)
        self._timer.timeout.connect(self._advance)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(100, 8)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Mid))
        width = self.width() * _BLOCK_FRACTION
        left = (self.width() - width) * self._step / (_STEPS - 1)
        block = QRectF(left, 0, width, self.height())
        painter.fillRect(block, self.palette().color(QPalette.ColorRole.Highlight))

    def _advance(self) -> None:
        self._step = (self._step + 1) % _STEPS
        self.update()
