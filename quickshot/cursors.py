"""Screenshot pointers with an explicit Win32 cursor for the capture canvas."""

import ctypes
from collections import OrderedDict
from ctypes import wintypes
from dataclasses import dataclass
from math import ceil
import sys

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen, QPixmap


@dataclass(frozen=True, slots=True)
class ToolPointer:
    kind: str
    diameter: int = 0
    color: str = ""


CROSSHAIR = ToolPointer("crosshair")
Pointer = ToolPointer | Qt.CursorShape

_WM_SETCURSOR = 0x0020
_HTCLIENT = 1
_CACHE_SIZE = 24
_SYSTEM_CURSORS = {
    Qt.CursorShape.ArrowCursor: 32512,
    Qt.CursorShape.IBeamCursor: 32513,
    Qt.CursorShape.CrossCursor: 32515,
    Qt.CursorShape.SizeFDiagCursor: 32642,
    Qt.CursorShape.SizeBDiagCursor: 32643,
    Qt.CursorShape.SizeHorCursor: 32644,
    Qt.CursorShape.SizeVerCursor: 32645,
    Qt.CursorShape.SizeAllCursor: 32646,
}


class _IconInfo(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP),
    ]


def _win_function(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


if sys.platform == "win32":
    _user = ctypes.WinDLL("user32")
    _gdi = ctypes.WinDLL("gdi32")
    _create_icon = _win_function(_user, "CreateIconIndirect", wintypes.HANDLE, ctypes.POINTER(_IconInfo))
    _get_cursor = _win_function(_user, "GetCursor", wintypes.HANDLE)
    _set_cursor = _win_function(_user, "SetCursor", wintypes.HANDLE, wintypes.HANDLE)
    _load_cursor = _win_function(_user, "LoadCursorW", wintypes.HANDLE, wintypes.HINSTANCE, ctypes.c_void_p)
    _destroy_cursor = _win_function(_user, "DestroyCursor", wintypes.BOOL, wintypes.HANDLE)
    _get_capture = _win_function(_user, "GetCapture", wintypes.HWND)
    _get_cursor_pos = _win_function(_user, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
    _window_from_point = _win_function(_user, "WindowFromPoint", wintypes.HWND, wintypes.POINT)
    _create_bitmap = _win_function(
        _gdi, "CreateBitmap", wintypes.HBITMAP, ctypes.c_int, ctypes.c_int,
        wintypes.UINT, wintypes.UINT, ctypes.c_char_p,
    )
    _delete_object = _win_function(_gdi, "DeleteObject", wintypes.BOOL, wintypes.HANDLE)


def is_canvas_cursor_message(event_type, message) -> bool:
    if sys.platform != "win32" or bytes(event_type) != b"windows_generic_MSG" or message is None:
        return False
    address = int(message)
    if not address:
        return False
    event = wintypes.MSG.from_address(address)
    return (
        event.message == _WM_SETCURSOR
        and (event.lParam & 0xffff) == _HTCLIENT
        and event.wParam == event.hWnd
        and event.lParam >> 16 != 0  # Let Qt handle menus and popup cursors.
    )


def _artwork(pointer: ToolPointer, scale: float) -> tuple[QImage, int]:
    extent = 10 if pointer.kind == "crosshair" else ceil(pointer.diameter / 2 + 2)
    pixels = ceil((2 * extent + 1) * scale)
    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    painter.translate(extent + 0.5, extent + 0.5)
    center = QPointF()
    if pointer.kind == "crosshair":
        for color, width in (("#172025", 2.0), ("#ffffff", 1.16)):
            painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for start, finish in ((-8.5, -2.5), (2.5, 8.5)):
                painter.drawLine(QPointF(start, 0), QPointF(finish, 0))
                painter.drawLine(QPointF(0, start), QPointF(0, finish))
    elif pointer.kind == "pen":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(pointer.color))
        painter.drawEllipse(center, pointer.diameter / 2, pointer.diameter / 2)
    else:
        outer = min(1.7, pointer.diameter / 3)
        radius = (pointer.diameter - outer) / 2
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for color, width in (("#172025", outer), ("#ffffff", outer * 0.58)):
            painter.setPen(QPen(QColor(color), width))
            painter.drawEllipse(center, radius, radius)
    painter.end()
    return image, extent


def _native_artwork(image: QImage, hotspot: int) -> int | None:
    width, height = image.width(), image.height()
    bitmap = image.toHBITMAP()
    color_bitmap = int(bitmap) if bitmap else 0
    mask_bitmap = _create_bitmap(width, height, 1, 1, bytes(((width + 15) // 16 * 2) * height))
    try:
        if color_bitmap and mask_bitmap:
            icon = _IconInfo(False, hotspot, hotspot, mask_bitmap, color_bitmap)
            return _create_icon(ctypes.byref(icon))
        return None
    finally:
        if color_bitmap:
            _delete_object(color_bitmap)
        if mask_bitmap:
            _delete_object(mask_bitmap)


@dataclass(slots=True)
class _CursorImage:
    qt: QCursor
    native: int | None = None
    owned: bool = False


class CanvasCursors:
    def __init__(self) -> None:
        self._cache: OrderedDict[tuple[Pointer, float], _CursorImage] = OrderedDict()
        self._applied: tuple[Pointer, float] | None = None
        self._native_platform = sys.platform == "win32" and QGuiApplication.platformName() == "windows"

    @staticmethod
    def _key(pointer: Pointer, ratio: float) -> tuple[Pointer, float]:
        return pointer, round(ratio, 3) if isinstance(pointer, ToolPointer) else 1.0

    def _cursor(self, key: tuple[Pointer, float]) -> _CursorImage:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        pointer, ratio = key
        if isinstance(pointer, ToolPointer):
            image, extent = _artwork(pointer, ratio)
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(ratio)
            native = _native_artwork(image, round(extent * ratio)) if self._native_platform else None
            if self._native_platform and not native:
                native = _load_cursor(None, _SYSTEM_CURSORS[Qt.CursorShape.CrossCursor])
                cursor = _CursorImage(QCursor(pixmap, extent, extent), native)
            else:
                cursor = _CursorImage(QCursor(pixmap, extent, extent), native, bool(native))
        else:
            resource = _SYSTEM_CURSORS.get(pointer)
            native = _load_cursor(None, resource) if self._native_platform and resource else None
            cursor = _CursorImage(QCursor(pointer), native)

        self._cache[key] = cursor
        if len(self._cache) > _CACHE_SIZE:
            current_handle = _get_cursor() if self._native_platform else None
            oldest = next(item for item in self._cache if item != self._applied
                          and (not self._cache[item].owned or self._cache[item].native != current_handle))
            removed = self._cache.pop(oldest)
            if removed.owned:
                _destroy_cursor(removed.native)
        return cursor

    def apply_qt(self, widget, pointer: Pointer) -> None:
        key = self._key(pointer, widget.devicePixelRatioF())
        shape = Qt.CursorShape.BitmapCursor if isinstance(pointer, ToolPointer) else pointer
        if key != self._applied or widget.cursor().shape() != shape:
            widget.setCursor(self._cursor(key).qt)
            self._applied = key

    def owns_pointer(self, widget, *, include_hover: bool = False) -> bool:
        if not self._native_platform:
            return False
        window = int(widget.winId())
        if _get_capture() == window:
            return True
        if include_hover:
            point = wintypes.POINT()
            return bool(_get_cursor_pos(ctypes.byref(point)) and _window_from_point(point) == window)
        return False

    def apply_native(self, widget, pointer: Pointer) -> bool:
        if not self._native_platform:
            return False
        cursor = self._cursor(self._key(pointer, widget.devicePixelRatioF()))
        if not cursor.native:
            return False
        if _get_cursor() != cursor.native:
            _set_cursor(cursor.native)
        return True

    def close(self) -> None:
        for cursor in self._cache.values():
            if cursor.owned:
                _destroy_cursor(cursor.native)
        self._cache.clear()
        self._applied = None
