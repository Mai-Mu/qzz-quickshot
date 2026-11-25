"""Single-screen capture, selection controls, and QR recognition."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QIODevice,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRunnable,
    QSaveFile,
    QSize,
    QStandardPaths,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QCursor,
    QEnterEvent,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QImage,
    QInputMethodEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRegion,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .annotations import AnnotationLayer, AnnotationStyle, Mark, annotation_font
from .cursors import CROSSHAIR, CanvasCursors, Pointer, ToolPointer, is_canvas_cursor_message
from .magnifier import PixelMagnifier
from .qr import decode_qr
from .toolbar import CaptureToolbar


logger = logging.getLogger(__name__)

_INFO_STYLE = """
    QFrame#selectionInfo {
        background: #f8fafb;
        border: 1px solid #cdd9dd;
        border-radius: 8px;
    }
    QLabel#selectionSize {
        color: #223b43;
        padding: 1px 3px;
        font-size: 11px;
    }
"""
_SIDE_STYLE = """
    QFrame#captureSideTools, QLabel#captureQrStatus {
        background: #192b31; border: 1px solid #496c72; border-radius: 6px;
    }
    QToolButton#scanQr {
        background: transparent; border: 1px solid transparent; border-radius: 4px;
    }
    QToolButton#scanQr:hover, QToolButton#scanQr:focus {
        background: #2b4249; border-color: #65828a;
    }
    QLabel#captureQrStatus { color: #deeeee; padding: 4px 8px; }
"""

_RESIZE_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "w": Qt.CursorShape.SizeHorCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
}
_HANDLE_RADIUS = 3.5
_OUTLINE_PADDING = 7.0
_AREA_TOOLS = {"rectangle", "ellipse", "mosaic", "blur"}
_PATH_TOOLS = {"pen", "highlight", "eraser", "mosaic_brush"}
_BRUSH_CURSORS = {"pen": "pen", "eraser": "eraser", "mosaic_brush": "eraser"}
_ENDPOINT_TOOLS = {"arrow", "line"}
_TEXT_OUTLINE_PADDING = 3
_TEXT_OUTLINE_HIT = 3


@dataclass(slots=True)
class _MarkDrag:
    anchor: QPointF
    original: Mark
    current: Mark
    index: int | None = None
    handle: str | int | None = None


def _qr_icon() -> QIcon:
    image = QPixmap(40, 40)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor("#deeeee"), 1.55, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    path = QPainterPath()
    for x, y in ((3, 3), (12, 3), (3, 12)):
        painter.drawRoundedRect(QRectF(x, y, 5, 5), 0.6, 0.6)
    path.moveTo(12, 12)
    path.lineTo(12, 15)
    path.lineTo(15, 15)
    path.moveTo(17, 12)
    path.lineTo(17, 17)
    path.lineTo(13, 17)
    painter.drawPath(path)
    painter.end()
    image.setDevicePixelRatio(2)
    return QIcon(image)


def _handle_centers(rect: QRectF) -> tuple[tuple[str, QPointF], ...]:
    center = rect.center()
    return (
        ("nw", rect.topLeft()),
        ("n", QPointF(center.x(), rect.top())),
        ("ne", rect.topRight()),
        ("w", QPointF(rect.left(), center.y())),
        ("e", QPointF(rect.right(), center.y())),
        ("sw", rect.bottomLeft()),
        ("s", QPointF(center.x(), rect.bottom())),
        ("se", rect.bottomRight()),
    )


def _resized_selection(rect: QRectF, handle: str, delta: QPointF, viewport: QSize) -> QRectF:
    left, right = rect.left(), rect.right()
    top, bottom = rect.top(), rect.bottom()
    minimum_width = min(rect.width(), 8.0)
    minimum_height = min(rect.height(), 8.0)
    if "w" in handle:
        left = min(right - minimum_width, max(0.0, left + delta.x()))
    elif "e" in handle:
        right = max(left + minimum_width, min(viewport.width(), right + delta.x()))
    if "n" in handle:
        top = min(bottom - minimum_height, max(0.0, top + delta.y()))
    elif "s" in handle:
        bottom = max(top + minimum_height, min(viewport.height(), bottom + delta.y()))
    return QRectF(QPointF(left, top), QPointF(right, bottom))


def selection_pixels(selection: QRectF, logical_size: QSize, pixel_size: QSize) -> QRect:
    """Map local Qt coordinates to the original capture's pixel grid."""
    if selection.isEmpty() or logical_size.isEmpty() or pixel_size.isEmpty():
        return QRect()

    scale_x = pixel_size.width() / logical_size.width()
    scale_y = pixel_size.height() / logical_size.height()
    # A moved selection can land exactly on a physical edge after a fractional DPI conversion.
    left = math.floor(selection.left() * scale_x + 1e-9)
    top = math.floor(selection.top() * scale_y + 1e-9)
    right = math.ceil(selection.right() * scale_x - 1e-9)
    bottom = math.ceil(selection.bottom() * scale_y - 1e-9)
    return QRect(left, top, right - left, bottom - top).intersected(
        QRect(0, 0, pixel_size.width(), pixel_size.height())
    )


def _selection_outline(rect: QRectF) -> QRegion:
    if rect.isEmpty():
        return QRegion()
    pad = _OUTLINE_PADDING
    outer = QRegion(rect.adjusted(-pad, -pad, pad, pad).toAlignedRect())
    inner = rect.adjusted(pad, pad, -pad, -pad)
    return outer if inner.isEmpty() else outer.subtracted(QRegion(inner.toAlignedRect()))


class _QrSignals(QObject):
    finished = Signal(int, str, bool)


class _QrScan(QRunnable):
    def __init__(self, revision: int, image: QImage) -> None:
        super().__init__()
        self._revision = revision
        self._image = image
        self.signals = _QrSignals()

    def run(self) -> None:
        text = ""
        failed = False
        try:
            text = decode_qr(self._image)
        except Exception:
            failed = True
            logger.exception("二维码识别失败")
        try:
            self.signals.finished.emit(self._revision, text, failed)
        except RuntimeError:
            # Qt may have released Python signal objects during interpreter shutdown.
            return


