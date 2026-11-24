import ctypes
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QTimer, Qt
from PySide6.QtGui import QKeySequence


DEFAULT_HOTKEY = "Ctrl+Shift+F2"
_WM_HOTKEY = 0x0312
_MOD_NOREPEAT = 0x4000
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409
_MODIFIERS = {
    Qt.KeyboardModifier.AltModifier: 0x0001,
    Qt.KeyboardModifier.ControlModifier: 0x0002,
    Qt.KeyboardModifier.ShiftModifier: 0x0004,
}
_SPECIAL_KEYS = {
    Qt.Key.Key_Space: 0x20,
    Qt.Key.Key_Insert: 0x2D,
    Qt.Key.Key_Home: 0x24,
    Qt.Key.Key_End: 0x23,
    Qt.Key.Key_PageUp: 0x21,
    Qt.Key.Key_PageDown: 0x22,
}


def windows_hotkey(sequence: QKeySequence) -> tuple[int, int]:
    if sequence.count() != 1:
        raise ValueError("请设置一个截图快捷键。")
    combination = sequence[0]
    qt_modifiers = combination.keyboardModifiers()
    if qt_modifiers & Qt.KeyboardModifier.MetaModifier:
        raise ValueError("请使用 Ctrl、Alt 或 Shift 作为修饰键。")
    modifiers = sum(flag for modifier, flag in _MODIFIERS.items() if qt_modifiers & modifier)
    key = combination.key()
    if key == Qt.Key.Key_F12:
        raise ValueError("F12 是 Windows 调试快捷键，请选择其他按键。")
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return modifiers, 0x70 + key - Qt.Key.Key_F1
    if not modifiers:
        raise ValueError("请按住 Ctrl、Alt 或 Shift，再按一个键。")
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return modifiers, int(key)
    if key in _SPECIAL_KEYS:
        return modifiers, _SPECIAL_KEYS[key]
    raise ValueError("请选择字母、数字、功能键、空格或翻页键。")


class GlobalHotkey(QAbstractNativeEventFilter):
    """Register one Win32 hotkey and dispatch it on the Qt event loop."""

    def __init__(self, application: QCoreApplication, activate: Callable[[], None]):
        super().__init__()
        self._application = application
        self._activate = activate
        self._id = 1
        self._registered: tuple[int, int] | None = None
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._register = user32.RegisterHotKey
        self._register.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._register.restype = wintypes.BOOL
        self._unregister = user32.UnregisterHotKey
        self._unregister.argtypes = [wintypes.HWND, ctypes.c_int]
        self._unregister.restype = wintypes.BOOL
        application.installNativeEventFilter(self)

    @property
    def active(self) -> bool:
        return self._registered is not None

    def set_shortcut(self, sequence: QKeySequence) -> None:
        binding = windows_hotkey(sequence)
        if binding == self._registered:
            return
        new_id = 2 if self._id == 1 else 1
        modifiers, key = binding
        if not self._register(None, new_id, modifiers | _MOD_NOREPEAT, key):
            error = ctypes.get_last_error()
            if error == _ERROR_HOTKEY_ALREADY_REGISTERED:
                raise ValueError("这个快捷键已被占用，请换一个组合。")
            raise ValueError(f"Windows 无法注册这个快捷键（错误 {error}）。")
        if self._registered is not None:
            self._unregister(None, self._id)
        self._registered = binding
        self._id = new_id

    def nativeEventFilter(self, event_type, message):
        if self._registered is not None and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            native = wintypes.MSG.from_address(int(message))
            if not native.hWnd and native.message == _WM_HOTKEY and native.wParam == self._id:
                QTimer.singleShot(0, self._activate)
                return True, 0
        return False, 0

    def close(self) -> None:
        if self._registered is not None:
            self._unregister(None, self._id)
            self._registered = None
        self._application.removeNativeEventFilter(self)
