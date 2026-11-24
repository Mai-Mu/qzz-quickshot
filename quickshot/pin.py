"""Always-on-top screenshot with directly selectable OCR text."""

from __future__ import annotations

import logging
from functools import cache
from math import hypot
from threading import Event

from PySide6.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRunnable,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QContextMenuEvent, QCursor, QImage, QKeySequence, QPainter, qRgba
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from .ocr import OcrError, OcrText, TextBox, TextLine, recognize
from .qr import decode_qr


logger = logging.getLogger(__name__)
_TEXT_HIGHLIGHT = QColor(18, 158, 170, 100)
_SHADOW_PADDING = 8
_SHADOW_PATCHES = ((0, 0), (0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1), (2, 2))


@cache
def _shadow_stamp() -> QImage:
    size = 2 * _SHADOW_PADDING + 1
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    for y in range(size):
        for x in range(size):
            dx = max(0.0, abs(x - _SHADOW_PADDING) - 0.5)
            dy = max(0.0, abs(y - _SHADOW_PADDING) - 0.5)
            fade = max(0.0, 1 - hypot(dx, dy) / 6)
            image.setPixel(x, y, qRgba(0, 0, 0, round(58 * fade * fade)))
    return image


def _draw_shadow(painter: QPainter, image_rect: QRectF) -> None:
    padding = _SHADOW_PADDING
    stamp = _shadow_stamp()
    source = (0, padding, padding + 1, stamp.width())
    target_x = (0, padding, image_rect.right(), image_rect.right() + padding)
    target_y = (0, padding, image_rect.bottom(), image_rect.bottom() + padding)
    for row, column in _SHADOW_PATCHES:
        source_rect = QRectF(
            source[column], source[row],
            source[column + 1] - source[column], source[row + 1] - source[row],
        )
        target_rect = QRectF(
            target_x[column], target_y[row],
            target_x[column + 1] - target_x[column], target_y[row + 1] - target_y[row],
        )
        painter.drawImage(target_rect, stamp, source_rect)


class _RecognitionSignals(QObject):
    ocr_finished = Signal(object, str)
    qr_finished = Signal(str)


class _RecognitionJob(QRunnable):
    def __init__(self, image: QImage, cancelled: Event):
        super().__init__()
        self.image = image
        self.cancelled = cancelled
        self.signals = _RecognitionSignals()

    def run(self) -> None:
        if self.cancelled.is_set():
            return
        try:
            qr_text = decode_qr(self.image)
        except Exception:
            logger.exception("贴图二维码识别失败")
        else:
            if qr_text and not self.cancelled.is_set():
                self.signals.qr_finished.emit(qr_text)

        if self.cancelled.is_set():
            return
        try:
            result = recognize(self.image, self.cancelled)
        except OcrError as error:
            result, message = OcrText(), str(error)
        except Exception:
            logger.exception("贴图文字识别失败")
            result, message = OcrText(), "无法识别贴图文字"
        else:
            message = ""
        if not self.cancelled.is_set():
            self.signals.ocr_finished.emit(result, message)


def _recognition_pool() -> QThreadPool:
    app = QApplication.instance()
    pool = app.findChild(QThreadPool, "pinRecognitionPool")
    if pool is None:
        pool = QThreadPool(app)
        pool.setObjectName("pinRecognitionPool")
        pool.setMaxThreadCount(2)
    return pool


def wait_for_recognition() -> None:
    pool = QApplication.instance().findChild(QThreadPool, "pinRecognitionPool")
    if pool is not None:
        pool.clear()
        pool.waitForDone()