class _InlineTextEditor(QLineEdit):
    composition_changed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.preedit = ""
        self._ink = QColor()
        self.setFrame(False)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignTop)
        self.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 0; }")
        # Align the standard Qt IME and caret with the canvas glyph origin.
        self.setTextMargins(-2, -1, 0, 0)

    def set_ink(self, ink: QColor) -> None:
        if ink != self._ink:
            self._ink = ink
            self._update_palette()

    def _update_palette(self) -> None:
        palette = self.palette()
        text = self._ink if self.preedit else QColor(Qt.GlobalColor.transparent)
        palette.setColor(QPalette.ColorRole.Text, text)
        palette.setColor(QPalette.ColorRole.HighlightedText, text)
        palette.setColor(QPalette.ColorRole.Highlight, QColor(21, 171, 181, 68))
        self.setPalette(palette)

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        was_composing = bool(self.preedit)
        self.preedit = event.preeditString()
        if bool(self.preedit) != was_composing:
            self._update_palette()
        super().inputMethodEvent(event)
        self.composition_changed.emit()

    def paintEvent(self, event: QPaintEvent) -> None:
        # The canvas paints committed input. Qt paints input-method composition and selection.
        super().paintEvent(event)
        if not self.preedit and self.hasFocus() and not self.hasSelectedText():
            caret = self.cursorRect()
            painter = QPainter(self)
            painter.setPen(QPen(QColor("#087e86"), 1.0))
            x = max(0, caret.center().x())
            painter.drawLine(x, caret.top(), x, caret.bottom())


