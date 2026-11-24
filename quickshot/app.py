import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QRect, QSettings, QStandardPaths, QTimer, Qt
from PySide6.QtGui import QFont, QIcon, QImage, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
)

from . import __version__
from .capture import CaptureOverlay
from .hotkey import DEFAULT_HOTKEY, GlobalHotkey
from .pin import PinnedImage, wait_for_recognition


_PORTABLE = QKeySequence.SequenceFormat.PortableText
_SETTINGS_KEY = "capture/shortcut"
_TRAY_HINT_KEY = "startup/trayHintShown"


class ShortcutDialog(QDialog):
    def __init__(self, current: QKeySequence, save_shortcut):
        super().__init__()
        self._save_shortcut = save_shortcut
        self.setObjectName("shortcutDialog")
        self.setWindowTitle("瞬截 · 设置")
        self.setMinimumWidth(300)

        title = QLabel("截图快捷键")
        title.setObjectName("shortcutTitle")

        self._editor = QKeySequenceEdit(current)
        self._editor.setObjectName("shortcutEditor")
        self._editor.setAccessibleName("截图快捷键")
        self._editor.setToolTip("点击输入框，按下新的组合键。")
        self._editor.setMaximumSequenceLength(1)
        self._editor.editingFinished.connect(self._clear_error)

        reset = QPushButton("恢复默认")
        reset.clicked.connect(lambda: self._editor.setKeySequence(QKeySequence(DEFAULT_HOTKEY)))
        form = QHBoxLayout()
        form.addWidget(self._editor, 1)
        form.addWidget(reset)

        self._error = QLabel()
        self._error.setObjectName("shortcutError")
        self._error.setWordWrap(True)
        self._error.hide()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)

        version = QLabel(f"版本 {__version__}")
        version.setObjectName("applicationVersion")
        version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        footer = QHBoxLayout()
        footer.addWidget(version)
        footer.addStretch()
        footer.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(self._error)
        layout.addLayout(footer)
        self.setStyleSheet("""
            QDialog#shortcutDialog { background: #f4f7f8; color: #183438; }
            QLabel { color: #395156; }
            QLabel#shortcutTitle { color: #102c30; font-size: 15px; font-weight: 700; }
            QLabel#applicationVersion { color: #506971; font-size: 11px; }
            QLabel#shortcutError { color: #ab3231; }
            QLineEdit { background: white; border: 1px solid #b8cbce; border-radius: 5px;
                         padding: 5px 7px; color: #183438; }
            QLineEdit:focus { border-color: #087e86; }
            QPushButton { background: #fff; border: 1px solid #b8cbce; border-radius: 5px;
                          padding: 4px 9px; color: #183438; }
            QPushButton:hover { background: #e6f2f2; }
            QPushButton:default { background: #087e86; border-color: #087e86; color: #fff; }
            QPushButton:default:hover { background: #076d74; }
            """)

    def _clear_error(self) -> None:
        self._error.hide()

    def _apply(self) -> None:
        try:
            self._save_shortcut(self._editor.keySequence())
        except ValueError as error:
            self._error.setText(str(error))
            self._error.show()
            return
        self.accept()


