"""Pixel loupe for the frozen screenshot under the capture pointer."""

from math import floor

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget


_COLUMNS = 17
_ROWS = 13
_CELL = 7
_MARGIN = 9
_COLOR_INSPECTOR = QColor("#1c343c")
_CENTER = QColor("#56e2ca")


def source_pixel(frame: QImage, viewport: QSize, point: QPointF) -> QPoint:
    x = floor(point.x() * frame.width() / viewport.width() + 1e-6)
    y = floor(point.y() * frame.height() / viewport.height() + 1e-6)
    return QPoint(min(max(x, 0), frame.width() - 1), min(max(y, 0), frame.height() - 1))


def _position(point: QPoint, size: QSize, viewport: QSize, obstacles: tuple[QRect, ...]) -> QPoint:
    offset = 18
    candidates = (
        QPoint(point.x() + offset, point.y() + offset),
        QPoint(point.x() - offset - size.width(), point.y() + offset),
        QPoint(point.x() + offset, point.y() - offset - size.height()),
        QPoint(point.x() - offset - size.width(), point.y() - offset - size.height()),
    )
    limits = QRect(QPoint(), viewport).adjusted(3, 3, -3, -3)
    for candidate in candidates:
        footprint = QRect(candidate, size)
        if limits.contains(footprint) and not any(footprint.intersects(rect) for rect in obstacles):
            return candidate
    for candidate in candidates:
        if limits.contains(QRect(candidate, size)):
            return candidate
    return QPoint(
        max(0, min(candidates[0].x(), viewport.width() - size.width())),
        max(0, min(candidates[0].y(), viewport.height() - size.height())),
    )


class PixelMagnifier(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("pixelMagnifier")
        self.setAccessibleName("截图像素放大器")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(_COLUMNS * _CELL + _MARGIN * 2, _ROWS * _CELL + 69)
        self._source = QImage()
        self._source_key: int | None = None
        self._pixel = QPoint()
        self._selection = QRect()
        self._color = QColor()
        self._hex = False
        self._pending: tuple[QPointF, QSize, QRect, tuple[QRect, ...]] | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(8)
        self._timer.timeout.connect(self._refresh)
        self.hide()

    def inspect(
        self, frame: QImage, viewport: QSize, point: QPointF,
        selection: QRect, obstacles: tuple[QRect, ...],
    ) -> None:
        key = frame.cacheKey()
        if key != self._source_key:
            self._source_key = key
            self._source = frame
            self._color = QColor()
        self._pending = point, viewport, selection, obstacles
        if not self.isVisible():
            self._refresh()
            self.raise_()
            self.show()
        elif not self._timer.isActive():
            self._timer.start()

    def _refresh(self) -> None:
        if self._pending is None:
            return
        point, viewport, selection, obstacles = self._pending
        self._pending = None
        pixel = source_pixel(self._source, viewport, point)
        if pixel != self._pixel or selection != self._selection or not self._color.isValid():
            self._pixel = pixel
            self._selection = selection
            self._color = self._source.pixelColor(pixel)
            self.update()
        position = _position(point.toPoint(), self.size(), viewport, obstacles)
        if position != self.pos():
            self.move(position)

    def dismiss(self, *, clear: bool = False) -> None:
        self._timer.stop()
        self._pending = None
        self.hide()
        if clear:
            self._source = QImage()
            self._source_key = None
            self._color = QColor()

    def toggle_format(self) -> None:
        self._hex = not self._hex
        self.update()

    def value(self) -> str:
        self._refresh()
        if not self._color.isValid():
            return ""
        if self._hex:
            return self._color.name().upper()
        return f"{self._color.red()}, {self._color.green()}, {self._color.blue()}"

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self._color.isValid():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#68979c"), 1))
        painter.setBrush(_COLOR_INSPECTOR)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        grid = QRect(_MARGIN, _MARGIN, _COLUMNS * _CELL, _ROWS * _CELL)
        half = QPoint(_COLUMNS // 2, _ROWS // 2)
        sample = QRect(self._pixel - half, QSize(_COLUMNS, _ROWS))
        clipped = sample.intersected(self._source.rect())
        destination = QRect(grid.topLeft() + (clipped.topLeft() - sample.topLeft()) * _CELL, clipped.size() * _CELL)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.fillRect(grid, QColor("#13242b"))
        painter.drawImage(destination, self._source, clipped)

        painter.setPen(QColor(84, 132, 143, 85))
        for column in range(_COLUMNS + 1):
            x = grid.left() + column * _CELL
            painter.drawLine(x, grid.top(), x, grid.bottom())
        for row in range(_ROWS + 1):
            y = grid.top() + row * _CELL
            painter.drawLine(grid.left(), y, grid.right(), y)
        center = grid.topLeft() + half * _CELL
        painter.fillRect(QRect(grid.left(), center.y(), grid.width(), _CELL), QColor(55, 207, 181, 54))
        painter.fillRect(QRect(center.x(), grid.top(), _CELL, grid.height()), QColor(55, 207, 181, 54))
        if not self._selection.isEmpty():
            painter.save()
            painter.setClipRect(grid)
            position = grid.topLeft() + (self._selection.topLeft() - sample.topLeft()) * _CELL
            right = position.x() + self._selection.width() * _CELL
            bottom = position.y() + self._selection.height() * _CELL
            painter.setPen(QPen(_CENTER, 1.2, Qt.PenStyle.DashLine))
            top = max(position.y(), grid.top())
            end_y = min(bottom, grid.bottom())
            left = max(position.x(), grid.left())
            end_x = min(right, grid.right())
            if top < end_y:
                for x in position.x(), right:
                    if grid.left() <= x <= grid.right():
                        painter.drawLine(x, top, x, end_y)
            if left < end_x:
                for y in position.y(), bottom:
                    if grid.top() <= y <= grid.bottom():
                        painter.drawLine(left, y, end_x, y)
            painter.restore()
        center_cell = QRect(center, QSize(_CELL, _CELL))
        painter.fillRect(center_cell, self._color)
        painter.setPen(QPen(QColor("#15252a"), 3))
        painter.drawRect(center_cell.adjusted(-1, -1, 1, 1))
        painter.setPen(QPen(_CENTER, 1))
        painter.drawRect(center_cell.adjusted(-1, -1, 1, 1))

        mono = QFont("Cascadia Mono")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPixelSize(11)
        painter.setFont(mono)
        painter.setPen(QColor("#d6e8ea"))
        text_align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(QRect(_MARGIN, grid.bottom() + 6, grid.width(), 14), text_align,
                         f"({self._pixel.x()}, {self._pixel.y()})")
        swatch = QRect(_MARGIN, grid.bottom() + 24, 11, 11)
        painter.fillRect(swatch, self._color)
        painter.setPen(QColor("#a5bcc3"))
        painter.drawRect(swatch)
        painter.setPen(QColor("#f1f8f8"))
        if self._hex:
            color = self._color.name().upper()
        else:
            color = f"{self._color.red()}, {self._color.green()}, {self._color.blue()}"
        painter.drawText(QRect(swatch.right() + 7, swatch.top() - 1, grid.width() - 19, 14), text_align, color)

        hint = self.font()
        hint.setPixelSize(10)
        painter.setFont(hint)
        painter.setPen(QColor("#a8c4c8"))
        painter.drawText(QRect(_MARGIN, grid.bottom() + 43, grid.width(), 14), text_align, "C 复制 · Shift 切换")