class CaptureOverlay(QWidget):
    image_copied = Signal(QImage)
    image_pinned = Signal(QImage, QRect)
    image_saved = Signal(str, QSize, int)

    def __init__(self) -> None:
        super().__init__(
            None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setObjectName("captureOverlay")
        self.setWindowTitle("瞬截")
        self._cursors = CanvasCursors()
        self._cursors.apply_qt(self, CROSSHAIR)
        self._cursor_refresh_timer = QTimer(self)
        self._cursor_refresh_timer.setSingleShot(True)
        self._cursor_refresh_timer.timeout.connect(self._refresh_cursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._frame = QImage()
        self._dark_frame = QImage()
        self._dark_frame_key: int | None = None
        self._crop = QImage()
        self._origin: QPointF | None = None
        self._selection = QRectF()
        self._resize_handle: str | None = None
        self._resize_base = QRectF()
        self._move_base: QRect | None = None
        self._annotations = AnnotationLayer()
        self._tool = ""
        self._selected_mark: int | None = None
        self._mark_drag: _MarkDrag | None = None
        self._polyline_points: list[QPointF] = []
        self._polyline_cursor: QPointF | None = None
        self._text_anchor: QPointF | None = None
        self._text_index: int | None = None
        self._text_outline = QRectF()
        self._text_drag_offset: QPointF | None = None
        self._revision = 0
        self._qr_pool = QThreadPool(self)
        self._qr_pool.setMaxThreadCount(1)
        self._info_timer = QTimer(self)
        self._info_timer.setSingleShot(True)
        self._info_timer.setInterval(16)
        self._info_timer.timeout.connect(self._position_info)
        self._magnifier = PixelMagnifier(self)
        self._magnifier_hold = False

        self._toolbar = CaptureToolbar(self)
        self._toolbar.setObjectName("captureActions")
        self._toolbar.setCursor(Qt.CursorShape.ArrowCursor)
        self._toolbar.tool_changed.connect(self._set_tool)
        self._toolbar.options_changed.connect(self._change_options)
        self._toolbar.layout_changed.connect(self._position_controls)
        self._toolbar.undo_requested.connect(self._undo)
        self._toolbar.redo_requested.connect(self._redo)
        self._toolbar.close_requested.connect(self._finish)
        self._toolbar.pin_requested.connect(self._pin)
        self._toolbar.save_requested.connect(self._save)
        self._toolbar.copy_requested.connect(self._copy)
        self._toolbar.hide()

        self._info = QFrame(self)
        self._info.setObjectName("selectionInfo")
        self._info.setStyleSheet(_INFO_STYLE)
        self._info.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        info_layout = QHBoxLayout(self._info)
        info_layout.setContentsMargins(5, 2, 5, 2)
        info_layout.setSpacing(5)
        self._size_label = QLabel(self._info)
        self._size_label.setObjectName("selectionSize")
        self._size_label.setAccessibleName("选区像素尺寸")
        info_layout.addWidget(self._size_label)
        self._info.hide()

        self._side_tools = QFrame(self)
        self._side_tools.setObjectName("captureSideTools")
        self._side_tools.setCursor(Qt.CursorShape.ArrowCursor)
        self._side_tools.setStyleSheet(_SIDE_STYLE)
        side_layout = QVBoxLayout(self._side_tools)
        side_layout.setContentsMargins(4, 3, 4, 3)
        side_layout.setSpacing(2)
        self._qr_button = QToolButton(self._side_tools)
        self._qr_button.setObjectName("scanQr")
        self._qr_button.setIcon(_qr_icon())
        self._qr_button.setIconSize(QSize(17, 17))
        self._qr_button.setFixedSize(27, 25)
        self._qr_button.setAccessibleName("扫描二维码")
        self._qr_button.setToolTip("扫描选区二维码并复制内容")
        self._qr_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._qr_button.clicked.connect(self._scan_qr)
        side_layout.addWidget(self._qr_button)
        self._side_tools.hide()

        self._qr_status = QLabel(self)
        self._qr_status.setObjectName("captureQrStatus")
        self._qr_status.setStyleSheet(_SIDE_STYLE)
        self._qr_status.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._qr_status.hide()
        self._qr_status_timer = QTimer(self)
        self._qr_status_timer.setSingleShot(True)
        self._qr_status_timer.setInterval(1800)
        self._qr_status_timer.timeout.connect(self._qr_status.hide)

        self._text_editor = _InlineTextEditor(self)
        self._text_editor.setObjectName("annotationTextInput")
        self._text_editor.setAccessibleName("标注文字")
        self._text_editor.setMouseTracking(True)
        self._text_editor.textChanged.connect(self._update_text_editor)
        self._text_editor.composition_changed.connect(self._update_text_editor)
        self._text_editor.returnPressed.connect(self._accept_text)
        text_cancel = QShortcut(QKeySequence("Esc"), self._text_editor)
        text_cancel.setContext(Qt.ShortcutContext.WidgetShortcut)
        text_cancel.activated.connect(self._cancel_text)
        self._text_editor.hide()
        for control in self.findChildren(QWidget):
            control.installEventFilter(self)
        self._capture_shortcuts: list[QShortcut] = []
        for key, slot in (
            ("Return", self._on_enter), ("Enter", self._on_enter),
            ("P", self._pin), ("Ctrl+S", self._save), ("Esc", self._on_escape),
            ("Ctrl+Z", self._undo), ("Ctrl+Y", self._redo), ("Ctrl+Shift+Z", self._redo),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(slot)
            self._capture_shortcuts.append(shortcut)

    def start(self) -> bool:
        if self.isVisible():
            return False
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("未找到可用的显示器。")
        frame = screen.grabWindow(0).toImage()
        if frame.isNull():
            raise RuntimeError("无法捕获当前显示器的画面。")

        # Overlay positions are logical. The capture and exports use physical pixels.
        frame.setDevicePixelRatio(1.0)
        self._frame = frame
        self.winId()
        self.windowHandle().setScreen(screen)
        self.setWindowState(Qt.WindowState.WindowNoState)
        self.setGeometry(screen.geometry())
        self._cursors.apply_qt(self, CROSSHAIR)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._refresh_cursor()
        return True

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._schedule_cursor_refresh()

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self._update_cursor(event.position())
        self._update_magnifier(event.position())

    def leaveEvent(self, event: QEvent) -> None:
        self._magnifier.dismiss()
        super().leaveEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._schedule_cursor_refresh()
            else:
                self._magnifier_hold = False
                self._magnifier.dismiss()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
            self._right_click(QPointF(self.mapFromGlobal(event.globalPosition().toPoint())))
            return True
        if watched is self._text_editor and event.type() in {
            QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick,
            QEvent.Type.MouseMove,
        }:
            point = self.mapFromGlobal(event.globalPosition())
            if self._handle_text_drag(event, point):
                return True
            if event.type() == QEvent.Type.MouseMove:
                self._update_cursor(point)
        if event.type() == QEvent.Type.Enter:
            self._magnifier.dismiss()
            self._schedule_cursor_refresh()
        elif event.type() == QEvent.Type.Leave:
            self._schedule_cursor_refresh()
        return super().eventFilter(watched, event)

    def nativeEvent(self, event_type, message):
        if is_canvas_cursor_message(event_type, message) and self.isVisible():
            point = QPointF(self.mapFromGlobal(QCursor.pos()))
            target = self._pointer_at(point)
            if target is not None and self._cursors.apply_native(self, target[0]):
                return True, 1
        return super().nativeEvent(event_type, message)

    def _schedule_cursor_refresh(self) -> None:
        self._cursor_refresh_timer.start(0)

    @Slot()
    def _refresh_cursor(self) -> None:
        if self.isVisible():
            point = self.mapFromGlobal(QCursor.pos())
            if self.rect().contains(point):
                self._update_cursor(QPointF(point), refresh_native=True)
                self._update_magnifier(QPointF(point))

    def _update_magnifier(self, point: QPointF) -> None:
        location = point.toPoint()
        if (self._frame.isNull() or self._mark_drag is not None
                or (self._origin is None and not QRectF(self.rect()).contains(point))
                or self._text_editor.isVisible() or QApplication.activePopupWidget() or QApplication.activeModalWidget()
                or (self._move_base is not None and not self._magnifier_hold)
                or (self._origin is None and self.childAt(location) is not None)):
            self._magnifier.dismiss()
            return
        handle = self._resize_handle or self._handle_at(point)
        if self._origin is None and not self._crop.isNull() and not self._magnifier_hold and handle is None:
            self._magnifier.dismiss()
            return
        controls = self._toolbar, self._info, self._side_tools
        obstacles = tuple(widget.geometry() for widget in controls if widget.isVisible())
        crop = self._physical_crop()
        sample = None if crop.isEmpty() else self._magnifier_corner(point, crop, handle or "")
        self._magnifier.inspect(self._frame, self.size(), point, crop, obstacles, sample=sample)

    def _magnifier_corner(self, point: QPointF, crop: QRect, handle: str) -> QPoint:
        center = self._selection.center()
        if self._origin is not None:
            if self._resize_handle is not None:
                # For edge handles, keep the same corner throughout the resize.
                point = self._origin
            elif self._move_base is None:
                # For a new selection, follow the corner opposite the drag origin.
                point = center
                center = self._origin
        east = "e" in handle or ("w" not in handle and point.x() >= center.x())
        south = "s" in handle or ("n" not in handle and point.y() >= center.y())
        # QRect's bottom and right edges are inclusive and identify the last pixels in the crop.
        return QPoint(crop.right() if east else crop.left(), crop.bottom() if south else crop.top())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if isinstance(QApplication.focusWidget(), QLineEdit):
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Alt and not event.isAutoRepeat():
            self._magnifier_hold = True
            self._update_magnifier(QPointF(self.mapFromGlobal(QCursor.pos())))
            event.accept()
            return
        if self._magnifier.isVisible():
            if key == Qt.Key.Key_Shift and not event.isAutoRepeat():
                self._magnifier.toggle_format()
                event.accept()
                return
            modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
            if key == Qt.Key.Key_C and not (event.modifiers() & modifiers):
                value = self._magnifier.value()
                if value:
                    QApplication.clipboard().setText(value)
                    event.accept()
                    return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Alt and not event.isAutoRepeat():
            self._magnifier_hold = False
            self._update_magnifier(QPointF(self.mapFromGlobal(QCursor.pos())))
            event.accept()
            return
        super().keyReleaseEvent(event)


    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._right_click(event.position())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if self._handle_text_drag(event, event.position()):
            return

        self._commit_text()
        point = self._clamp(event.position())
        handle = self._handle_at(point)
        if handle is not None:
            self._finish_polyline()
            self._resize_handle = handle
            self._resize_base = QRectF(self._selection)
            self._begin_selection_edit(event.position())
            return

        if self._tool:
            self._magnifier.dismiss()
            if self._selection.contains(point):
                self._begin_mark(point)
                self._update_cursor(point)
            else:
                self._set_tool("")
                self._toolbar.reset()
                self._update_annotation_view()
            return

        if not self._crop.isNull() and self._selection.contains(point):
            self._move_base = self._physical_crop()
            self._begin_selection_edit(event.position())
            return

        previous = self._selection
        self._reset_selection()
        self._origin = point
        self.setFocus()
        self._update_cursor(point)
        self._update_magnifier(point)
        self._invalidate_selection_change(previous)

    def _begin_selection_edit(self, origin: QPointF) -> None:
        self._origin = origin
        self._invalidate_qr()
        self._crop = QImage()
        self._toolbar.hide()
        self._side_tools.hide()
        self.setFocus()
        self._update_cursor(origin)
        self._update_magnifier(origin)

    def _right_click(self, point: QPointF) -> None:
        if self._selection.isEmpty() and self._origin is None:
            self._finish()
            return
        previous = self._selection
        self._reset_selection()
        self._invalidate_selection_change(previous)
        self.setFocus()
        self._update_cursor(point)
        self._update_magnifier(point)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        point = event.position()
        if self._handle_text_drag(event, point):
            return
        if event.button() == Qt.MouseButton.LeftButton and self._selection.contains(point):
            if self._tool == "polyline" and self._polyline_points:
                self._append_polyline(point)
                self._finish_polyline()
                return
            if self._tool == "text":
                index = self._annotations.hit_test(point, "text")
                if index is not None:
                    self._mark_drag = None
                    self._select_mark(index)
                    self._start_text(self._annotations.get(index).points[0], index)
                    return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = event.position()
        if self._handle_text_drag(event, point):
            return
        if self._origin is not None:
            self._update_selection(point)
        elif self._mark_drag is not None:
            self._update_mark_preview(self._annotation_point(point))
        elif self._polyline_points:
            previous, _ = self._render_state()
            self._polyline_cursor = self._annotation_point(point)
            current, _ = self._render_state()
            self._invalidate_marks(previous, current)
        self._update_cursor(point)
        self._update_magnifier(point)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        point = event.position()
        if self._handle_text_drag(event, point):
            return
        if self._origin is not None:
            self._update_selection(point)
            self._origin = None
            self._resize_handle = None
            self._move_base = None
            self._commit_selection()
        elif self._mark_drag is not None:
            self._update_mark(self._annotation_point(point))
            self._finish_mark()
        self._update_cursor(point)
        self._schedule_cursor_refresh()
        self._update_magnifier(point)

    def _handle_at(self, point: QPointF) -> str | None:
        if self._crop.isNull():
            return None
        handle, center = min(
            self._visible_handles(), key=lambda entry: (entry[1] - point).manhattanLength()
        )
        return handle if (center - point).manhattanLength() <= 11 else None

    def _visible_handles(self) -> tuple[tuple[str, QPointF], ...]:
        inset = _HANDLE_RADIUS + 1
        return tuple(
            (
                handle,
                QPointF(
                    min(self.width() - inset, max(inset, center.x())),
                    min(self.height() - inset, max(inset, center.y())),
                ),
            )
            for handle, center in _handle_centers(self._selection)
        )

    def _update_selection(self, point: QPointF) -> None:
        previous = self._selection
        if self._move_base is not None:
            self._move_selection(point - self._origin)
        elif self._resize_handle is None:
            self._selection = QRectF(self._origin, self._clamp(point)).normalized()
        else:
            self._selection = _resized_selection(
                self._resize_base, self._resize_handle, point - self._origin, self.size()
            )
        if previous != self._selection:
            self._invalidate_selection_change(
                previous, marks_moved=self._move_base is not None and self._annotations.has_annotations()
            )
            if not self._info_timer.isActive():
                self._info_timer.start()

    def _move_selection(self, distance: QPointF) -> None:
        base = self._move_base
        scale_x = self._frame.width() / self.width()
        scale_y = self._frame.height() / self.height()
        left = max(0, min(base.x() + round(distance.x() * scale_x), self._frame.width() - base.width()))
        top = max(0, min(base.y() + round(distance.y() * scale_y), self._frame.height() - base.height()))
        if self._physical_crop().topLeft() == QPoint(left, top):
            return
        # Move in source pixels so that rounded widths stay stable at fractional DPI.
        moved = QRectF(left / scale_x, top / scale_y, base.width() / scale_x, base.height() / scale_y)
        self._annotations.translate(moved.topLeft() - self._selection.topLeft())
        self._selection = moved

    def _invalidate_selection_change(self, previous: QRectF, *, marks_moved: bool = False) -> None:
        if previous == self._selection:
            return
        before = QRegion(previous.toAlignedRect())
        after = QRegion(self._selection.toAlignedRect())
        dirty = before.united(after) if marks_moved else before.xored(after)
        dirty = dirty.united(_selection_outline(previous)).united(_selection_outline(self._selection))
        self.update(dirty)

    def _physical_crop(self) -> QRect:
        return selection_pixels(self._selection, self.size(), self._frame.size())

    def _commit_selection(self) -> None:
        self._info_timer.stop()
        crop_rect = self._physical_crop()
        if not crop_rect.isEmpty():
            self._crop = self._frame.copy(crop_rect)
            self._position_controls()
            self._toolbar.show()
            self._side_tools.show()
        else:
            self._info.hide()
        self.update(_selection_outline(self._selection))

    def _pointer_at(self, point: QPointF) -> tuple[Pointer, bool] | None:
        location = point.toPoint()
        if not self.rect().contains(location) or QApplication.activePopupWidget() or QApplication.activeModalWidget():
            return None
        if self._text_drag_offset is not None:
            return Qt.CursorShape.SizeAllCursor, False
        if self._origin is None and self._mark_drag is None:
            control = self.childAt(location)
            if control is self._text_editor:
                cursor = Qt.CursorShape.SizeAllCursor if self._over_text_outline(point) else Qt.CursorShape.IBeamCursor
                return cursor, True
            if control is not None:
                # Qt child widgets share our HWND, so WM_SETCURSOR also needs their cursor.
                return control.cursor().shape(), True
        return self._canvas_pointer(point), False

    def _canvas_pointer(self, point: QPointF) -> Pointer:
        if self._over_text_outline(point):
            return Qt.CursorShape.SizeAllCursor
        handle = self._resize_handle or self._handle_at(point)
        if handle is not None:
            return _RESIZE_CURSORS[handle]
        if self._move_base is not None or (
            self._origin is None and not self._tool and not self._crop.isNull() and self._selection.contains(point)
        ):
            return Qt.CursorShape.SizeAllCursor
        drag = self._mark_drag
        if self._tool in _BRUSH_CURSORS and (drag is not None or self._selection.contains(point)):
            mark = drag.current if drag is not None else self._current_selected_mark()
            style = mark.style if mark is not None and mark.kind == self._tool else self._toolbar.style_for(self._tool)
            return ToolPointer(_BRUSH_CURSORS[self._tool], style.width, style.color if self._tool == "pen" else "")
        if drag is not None:
            if isinstance(drag.handle, str):
                return _RESIZE_CURSORS[drag.handle]
            if drag.index is not None and drag.handle is None:
                return Qt.CursorShape.SizeAllCursor
        elif self._origin is None:
            mark_handle = self._mark_handle_at(point)
            if isinstance(mark_handle, str):
                return _RESIZE_CURSORS[mark_handle]
            if self._tool == "text" and self._selection.contains(point):
                return Qt.CursorShape.IBeamCursor
        return CROSSHAIR

    def _update_cursor(self, point: QPointF, *, refresh_native: bool = False) -> None:
        target = self._pointer_at(point)
        if target is None:
            return
        pointer, over_control = target
        if not over_control:
            self._cursors.apply_qt(self, pointer)
        if self._text_editor.isVisible() and self._text_editor.cursor().shape() != pointer and (
            self._text_drag_offset is not None or self.childAt(point.toPoint()) is self._text_editor
        ):
            self._text_editor.setCursor(pointer)
        if (refresh_native or over_control or self._origin is not None or self._mark_drag is not None
                or self._text_drag_offset is not None) and self._cursors.owns_pointer(
            self, include_hover=refresh_native or over_control,
        ):
            self._cursors.apply_native(self, pointer)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        if self._frame.isNull():
            painter.fillRect(event.rect(), Qt.GlobalColor.black)
            return

        bounds = QRectF(self.rect())
        selection = self._selection
        dirty = QRectF(event.rect())
        source_bounds = QRectF(self._frame.rect())
        # Qt clips both blits to the widget's dirty paint region.
        if not selection.contains(dirty):
            painter.drawImage(bounds, self._dark_snapshot(), source_bounds)
        if selection.isEmpty():
            return

        if selection.intersects(dirty):
            preview, replacement = self._render_state()
            painter.save()
            painter.setClipRect(selection, Qt.ClipOperation.IntersectClip)
            painter.drawImage(bounds, self._frame, source_bounds)
            self._annotations.draw(
                painter, self._frame, selection, self.size(),
                preview=preview, replacement=replacement,
            )
            self._paint_mark_handles(painter)
            painter.restore()
        pad = _OUTLINE_PADDING
        if not selection.adjusted(pad, pad, -pad, -pad).contains(dirty):
            painter.setPen(QPen(QColor("#15abb5"), 1.0))
            painter.drawRect(selection)
            if self._origin is None or self._resize_handle is not None or self._move_base is not None:
                self._paint_capture_handles(painter)

    def _dark_snapshot(self) -> QImage:
        key = self._frame.cacheKey()
        if self._dark_frame_key != key:
            self._dark_frame = QImage(self._frame)
            painter = QPainter(self._dark_frame)
            painter.fillRect(self._dark_frame.rect(), QColor(0, 0, 0, 120))
            painter.end()
            self._dark_frame_key = key
        return self._dark_frame

    def _paint_capture_handles(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#07565e"), 1.0))
        painter.setBrush(QColor("#dbffff"))
        radius = _HANDLE_RADIUS
        for _, center in self._visible_handles():
            bounds = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
            painter.drawRoundedRect(bounds, 1.25, 1.25)

    def _paint_mark_handles(self, painter: QPainter) -> None:
        if self._text_anchor is not None:
            painter.setPen(QPen(QColor("#087e86"), 1.0, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._text_outline)
            return
        mark = self._current_selected_mark()
        if mark is None or mark.kind == "text":
            return
        handles = self._mark_handles(mark)
        if not handles:
            bounds = self._mark_bounds(mark).adjusted(-3, -3, 3, 3)
            painter.setPen(QPen(QColor("#087e86"), 1.0, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(bounds)
            return
        painter.setBrush(QColor("#087e86"))
        painter.setPen(QPen(QColor("#ecfffd"), 1.1))
        for _, center in handles:
            painter.drawEllipse(center, 4.0, 4.0)

    def _current_selected_mark(self) -> Mark | None:
        if self._selected_mark is None:
            return None
        if self._mark_drag is not None and self._mark_drag.index == self._selected_mark:
            return self._mark_drag.current
        return self._annotations.get(self._selected_mark)

    @staticmethod
    def _mark_handles(mark: Mark) -> tuple[tuple[str | int, QPointF], ...]:
        if mark.kind in _AREA_TOOLS:
            return _handle_centers(QRectF(mark.points[0], mark.points[-1]).normalized())
        if mark.kind in _ENDPOINT_TOOLS or mark.kind == "polyline":
            return tuple(enumerate(mark.points))
        return ()

    @staticmethod
    def _mark_bounds(mark: Mark) -> QRectF:
        if mark.kind == "text":
            metrics = QFontMetricsF(annotation_font(mark.style))
            lines = mark.text.splitlines() or [""]
            return QRectF(
                mark.points[0].x(), mark.points[0].y(),
                max(8.0, *(metrics.horizontalAdvance(line) for line in lines)),
                metrics.height() + (len(lines) - 1) * metrics.lineSpacing(),
            )
        xs = [point.x() for point in mark.points]
        ys = [point.y() for point in mark.points]
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def _mark_handle_at(self, point: QPointF) -> str | int | None:
        mark = self._current_selected_mark()
        if mark is None or not self._selection.contains(point):
            return None
        handles = [
            (handle, (center - point).manhattanLength())
            for handle, center in self._mark_handles(mark) if self._selection.contains(center)
        ]
        if not handles:
            return None
        handle, distance = min(handles, key=lambda entry: entry[1])
        return handle if distance <= 9 else None


    def _begin_mark(self, point: QPointF) -> None:
        self._invalidate_qr()
        kind = self._tool
        if kind == "polyline" and self._polyline_points:
            self._append_polyline(point)
            return
        handle = self._mark_handle_at(point)
        index = self._selected_mark if handle is not None else None
        if index is None and kind not in {"eraser", "mosaic_brush"}:
            index = self._annotations.hit_test(point, kind)
        self._select_mark(index)
        if index is not None:
            mark = self._annotations.get(index)
            self._mark_drag = _MarkDrag(point, mark, mark, index, handle)
        elif kind == "text":
            self._start_text(point, None)
        elif kind == "polyline":
            self._append_polyline(point)
        else:
            points = (point,) if kind in _PATH_TOOLS else (point, point)
            mark = Mark(kind, points, self._toolbar.style_for(kind))
            self._mark_drag = _MarkDrag(point, mark, mark)
        self.update()

    def _update_mark(self, point: QPointF) -> None:
        drag = self._mark_drag
        original = drag.original
        if drag.index is not None:
            delta = point - drag.anchor
            if delta.isNull():
                drag.current = original
                return
            if isinstance(drag.handle, str):
                bounds = QRectF(original.points[0], original.points[-1]).normalized()
                bounds = _resized_selection(bounds, drag.handle, delta, self.size())
                points = (bounds.topLeft(), bounds.bottomRight())
            elif drag.handle is not None:
                points = list(original.points)
                points[drag.handle] = self._annotation_point(original.points[drag.handle] + delta)
                points = tuple(points)
            else:
                points = tuple(anchor + delta for anchor in original.points)
        elif original.kind in _PATH_TOOLS:
            points = drag.current.points
            if (point - points[-1]).manhattanLength() >= 1:
                points = (*points, point)
        else:
            points = (drag.anchor, point)
        drag.current = replace(original, points=points)

    def _update_mark_preview(self, point: QPointF) -> None:
        drag = self._mark_drag
        previous = drag.current
        self._update_mark(point)
        current = drag.current
        if previous == current:
            return
        if drag.index is None and current.kind in _PATH_TOOLS:
            # An appended segment changes only the end of a freehand stroke.
            self._invalidate_marks(None, replace(current, points=current.points[-3:]))
            return
        self._invalidate_marks(previous, current)

    def _invalidate_marks(self, previous: Mark | None, current: Mark | None) -> None:
        if previous == current:
            return
        dirty = QRegion()
        for mark in (previous, current):
            if mark is not None:
                bounds = self._annotations.mark_bounds(mark).adjusted(-6, -6, 6, 6)
                dirty = dirty.united(QRegion(bounds.toAlignedRect()))
        self.update(dirty)

    @staticmethod
    def _valid_mark(mark: Mark) -> bool:
        if mark.kind in _AREA_TOOLS:
            bounds = QRectF(mark.points[0], mark.points[-1]).normalized()
            return bounds.width() >= 2 and bounds.height() >= 2
        if mark.kind in _ENDPOINT_TOOLS:
            return (mark.points[0] - mark.points[-1]).manhattanLength() >= 2
        return True

    def _finish_mark(self) -> None:
        drag = self._mark_drag
        self._mark_drag = None
        if not self._valid_mark(drag.current):
            self.update()
            return
        if drag.index is not None:
            self._annotations.replace(drag.index, drag.current)
        else:
            index = self._annotations.add(drag.current)
            if drag.current.kind not in {"eraser", "mosaic_brush"}:
                self._select_mark(index)
        self._update_annotation_view()

    def _append_polyline(self, point: QPointF) -> None:
        if not self._polyline_points or (point - self._polyline_points[-1]).manhattanLength() >= 2:
            self._polyline_points.append(point)
        self._polyline_cursor = point
        self._update_annotation_view()

    def _finish_polyline(self) -> None:
        if not self._polyline_points:
            return
        if len(self._polyline_points) >= 2:
            mark = Mark("polyline", tuple(self._polyline_points), self._toolbar.style_for("polyline"))
            self._select_mark(self._annotations.add(mark))
        self._polyline_points.clear()
        self._polyline_cursor = None
        self._update_annotation_view()

    def _render_state(self) -> tuple[Mark | None, tuple[int, Mark] | None]:
        if self._text_anchor is not None:
            # While the IME owns the composition it paints its candidate text and formatting.
            text = "" if self._text_editor.preedit else self._text_editor.text()
            mark = self._text_edit_mark(text)
            if self._text_index is not None:
                return None, (self._text_index, mark)
            return mark if text else None, None
        if self._mark_drag is not None:
            drag = self._mark_drag
            if not self._valid_mark(drag.current):
                return None, None
            if drag.index is not None:
                return None, (drag.index, drag.current)
            return drag.current, None
        if self._polyline_points:
            points = tuple(self._polyline_points)
            if (
                self._polyline_cursor is not None
                and (self._polyline_cursor - points[-1]).manhattanLength() >= 2
            ):
                points = (*points, self._polyline_cursor)
            if len(points) > 1:
                return Mark("polyline", points, self._toolbar.style_for("polyline")), None
        return None, None

    def closeEvent(self, event: QCloseEvent) -> None:
        self._reset_selection()
        self._frame = QImage()
        self._dark_frame = QImage()
        self._dark_frame_key = None
        super().closeEvent(event)

    def _clamp(self, point: QPointF) -> QPointF:
        return QPointF(
            min(max(0.0, point.x()), self.width()),
            min(max(0.0, point.y()), self.height()),
        )

    def _annotation_point(self, point: QPointF) -> QPointF:
        return QPointF(
            min(self._selection.right(), max(self._selection.left(), point.x())),
            min(self._selection.bottom(), max(self._selection.top(), point.y())),
        )

    @Slot()
    def _position_controls(self) -> None:
        if self._selection.isEmpty():
            return
        margin = 8
        gap = 6
        self._position_info()
        self._toolbar.adjustSize()
        desired_x = math.ceil(self._selection.right()) - self._toolbar.width()
        x = max(margin, min(desired_x, self.width() - self._toolbar.width() - margin))
        below = math.ceil(self._selection.bottom()) + gap
        above = math.floor(self._selection.top()) - self._toolbar.height() - gap
        above_toolbar = QRect(QPoint(x, above), self._toolbar.size())
        if above_toolbar.intersects(self._info.geometry()):
            above = self._info.y() - self._toolbar.height() - gap
        if below + self._toolbar.height() <= self.height() - margin:
            desired_y = below
        elif above >= margin:
            desired_y = above
        else:
            desired_y = math.floor(self._selection.bottom()) - self._toolbar.height() - gap
        y = max(margin, min(desired_y, self.height() - self._toolbar.height() - margin))
        self._toolbar.move(x, y)
        self._position_side_tools()
        self._schedule_cursor_refresh()

    def _position_info(self) -> None:
        crop = self._physical_crop()
        if crop.isEmpty():
            self._info.hide()
            return
        dimensions = f"{crop.width()} × {crop.height()} px"
        if dimensions != self._size_label.text():
            self._size_label.setText(dimensions)
            self._info.adjustSize()
        left = math.floor(self._selection.left())
        top = math.floor(self._selection.top())
        x = max(8, min(left, self.width() - self._info.width() - 8))
        y = top - self._info.height() - 9
        if y < 8:
            y = top + 10
        if self._info.pos() != QPoint(x, y):
            self._info.move(x, y)
        if self._info.isHidden():
            self._info.show()
            self._info.raise_()

    def _position_side_tools(self) -> None:
        margin, gap = 8, 6
        self._side_tools.adjustSize()
        width, height = self._side_tools.width(), self._side_tools.height()
        right = math.ceil(self._selection.right()) + gap
        if right + width > self.width() - margin:
            right = math.floor(self._selection.right()) - width - 12
        x = max(margin, min(right, self.width() - width - margin))
        y = max(margin, min(math.floor(self._selection.top()), self.height() - height - margin))
        bottom_tools = self._toolbar.geometry()
        if QRect(x, y, width, height).intersects(bottom_tools):
            below = bottom_tools.bottom() + gap + 1
            above = bottom_tools.top() - height - gap
            if below + height <= self.height() - margin:
                y = below
            elif above >= margin:
                y = above
        self._side_tools.move(x, y)
        if self._qr_status.isVisible():
            self._position_qr_status()

    def _position_qr_status(self) -> None:
        margin, gap = 8, 6
        self._qr_status.adjustSize()
        x = self._side_tools.geometry().right() + gap + 1
        if x + self._qr_status.width() > self.width() - margin:
            x = self._side_tools.x() - self._qr_status.width() - gap
        y = self._side_tools.y() + (self._side_tools.height() - self._qr_status.height()) // 2
        self._qr_status.move(max(margin, x), max(margin, y))

    def _show_qr_status(self, text: str, *, temporary: bool = True) -> None:
        self._qr_status.setText(text)
        self._position_qr_status()
        self._qr_status.show()
        self._qr_status.raise_()
        if temporary:
            self._qr_status_timer.start()

    def _invalidate_qr(self) -> None:
        self._revision += 1
        self._qr_pool.clear()
        self._qr_status_timer.stop()
        self._qr_status.hide()

    def _reset_selection(self) -> None:
        self._info_timer.stop()
        self._magnifier.dismiss(clear=True)
        self._invalidate_qr()
        self._cancel_text()
        self._origin = None
        self._resize_handle = None
        self._resize_base = QRectF()
        self._move_base = None
        self._selection = QRectF()
        self._crop = QImage()
        self._tool = ""
        self._selected_mark = None
        self._mark_drag = None
        self._polyline_points.clear()
        self._polyline_cursor = None
        self._annotations.clear()
        self._toolbar.reset()
        self._toolbar.set_history(False, False)
        self._toolbar.hide()
        self._side_tools.hide()
        self._info.hide()
        self._cursors.apply_qt(self, CROSSHAIR)


    @Slot(str)
    def _set_tool(self, kind: str) -> None:
        if kind == self._tool:
            return
        self._commit_text()
        self._finish_polyline()
        self._mark_drag = None
        self._tool = kind
        self._select_mark(None)
        self._schedule_cursor_refresh()
        self.update()

    def _select_mark(self, index: int | None) -> None:
        self._selected_mark = index
        if index is not None:
            mark = self._annotations.get(index)
            self._toolbar.load_mark_style(mark.kind, mark.style)
        elif self._tool:
            self._toolbar.load_mark_style(self._tool, self._toolbar.style_for(self._tool))

    @Slot(str, object)
    def _change_options(self, kind: str, style: AnnotationStyle) -> None:
        if kind != self._tool:
            return
        if self._selected_mark is not None:
            index = self._selected_mark
            mark = self._annotations.get(index)
            if mark.kind == kind and style != mark.style:
                self._annotations.replace(index, replace(mark, style=style))
        if kind in _BRUSH_CURSORS:
            self._schedule_cursor_refresh()
        if kind == "text":
            self._update_text_editor()
        self._update_annotation_view()

    def _start_text(self, point: QPointF, index: int | None) -> None:
        self._text_anchor = point
        self._text_index = index
        self._text_editor.setText(self._annotations.get(index).text if index is not None else "")
        self._update_text_editor()
        for shortcut in self._capture_shortcuts:
            shortcut.setEnabled(False)
        # A click on the outline can go to the canvas; keep keyboard focus in the editor.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._text_editor.show()
        self._text_editor.raise_()
        self._text_editor.setFocus()
        if index is not None:
            self._text_editor.selectAll()

    def _text_edit_mark(self, text: str) -> Mark:
        assert self._text_anchor is not None
        if self._text_index is not None:
            return replace(self._annotations.get(self._text_index), points=(self._text_anchor,), text=text)
        return Mark("text", (self._text_anchor,), self._toolbar.style_for("text"), text)

    @Slot()
    def _update_text_editor(self) -> None:
        if self._text_anchor is None:
            return
        editor = self._text_editor
        mark = self._text_edit_mark(editor.text() + editor.preedit)
        font = annotation_font(mark.style)
        if editor.font() != font:
            editor.setFont(font)
        editor.set_ink(QColor(mark.style.color))
        content = self._mark_bounds(mark)
        pad = _TEXT_OUTLINE_PADDING
        self._text_outline = content.adjusted(-pad, -pad, pad, pad)
        bounds = content.adjusted(0, 0, 8, 3).toAlignedRect()
        editor.setGeometry(bounds)
        clip = bounds.intersected(self._selection.toAlignedRect()).translated(-bounds.topLeft())
        editor.setMask(QRegion(clip))
        self.update()

    def _over_text_outline(self, point: QPointF) -> bool:
        if self._text_anchor is None or not self._selection.contains(point):
            return False
        hit = _TEXT_OUTLINE_HIT
        return (
            self._text_outline.adjusted(-hit, -hit, hit, hit).contains(point)
            and not self._text_outline.adjusted(hit, hit, -hit, -hit).contains(point)
        )

    def _handle_text_drag(self, event: QMouseEvent, point: QPointF) -> bool:
        if self._text_anchor is None:
            return False
        event_type = event.type()
        left = event.button() == Qt.MouseButton.LeftButton
        if event_type in {QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick}:
            if not left or not self._over_text_outline(point):
                return False
            self._text_drag_offset = point - self._text_anchor
            self._text_editor.setFocus(Qt.FocusReason.MouseFocusReason)
        elif self._text_drag_offset is not None and (
            event_type == QEvent.Type.MouseMove or (event_type == QEvent.Type.MouseButtonRelease and left)
        ):
            anchor = self._annotation_point(point - self._text_drag_offset)
            if anchor != self._text_anchor:
                self._text_anchor = anchor
                self._update_text_editor()
                QGuiApplication.inputMethod().update(Qt.InputMethodQuery.ImCursorRectangle)
            if event_type == QEvent.Type.MouseButtonRelease:
                self._text_drag_offset = None
                self._text_editor.setFocus(Qt.FocusReason.MouseFocusReason)
                self._schedule_cursor_refresh()
        else:
            return False
        self._update_cursor(point, refresh_native=True)
        event.accept()
        return True

    @Slot()
    def _accept_text(self) -> None:
        if not self._text_editor.preedit:
            self._commit_text()

    @Slot()
    def _cancel_text(self) -> None:
        if self._text_anchor is None:
            return
        self._text_anchor = None
        self._text_index = None
        self._text_drag_offset = None
        if self._text_editor.preedit:
            QGuiApplication.inputMethod().reset()
            if self._text_editor.preedit:
                self._text_editor.inputMethodEvent(QInputMethodEvent())
        self._text_editor.hide()
        self._text_editor.clear()
        for shortcut in self._capture_shortcuts:
            shortcut.setEnabled(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()
        self._schedule_cursor_refresh()
        self.update()

    @Slot()
    def _commit_text(self) -> None:
        if self._text_anchor is None:
            return
        if self._text_editor.preedit:
            QGuiApplication.inputMethod().commit()
        index, text = self._text_index, self._text_editor.text()
        mark = self._text_edit_mark(text)
        self._cancel_text()
        if not text.strip():
            return
        if index is not None:
            existing = self._annotations.get(index)
            if existing != mark:
                self._annotations.replace(index, mark)
        else:
            index = self._annotations.add(mark)
        self._select_mark(index)
        self._update_annotation_view()

    @Slot()
    def _undo(self) -> None:
        self._cancel_text()
        self._mark_drag = None
        if self._polyline_points:
            self._polyline_points.pop()
            self._polyline_cursor = None
        elif self._annotations.undo():
            self._select_mark(None)
        self._update_annotation_view()

    @Slot()
    def _redo(self) -> None:
        self._cancel_text()
        self._mark_drag = None
        if not self._polyline_points and self._annotations.redo():
            self._select_mark(None)
        self._update_annotation_view()

    def _update_annotation_view(self) -> None:
        self._invalidate_qr()
        self._toolbar.set_history(
            bool(self._polyline_points) or self._annotations.can_undo(),
            not self._polyline_points and self._annotations.can_redo(),
        )
        self.update()

    @Slot()
    def _on_enter(self) -> None:
        if self._polyline_points:
            self._finish_polyline()
        else:
            self._copy()

    @Slot()
    def _on_escape(self) -> None:
        if self._polyline_points:
            self._polyline_points.clear()
            self._polyline_cursor = None
            self._update_annotation_view()
        else:
            self._finish()

    def _output_image(self) -> QImage:
        self._commit_text()
        self._finish_polyline()
        if self._crop.isNull():
            return self._crop
        preview, replacement = self._render_state()
        if not self._annotations.has_annotations() and preview is None:
            return self._crop
        return self._annotations.flatten(
            self._frame, self._selection, self._physical_crop(), self.size(),
            preview=preview, replacement=replacement,
        )

    @Slot()
    def _finish(self) -> None:
        self.close()

    @Slot()
    def shutdown(self) -> None:
        self.close()
        self._qr_pool.waitForDone()
        self._cursors.close()

    @Slot()
    def _copy(self) -> None:
        crop = self._output_image()
        if not crop.isNull():
            self._finish()
            self.image_copied.emit(crop)

    @Slot()
    def _pin(self) -> None:
        crop = self._output_image()
        if not crop.isNull():
            source_rect = self._selection.toAlignedRect().translated(self.mapToGlobal(QPoint()))
            self._finish()
            self.image_pinned.emit(crop, source_rect)

    @Slot()
    def _save(self) -> None:
        if self._crop.isNull():
            return
        path = self._choose_png_path()
        if not path:
            return
        if Path(path).suffix.lower() != ".png":
            QMessageBox.warning(self, "文件名无效", "请使用 .png 扩展名保存截图。")
            self._schedule_cursor_refresh()
            return

        image = self._output_image()
        # The composited screenshot remains fully opaque when its source is opaque.
        if not self._frame.hasAlphaChannel() and image.hasAlphaChannel():
            image = image.convertToFormat(QImage.Format.Format_RGB32)

        output = QSaveFile(path)
        written = output.open(QIODevice.OpenModeFlag.WriteOnly) and image.save(output, "PNG")
        size_bytes = output.pos() if written else 0
        if not written or not output.commit():
            QMessageBox.warning(self, "保存失败", "无法写入该位置，请选择其他文件夹。")
            self._schedule_cursor_refresh()
            return
        self._finish()
        self.image_saved.emit(path, image.size(), size_bytes)

    def _choose_png_path(self) -> str:
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        dialog = QFileDialog(self, "保存截图", folder, "PNG 图片 (*.png)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix("png")
        dialog.selectFile(f"瞬截_{datetime.now():%Y%m%d_%H%M%S}.png")
        result = dialog.exec()
        self._schedule_cursor_refresh()
        return dialog.selectedFiles()[0] if result == QDialog.DialogCode.Accepted else ""

    @Slot()
    def _scan_qr(self) -> None:
        image = self._output_image()
        if image.isNull() or not self.isVisible():
            return
        self._invalidate_qr()
        self._show_qr_status("正在识别二维码…", temporary=False)
        scan = _QrScan(self._revision, image)
        scan.signals.finished.connect(self._qr_finished)
        self._qr_pool.start(scan)

    @Slot(int, str, bool)
    def _qr_finished(self, revision: int, text: str, failed: bool) -> None:
        if revision != self._revision or not self.isVisible():
            return
        if text:
            QApplication.clipboard().setText(text)
            self._show_qr_status("二维码内容已复制")
        elif failed:
            self._show_qr_status("二维码识别失败")
        else:
            self._show_qr_status("未识别到二维码")