class PinnedImage(QWidget):
    def __init__(self, image: QImage, geometry: QRect | None = None):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("pinnedImage")
        self.setWindowTitle("瞬截 · 贴图")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._image = QImage(image)
        self._image.setDevicePixelRatio(1.0)
        self._text = OcrText()
        self._recognition_status = "正在识别文字…"
        self._qr_text = ""
        self._cancelled = Event()
        self._recognition_started = False
        self._anchor = 0
        self._caret = 0
        self._gesture = ""
        self._press_position = QPoint()
        self._drag_offset = QPoint()
        self._moving_window = False

        location = geometry.center() if geometry is not None else QCursor.pos()
        screen = QApplication.screenAt(location) or QApplication.primaryScreen()
        if geometry is None:
            available = screen.availableGeometry()
            scale = min(
                1 / screen.devicePixelRatio(),
                0.84 * available.width() / max(1, self._image.width()),
                0.84 * available.height() / max(1, self._image.height()),
            )
            width = max(1, round(self._image.width() * scale))
            height = max(1, round(self._image.height() * scale))
            left = max(available.left(), min(location.x(), available.right() + 1 - width))
            top = max(available.top(), min(location.y(), available.bottom() + 1 - height))
            geometry = QRect(left, top, width, height)

        padding = _SHADOW_PADDING
        self._image_rect = QRectF(padding, padding, geometry.width(), geometry.height())
        window_geometry = geometry.adjusted(-padding, -padding, padding, padding)
        self.winId()
        self.windowHandle().setScreen(screen)
        self.setFixedSize(window_geometry.size())
        self.move(window_geometry.topLeft())

    def _start_recognition(self) -> None:
        if self._cancelled.is_set():
            return
        job = _RecognitionJob(self._image, self._cancelled)
        job.signals.ocr_finished.connect(self._recognition_finished, Qt.ConnectionType.QueuedConnection)
        job.signals.qr_finished.connect(self._qr_finished, Qt.ConnectionType.QueuedConnection)
        _recognition_pool().start(job)

    @Slot(object, str)
    def _recognition_finished(self, result: OcrText, error: str) -> None:
        self._text = result
        self._recognition_status = error or ("复制所选文字" if result.glyphs else "未识别到文字")
        if self._gesture == "":
            self._update_cursor(QPointF(self.mapFromGlobal(QCursor.pos())))

    @Slot(str)
    def _qr_finished(self, result: str) -> None:
        self._qr_text = result

    def closeEvent(self, event) -> None:
        self._cancelled.set()
        super().closeEvent(event)

    def _point_in_image(self, point: QPointF) -> QPointF:
        return QPointF(
            (point.x() - self._image_rect.left()) * self._image.width() / self._image_rect.width(),
            (point.y() - self._image_rect.top()) * self._image.height() / self._image_rect.height(),
        )

    def _line_at(self, point: QPointF) -> TextLine | None:
        x, y = point.x(), point.y()
        for line in self._text.lines:
            if not line.box.top <= y <= line.box.bottom:
                continue
            tolerance = min(5.0, (line.box.bottom - line.box.top) * 0.14)
            glyphs = self._text.glyphs[line.start:line.end]
            if any(glyph.box.left - tolerance <= x <= glyph.box.right + tolerance for glyph in glyphs):
                return line
        return None

    def _caret_on_line(self, line: TextLine, x: float) -> int:
        for index in range(line.start, line.end):
            box = self._text.glyphs[index].box
            if x < (box.left + box.right) / 2:
                return index
        return line.end

    def _update_selection(self, position: QPointF) -> None:
        point = self._point_in_image(position)
        x, y = point.x(), point.y()

        def distance(line: TextLine) -> tuple[float, float]:
            box = line.box
            dx = max(box.left - x, x - box.right, 0)
            dy = max(box.top - y, y - box.bottom, 0)
            return dy, dx

        closest = min(self._text.lines, key=distance)
        self._caret = self._caret_on_line(closest, x)
        self.update()

    def _selection(self) -> tuple[int, int]:
        return min(self._anchor, self._caret), max(self._anchor, self._caret)

    def _rectangle_on_screen(self, box: TextBox) -> QRectF:
        scale_x = self._image_rect.width() / self._image.width()
        scale_y = self._image_rect.height() / self._image.height()
        return QRectF(
            self._image_rect.left() + box.left * scale_x,
            self._image_rect.top() + box.top * scale_y,
            (box.right - box.left) * scale_x,
            (box.bottom - box.top) * scale_y,
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(event.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        _draw_shadow(painter, self._image_rect)
        painter.drawImage(self._image_rect, self._image)
        painter.setClipRect(self._image_rect)

        # Recognize after the first on-screen paint, including when a pin was previously rendered while hidden.
        if self.isVisible() and not self._recognition_started:
            self._recognition_started = True
            QTimer.singleShot(0, self, self._start_recognition)

        start, end = self._selection()
        if start == end:
            return
        for line in self._text.lines:
            selected_start, selected_end = max(line.start, start), min(line.end, end)
            if selected_start >= selected_end:
                continue
            selected = self._text.glyphs[selected_start:selected_end]
            bounds = TextBox(
                min(glyph.box.left for glyph in selected), line.box.top,
                max(glyph.box.right for glyph in selected), line.box.bottom,
            )
            painter.fillRect(self._rectangle_on_screen(bounds), _TEXT_HIGHLIGHT)

    def _update_cursor(self, position: QPointF) -> None:
        if not self._image_rect.contains(position):
            self.unsetCursor()
        elif self._line_at(self._point_in_image(position)) is not None:
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._image_rect.contains(event.position()):
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)

        alt_pressed = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        point = self._point_in_image(event.position())
        line = self._line_at(point)
        if line is not None and not alt_pressed:
            self._gesture = "select"
            self._anchor = self._caret_on_line(line, point.x())
            self._caret = self._anchor
        else:
            self._gesture = "move"
            self._press_position = event.globalPosition().toPoint()
            self._drag_offset = self._press_position - self.pos()
            self._moving_window = False
            if not alt_pressed:
                self._caret = self._anchor
        self.update()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._gesture == "select":
            self._update_selection(event.position())
        elif self._gesture == "move":
            position = event.globalPosition().toPoint()
            if not self._moving_window:
                if (position - self._press_position).manhattanLength() < QApplication.startDragDistance():
                    return
                self._moving_window = True
                if self.windowHandle().startSystemMove():
                    self._gesture = ""
                    return
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.move(position - self._drag_offset)
        else:
            self._update_cursor(event.position())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._gesture == "select":
                self._update_selection(event.position())
            self._gesture = ""
            self._update_cursor(event.position())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _copy_text(self) -> None:
        text = self._text.selected_text(*self._selection())
        if text:
            QApplication.clipboard().setText(text)

    def _copy_image(self) -> None:
        QApplication.clipboard().setImage(self._image)

    def _copy_qr(self) -> None:
        QApplication.clipboard().setText(self._qr_text)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            if self._anchor != self._caret:
                self._copy_text()
            else:
                self._copy_image()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        if event.reason() == QContextMenuEvent.Reason.Mouse and not self._image_rect.contains(QPointF(event.pos())):
            return super().contextMenuEvent(event)
        menu = QMenu(self)
        text_action = menu.addAction(self._recognition_status, self._copy_text)
        text_action.setEnabled(self._anchor != self._caret)
        menu.addAction("复制图片", self._copy_image)
        if self._qr_text:
            menu.addAction("复制二维码内容", self._copy_qr)
        menu.addSeparator()
        menu.addAction("关闭贴图", self.close)
        menu.exec(event.globalPos())