class Quickshot:
    def __init__(self, application: QApplication):
        self._app = application
        self._settings = QSettings()
        self._shortcut = QKeySequence.fromString(self._settings.value(_SETTINGS_KEY, DEFAULT_HOTKEY, str), _PORTABLE)
        self._settings_open = False
        self._pins: dict[int, PinnedImage] = {}

        self._capture = CaptureOverlay()
        self._capture.image_copied.connect(self._copy_image)
        self._capture.image_pinned.connect(self._pin_image)
        self._capture.image_saved.connect(self._image_saved)

        self._menu = QMenu()
        self._capture_action = self._menu.addAction("开始截图")
        self._capture_action.triggered.connect(lambda: QTimer.singleShot(0, self.start_capture))
        self._menu.addAction("设置…", self._edit_shortcut)
        self._menu.addSeparator()
        self._menu.addAction("退出瞬截", application.quit)

        self._tray = QSystemTrayIcon(application.windowIcon(), application)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._tray_activated)
        self._tray.show()

        self._hotkey = GlobalHotkey(application, self.start_capture)
        try:
            self._hotkey.set_shortcut(self._shortcut)
        except ValueError as error:
            message = f"{error}\n可以从托盘菜单开始截图或改键。"
            QTimer.singleShot(0, lambda: self._notice("截图快捷键未启用", message))
        self._update_tray()
        if not self._settings.value(_TRAY_HINT_KEY, False, bool):
            QTimer.singleShot(400, self._tray, self._show_first_start_hint)
        application.aboutToQuit.connect(self._close)

    def _show_first_start_hint(self) -> None:
        if (not self._tray.isVisible() or not self._tray.supportsMessages()
                or self._settings.value(_TRAY_HINT_KEY, False, bool)):
            return
        self._tray.showMessage(
            "瞬截已启动", "瞬截正在系统托盘运行。右键点击托盘图标可打开设置。",
            QSystemTrayIcon.MessageIcon.Information, 5000,
        )
        self._settings.setValue(_TRAY_HINT_KEY, True)
        self._settings.sync()

    def start_capture(self) -> None:
        if self._settings_open:
            return
        try:
            self._capture.start()
        except RuntimeError as error:
            self._notice("截图失败", str(error))

    def _update_tray(self) -> None:
        if self._hotkey.active:
            shortcut = self._shortcut.toString(QKeySequence.SequenceFormat.NativeText)
            self._tray.setToolTip(f"瞬截 · {shortcut} 截图")
            self._capture_action.setText(f"开始截图\t{shortcut}")
        else:
            self._tray.setToolTip("瞬截 · 快捷键未启用")
            self._capture_action.setText("开始截图")

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.start_capture()

    def _edit_shortcut(self) -> None:
        if self._settings_open:
            return
        self._settings_open = True
        try:
            ShortcutDialog(self._shortcut, self._set_shortcut).exec()
        finally:
            self._settings_open = False

    def _set_shortcut(self, shortcut: QKeySequence) -> None:
        self._hotkey.set_shortcut(shortcut)
        self._shortcut = shortcut
        self._settings.setValue(_SETTINGS_KEY, shortcut.toString(_PORTABLE))
        self._settings.sync()
        self._update_tray()

    def _notice(self, title: str, message: str) -> None:
        self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Warning, 5000)

    def _copy_image(self, image: QImage) -> None:
        self._app.clipboard().setImage(image)

    def _image_saved(self, path: str) -> None:
        self._tray.showMessage("截图已保存", Path(path).name, QSystemTrayIcon.MessageIcon.Information, 3000)

    def _pin_image(self, image: QImage, geometry: QRect | None = None) -> None:
        pin = PinnedImage(image, geometry)
        token = id(pin)
        self._pins[token] = pin
        pin.destroyed.connect(lambda: self._pins.pop(token, None))
        pin.show()
        pin.raise_()
        pin.activateWindow()

    def _close(self) -> None:
        self._hotkey.close()
        self._tray.hide()
        for pin in tuple(self._pins.values()):
            pin.close()
        self._capture.shutdown()
        wait_for_recognition()


def main() -> int:
    if sys.platform != "win32":
        print("瞬截面向 Windows 11 x64。", file=sys.stderr)
        return 1

    application = QApplication(sys.argv)
    application.setApplicationName("qzz-quickshot")
    application.setApplicationDisplayName("瞬截")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("qzz")
    application.setQuitOnLastWindowClosed(False)
    application.setFont(QFont("Microsoft YaHei UI", 10))
    icon = QIcon(str(Path(__file__).with_name("assets") / "quickshot.ico"))
    if icon.isNull():
        icon = application.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon)
    application.setWindowIcon(icon)

    data = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    data.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data / "quickshot.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, "瞬截", "瞬截已在运行。可从 Windows 托盘打开。")
        return 0
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "瞬截", "Windows 托盘当前不可用。")
        return 1

    quickshot = Quickshot(application)
    result = application.exec()
    del quickshot
    lock.unlock()
    return result
