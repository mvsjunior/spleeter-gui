"""A stem's waveform, with the playback position. Clicking it asks to go to that point."""

import numpy as np
from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent, QPalette, QPixmap, QResizeEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

_CURSOR_WIDTH = 2


class WaveformView(QWidget):
    seek_requested = Signal(float)  # fraction of the song

    def __init__(self, peaks: np.ndarray, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._peaks = peaks
        self._pixmap: QPixmap | None = None
        self._cursor_x = 0
        self._dimmed = False

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(300, 40)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(100, 24)

    def set_cursor(self, fraction: float) -> None:
        x = round(fraction * self.width())
        if x != self._cursor_x:
            # Only the cursor's two places are repainted: this runs many times a second.
            self.update(QRect(self._cursor_x, 0, _CURSOR_WIDTH, self.height()))
            self.update(QRect(x, 0, _CURSOR_WIDTH, self.height()))
            self._cursor_x = x

    def set_dimmed(self, dimmed: bool) -> None:
        if dimmed != self._dimmed:
            self._dimmed = dimmed
            self._pixmap = None
            self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        self._pixmap = None
        super().resizeEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.width() > 0:
            self.seek_requested.emit(min(max(event.position().x() / self.width(), 0.0), 1.0))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        if self._pixmap is None:
            self._pixmap = self._draw_wave()
        painter = QPainter(self)
        painter.drawPixmap(QPoint(0, 0), self._pixmap)
        highlight = self.palette().color(QPalette.ColorRole.Highlight)
        painter.fillRect(self._cursor_x, 0, _CURSOR_WIDTH, self.height(), highlight)

    def _draw_wave(self) -> QPixmap:
        pixmap = QPixmap(self.size())
        pixmap.fill(self.palette().color(QPalette.ColorRole.Base))
        width, height = self.width(), self.height()
        if width <= 0 or not len(self._peaks):
            return pixmap
        # The loudest bucket of each pixel, so that short hits do not vanish when narrow.
        edges = np.linspace(0, len(self._peaks), width + 1, dtype=np.int64)
        starts = np.minimum(edges[:-1], len(self._peaks) - 1)
        heights = np.maximum.reduceat(self._peaks, starts)
        role = QPalette.ColorRole.Mid if self._dimmed else QPalette.ColorRole.Text
        painter = QPainter(pixmap)
        painter.setPen(self.palette().color(role))
        middle = height // 2
        for x, peak in enumerate(heights.tolist()):
            half = round(min(peak, 1.0) * (middle - 1))
            painter.drawLine(x, middle - half, x, middle + half)
        painter.end()
        return pixmap
