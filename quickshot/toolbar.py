"""Compact screenshot controls and remembered settings for each drawing tool."""

from dataclasses import replace
import re

from PySide6.QtCore import QEvent, QPointF, QRectF, QSettings, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .annotations import AnnotationStyle


_GROUPS = {
    "shapeAnnotation": ("形状", ("rectangle", "ellipse")),
    "lineAnnotation": ("线条和箭头", ("arrow", "line", "polyline")),
    "penAnnotation": ("画笔", ("pen",)),
    "highlightAnnotation": ("荧光笔", ("highlight",)),
    "obscureAnnotation": ("马赛克和模糊", ("mosaic", "mosaic_brush", "blur")),
    "textAnnotation": ("文字", ("text",)),
    "eraserAnnotation": ("橡皮擦", ("eraser",)),
}
_TOOL_NAMES = dict(
    rectangle="矩形", ellipse="椭圆", arrow="箭头", line="直线", polyline="折线",
    pen="画笔", highlight="荧光笔", mosaic="矩形马赛克", mosaic_brush="涂抹马赛克",
    blur="模糊", text="文字", eraser="橡皮擦",
)
_DEFAULTS = {kind: AnnotationStyle() for kind in _TOOL_NAMES}
_DEFAULTS.update(
    arrow=AnnotationStyle(variant="filled"), highlight=AnnotationStyle(color="#e6be38", width=16),
    mosaic=AnnotationStyle(size=14), mosaic_brush=AnnotationStyle(width=24, size=14),
    blur=AnnotationStyle(size=16), eraser=AnnotationStyle(width=20),
)
_VARIANTS = (
    ("outlineShape", "描边", "solid", {"rectangle", "ellipse"}),
    ("filledShape", "填充", "filled", {"rectangle", "ellipse"}),
    ("filledArrow", "实心箭头", "filled", {"arrow"}),
    ("openArrow", "空心箭头", "open", {"arrow"}),
    ("doubleArrow", "双向箭头", "double", {"arrow"}),
)
_PALETTE = (
    ("红", "#e3494e"), ("橙", "#ec8b43"), ("黄", "#e6be38"), ("绿", "#21a889"),
    ("蓝", "#4788dc"), ("紫", "#8b67d8"), ("白", "#f7f9fa"), ("黑", "#15252b"),
    ("粉", "#f4a4ad"), ("杏", "#ffd0a4"), ("浅黄", "#fbec9a"), ("薄荷", "#96ddc3"),
    ("浅蓝", "#a6cef4"), ("浅紫", "#d5c2f4"), ("浅灰", "#bacbd2"), ("深灰", "#5a6f79"),
)
_COLORLESS = {"mosaic", "mosaic_brush", "blur", "eraser"}
_MEASURE = {
    "text": ("size", "字号", 10, 96), "mosaic": ("size", "颗粒大小", 3, 64),
    "blur": ("size", "模糊强度", 3, 64), "eraser": ("width", "橡皮宽度", 3, 96),
    "highlight": ("width", "笔宽", 3, 72), "mosaic_brush": ("width", "笔宽", 3, 96),
}
_CELL_SIZE = ("size", "颗粒大小", 3, 64)
_LINE_WIDTH = ("width", "线宽", 1, 40)
_PREFIX = "annotation"
_SHARED_PREFERENCES = (
    ("shapeAnnotation", _GROUPS["shapeAnnotation"][1], {"color": None, "width": _LINE_WIDTH[2:]}),
    ("lineAnnotation", _GROUPS["lineAnnotation"][1], {"color": None, "width": _LINE_WIDTH[2:]}),
    ("obscureAnnotation", ("mosaic", "mosaic_brush"), {"size": _CELL_SIZE[2:]}),
)
_SHARED_FIELDS = {
    (kind, field): (group, kinds)
    for group, kinds, fields in _SHARED_PREFERENCES
    for kind in kinds for field in fields
}


def _tool_icon(name: str, color: str = "#dceced") -> QIcon:
    image = QPixmap(40, 40)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    ink = QColor(color)
    painter.setPen(QPen(ink, 1.45, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

    def line(*coordinates: tuple[float, float]) -> None:
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in coordinates]))

    match name:
        case "shapeAnnotation":
            painter.drawRoundedRect(QRectF(3.5, 4, 9, 8), 1, 1)
            painter.drawEllipse(QRectF(9, 9.5, 7.5, 6.5))
        case "lineAnnotation":
            line((3.5, 14.5), (8, 10), (10.5, 12), (16, 5))
            line((11.5, 5), (16, 5), (16, 9.5))
        case "penAnnotation":
            line((4, 16), (5, 11.5), (13.6, 3), (17, 6.4), (8.5, 15), (4, 16))
            line((11.7, 5), (15, 8.3))
        case "highlightAnnotation":
            line((3.5, 14), (8, 9.5), (10.5, 12), (6, 16.5), (3.5, 16.5), (3.5, 14))
            line((8, 9.5), (12.5, 4), (16.5, 8), (10.5, 12))
        case "obscureAnnotation":
            painter.drawRoundedRect(QRectF(3.5, 3.5, 13, 13), 1.1, 1.1)
            painter.fillRect(QRectF(5.8, 5.8, 3, 3), ink)
            painter.fillRect(QRectF(10.8, 5.8, 3, 3), QColor("#88a6ab"))
            painter.fillRect(QRectF(10.8, 10.8, 3, 3), ink)
        case "textAnnotation":
            line((4, 4.5), (16, 4.5))
            line((10, 4.5), (10, 16))
            line((7, 16), (13, 16))
        case "eraserAnnotation":
            line((4, 11.5), (11.8, 3.8), (16.8, 8.8), (9, 16.5), (6.5, 16.5), (4, 14), (4, 11.5))
            line((7.7, 8.6), (12.2, 13.2))
            line((9, 16.5), (16.5, 16.5))
        case "undoAnnotation" | "redoAnnotation":
            if name == "redoAnnotation":
                painter.translate(20, 0)
                painter.scale(-1, 1)
            line((7.5, 4), (4, 7.7), (7.5, 11.3))
            path = QPainterPath(QPointF(4, 7.7))
            path.lineTo(11.4, 7.7)
            path.cubicTo(17, 7.7, 17, 14.5, 12.1, 15.2)
            painter.drawPath(path)
        case "cancelCapture":
            line((5.3, 5.3), (14.7, 14.7))
            line((14.7, 5.3), (5.3, 14.7))
        case "pinImage":
            line((7, 4), (13, 4), (12.5, 8.5), (15, 11.5), (5, 11.5), (7.5, 8.5), (7, 4))
            line((10, 11.5), (10, 16.8))
        case "saveImage":
            line((10, 3.5), (10, 12.7))
            line((6.7, 9.5), (10, 12.7), (13.3, 9.5))
            line((4, 13), (4, 16), (16, 16), (16, 13))
        case "copyImage":
            painter.drawRoundedRect(QRectF(7, 6.5, 9.5, 10.2), 1.25, 1.25)
            line((4, 12.5), (4, 4), (12.3, 4))
        case "rectangle" | "ellipse":
            outline = QRectF(3.4, 5.2, 13.2, 9.6)
            if name == "rectangle":
                painter.drawRoundedRect(outline, 1, 1)
            else:
                painter.drawEllipse(outline)
        case "outlineShape" | "filledShape":
            if name == "filledShape":
                painter.setBrush(ink)
            painter.drawRoundedRect(QRectF(4.2, 4.2, 11.6, 11.6), 2.2, 2.2)
        case "arrow" | "filledArrow" | "openArrow" | "doubleArrow":
            line((4, 15.5), (15.5, 4))
            if name == "filledArrow":
                painter.setBrush(ink)
                painter.drawPolygon(QPolygonF([QPointF(15.5, 4), QPointF(9.5, 5), QPointF(14.5, 10)]))
            else:
                line((9.5, 4), (15.5, 4), (15.5, 10))
            if name == "doubleArrow":
                line((4, 10), (4, 15.5), (9.5, 15.5))
        case "line":
            line((4, 15.5), (15.5, 4))
        case "polyline":
            line((3.5, 13.5), (8.5, 7.5), (11.5, 12), (16.5, 5))
        case "mosaic" | "mosaic_brush":
            outline = QRectF(3.4, 3.4, 13.2, 13.2)
            if name == "mosaic":
                painter.drawRoundedRect(outline, 1, 1)
            else:
                painter.drawEllipse(outline)
            for x, y in ((6, 6), (11, 6), (6, 11), (11, 11)):
                painter.fillRect(QRectF(x, y, 2.8, 2.8), ink)
        case "blur":
            for x, opacity in ((7, 0.4), (10, 1), (13, 0.4)):
                painter.setOpacity(opacity)
                line((x, 5), (x, 15))
        case "editColor":
            line((6, 14), (7, 11), (13, 5), (15, 7), (9, 13), (6, 14))
        case "confirmColor":
            line((4.5, 10.5), (8.5, 14.5), (15.5, 6.5))
    painter.end()
    image.setDevicePixelRatio(2.0)
    return QIcon(image)


class _NumberField(QLineEdit):
    valueChanged = Signal(int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._value = 1
        self.minimum = 1
        self.maximum = 96
        self.unit = ""
        self.setMaxLength(7)
        self.editingFinished.connect(self._commit)

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:
        adjusted = max(self.minimum, min(self.maximum, value))
        previous, self._value = self._value, adjusted
        self.setText(f"{adjusted}{self.unit}")
        if previous != adjusted:
            self.valueChanged.emit(adjusted)

    def _commit(self) -> None:
        digits = self.text().strip().removesuffix("px").strip()
        self.setValue(int(digits) if re.fullmatch(r"[0-9]+", digits) else self._value)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.setValue(self._value + (1 if event.key() == Qt.Key.Key_Up else -1))
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.setValue(self._value + (1 if delta > 0 else -1))
            event.accept()
        else:
            event.ignore()


class CaptureToolbar(QFrame):
    tool_changed = Signal(str)
    options_changed = Signal(str, object)
    layout_changed = Signal()
    undo_requested = Signal()
    redo_requested = Signal()
    close_requested = Signal()
    pin_requested = Signal()
    save_requested = Signal()
    copy_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("captureActions")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._settings = QSettings()
        self._styles = {kind: self._read_style(kind) for kind in _TOOL_NAMES}
        self._last_kind = {}
        for group, (_, kinds) in _GROUPS.items():
            value = self._settings.value(f"{_PREFIX}/group/{group}", kinds[0], str)
            self._last_kind[group] = value if value in kinds else kinds[0]
        self._load_shared_preferences()
        self._group: str | None = None
        self._shown_style = AnnotationStyle()
        self._buttons: dict[str, QToolButton] = {}

        rows = QVBoxLayout(self)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(3)
        rows.setAlignment(Qt.AlignmentFlag.AlignLeft)
        main, main_row = self._row("captureToolStrip", 1)
        rows.addWidget(main)
        for name, (caption, _) in _GROUPS.items():
            button = self._button(name, caption, main)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, group=name: self._select_group(group))
            self._buttons[name] = button
            main_row.addWidget(button)
        self._divider(main_row)
        self._undo = self._action(main_row, "undoAnnotation", "撤销 · Ctrl+Z", self.undo_requested)
        self._redo = self._action(main_row, "redoAnnotation", "重做 · Ctrl+Y", self.redo_requested)
        self._divider(main_row)
        for name, tooltip, signal in (
            ("cancelCapture", "取消 · Esc", self.close_requested),
            ("pinImage", "原位贴图 · P", self.pin_requested),
            ("saveImage", "保存 PNG · Ctrl+S", self.save_requested),
            ("copyImage", "复制 · Enter", self.copy_requested),
        ):
            self._action(main_row, name, tooltip, signal)

        self._parameters, options = self._row("annotationParameters", 3)
        rows.addWidget(self._parameters, 0, Qt.AlignmentFlag.AlignLeft)
        self._measure = self._number_field("annotationMeasure")
        self._measure.valueChanged.connect(self._change_measure)
        self._width_label = QLabel("笔宽", self._parameters)
        self._width_label.setObjectName("annotationParameterLabel")
        options.addWidget(self._width_label)
        options.addWidget(self._measure)
        self._cell_label = QLabel("颗粒", self._parameters)
        self._cell_label.setObjectName("annotationParameterLabel")
        options.addWidget(self._cell_label)
        self._mosaic_size = self._number_field("annotationMosaicSize")
        self._mosaic_size.valueChanged.connect(lambda value: self._change_style(size=value))
        options.addWidget(self._mosaic_size)
        self._measure_divider = self._divider(options)

        self._variant_buttons = {}
        for name, caption, code, _ in _VARIANTS:
            button = self._choice(name, caption, f"annotationVariant-{name}")
            button.clicked.connect(lambda _checked=False, value=code: self._select_variant(value))
            options.addWidget(button)
            self._variant_buttons[name] = button
        self._kind_buttons = {}
        for _, kinds in _GROUPS.values():
            if len(kinds) < 2:
                continue
            for kind in kinds:
                button = self._choice(kind, _TOOL_NAMES[kind], f"annotationKind-{kind}")
                button.clicked.connect(lambda _checked=False, value=kind: self._select_kind(value))
                options.addWidget(button)
                self._kind_buttons[kind] = button
        self._color_divider = self._divider(options)

        self._colors = QWidget(self._parameters)
        color_row = QHBoxLayout(self._colors)
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(4)
        self._custom = QToolButton(self._colors)
        self._custom.setObjectName("annotationCustomColor")
        self._custom.setAccessibleName("当前颜色，点击输入 HEX")
        self._custom.setFixedSize(28, 27)
        self._custom.setIconSize(QSize(16, 16))
        self._custom.clicked.connect(self._toggle_color_entry)
        color_row.addWidget(self._custom)

        colors = QGridLayout()
        colors.setSpacing(2)
        self._swatches = []
        for i, (caption, color) in enumerate(_PALETTE):
            swatch = QToolButton(self._colors)
            swatch.setObjectName(f"annotationColor{i}")
            swatch.setToolTip(f"{caption} · {color.upper()}")
            swatch.setAccessibleName(f"{caption}色")
            swatch.setFixedSize(13, 12)
            swatch.clicked.connect(lambda _checked=False, value=color: self._change_color(value))
            colors.addWidget(swatch, i // 8, i % 8)
            self._swatches.append(swatch)
        color_row.addLayout(colors)
        options.addWidget(self._colors)

        self._color_entry, color_editor = self._row("annotationColorEntry", 4)
        rows.addWidget(self._color_entry, 0, Qt.AlignmentFlag.AlignLeft)
        hex_label = QLabel("HEX", self._color_entry)
        hex_label.setObjectName("annotationParameterLabel")
        color_editor.addWidget(hex_label)
        self._hex_color = QLineEdit(self._color_entry)
        self._hex_color.setObjectName("annotationHexColor")
        self._hex_color.setAccessibleName("自定义 HEX 颜色")
        self._hex_color.setToolTip("六位 HEX 颜色，例如 #087E86")
        self._hex_color.setMaxLength(7)
        self._hex_color.setFixedSize(78, 22)
        self._hex_color.installEventFilter(self)
        self._hex_color.textChanged.connect(self._validate_hex_color)
        self._hex_color.returnPressed.connect(self._apply_hex_color)
        color_editor.addWidget(self._hex_color)
        self._apply_color = self._entry_button("confirmColor", "使用此颜色")
        self._apply_color.clicked.connect(self._apply_hex_color)
        color_editor.addWidget(self._apply_color)
        close_color = self._entry_button("cancelCapture", "收起颜色输入")
        close_color.clicked.connect(self._hide_color_entry)
        color_editor.addWidget(close_color)
        self._color_entry.hide()

        self.setStyleSheet("""
            QFrame#captureToolStrip {
                background: #192b31; border: 1px solid #496c72; border-radius: 6px;
            }
            QFrame#annotationParameters, QFrame#annotationColorEntry {
                background: #edf6f6; border: 1px solid #a1bfc1; border-radius: 6px;
            }
            QFrame#captureToolDivider { background: #426069; border: 0; }
            QToolButton { background: transparent; border: 1px solid transparent; border-radius: 4px; color: #deeeee; }
            QToolButton:hover, QToolButton:focus { background: #2b4249; border-color: #65828a; }
            QToolButton:checked { background: #285654; border-color: #4e9e92; }
            QToolButton#copyImage { background: #087e86; }
            QToolButton#copyImage:hover, QToolButton#copyImage:focus { background: #11939b; }
            QFrame#annotationParameters QToolButton, QFrame#annotationColorEntry QToolButton {
                background: transparent; color: #183f44;
            }
            QFrame#annotationParameters QToolButton:hover, QFrame#annotationParameters QToolButton:focus,
            QFrame#annotationColorEntry QToolButton:hover, QFrame#annotationColorEntry QToolButton:focus {
                background: #dceced; border-color: #a1bfc1;
            }
            QFrame#annotationParameters QToolButton:checked { background: #c8e9e6; border-color: #70a9a6; }
            QFrame#annotationParameters QFrame#captureToolDivider { background: #b1ccce; }
            QLabel#annotationParameterLabel { color: #3e6165; }
            QFrame#annotationParameters QLineEdit, QLineEdit#annotationHexColor {
                background: #fafdfd; color: #19373c;
                border: 1px solid #a1bfc1; border-radius: 4px; padding-left: 4px;
                selection-background-color: #087e86; }
            QFrame#annotationParameters QLineEdit:focus, QLineEdit#annotationHexColor:focus { border-color: #087e86; }
            """)
        self.set_history(False, False)
        self._parameters.hide()

    def _row(self, name: str, spacing: int) -> tuple[QFrame, QHBoxLayout]:
        frame = QFrame(self)
        frame.setObjectName(name)
        frame.setCursor(Qt.CursorShape.ArrowCursor)
        layout = QHBoxLayout(frame)
        layout.setSpacing(spacing)
        layout.setContentsMargins(4, 3, 4, 3)
        return frame, layout

    @staticmethod
    def _divider(row: QHBoxLayout) -> QFrame:
        divider = QFrame(row.parentWidget())
        divider.setObjectName("captureToolDivider")
        divider.setFixedSize(1, 15)
        row.addSpacing(2)
        row.addWidget(divider)
        row.addSpacing(2)
        return divider

    @staticmethod
    def _button(name: str, caption: str, parent: QFrame) -> QToolButton:
        button = QToolButton(parent)
        button.setObjectName(name)
        button.setToolTip(caption)
        button.setAccessibleName(caption.split(" · ")[0])
        button.setIcon(_tool_icon(name))
        button.setIconSize(QSize(17, 17))
        button.setFixedSize(27, 25)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return button

    def _action(self, row: QHBoxLayout, name: str, caption: str, signal) -> QToolButton:
        button = self._button(name, caption, row.parentWidget())
        button.clicked.connect(lambda _checked=False: signal.emit())
        row.addWidget(button)
        return button

    def _number_field(self, name: str) -> _NumberField:
        field = _NumberField(self._parameters)
        field.setObjectName(name)
        field.setFixedSize(54, 25)
        field.installEventFilter(self)
        return field

    def _choice(self, icon: str, caption: str, name: str) -> QToolButton:
        button = self._entry_button(icon, caption, self._parameters)
        button.setObjectName(name)
        button.setCheckable(True)
        return button

    def _entry_button(self, icon: str, caption: str, parent: QWidget | None = None) -> QToolButton:
        button = QToolButton(parent if parent is not None else self._color_entry)
        button.setIcon(_tool_icon(icon, "#24575b"))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(caption)
        button.setAccessibleName(caption)
        button.setFixedSize(24, 25)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return button

    def _read_style(self, kind: str) -> AnnotationStyle:
        default = _DEFAULTS[kind]
        base = f"{_PREFIX}/{kind}"
        measures = [_MEASURE.get(kind, _LINE_WIDTH)]
        if kind == "mosaic_brush":
            measures.append(_CELL_SIZE)
        fields = {"width": default.width, "size": default.size}
        for field, _, minimum, maximum in measures:
            value = self._read_preference(f"{base}/{field}", (minimum, maximum))
            if value is not None:
                fields[field] = value

        color = self._read_preference(f"{base}/color", None) or default.color
        variant = self._settings.value(f"{base}/variant", default.variant, str)
        if all(variant != code or kind not in kinds for _, _, code, kinds in _VARIANTS):
            variant = default.variant
        return AnnotationStyle(color, **fields, variant=variant)

    def _read_preference(self, key: str, limits: tuple[int, int] | None) -> str | int | None:
        value = self._settings.value(key)
        if value is None:
            return None
        if limits is None:
            color = QColor(str(value))
            return color.name() if color.isValid() else None
        try:
            return max(limits[0], min(limits[1], int(value)))
        except (TypeError, ValueError):
            return None

    def _load_shared_preferences(self) -> None:
        for group, kinds, fields in _SHARED_PREFERENCES:
            last = self._last_kind[group]
            migration_order = (last, *(kind for kind in kinds if kind != last)) if last in kinds else kinds
            for field, limits in fields.items():
                key = f"{_PREFIX}/shared/{group}/{field}"
                value = self._read_preference(key, limits)
                if value is None:
                    # Import the last-used subtype first, skipping unconfigured or damaged entries.
                    for kind in migration_order:
                        value = self._read_preference(f"{_PREFIX}/{kind}/{field}", limits)
                        if value is not None:
                            break
                    if value is None:
                        value = getattr(_DEFAULTS[kinds[0]], field)
                    self._settings.setValue(key, value)
                for kind in kinds:
                    self._styles[kind] = replace(self._styles[kind], **{field: value})

    def style_for(self, kind: str) -> AnnotationStyle:
        return self._styles[kind]

    @property
    def _kind(self) -> str | None:
        return self._last_kind[self._group] if self._group else None

    def _select_group(self, group: str) -> None:
        self._group = None if self._group == group else group
        self._refresh_mode()
        self.tool_changed.emit(self._kind or "")

    def _select_kind(self, kind: str) -> None:
        if self._group is None or kind not in _GROUPS[self._group][1]:
            return
        if kind == self._kind:
            self._kind_buttons[kind].setChecked(True)
            return
        self._last_kind[self._group] = kind
        self._settings.setValue(f"{_PREFIX}/group/{self._group}", kind)
        self._refresh_mode()
        self.tool_changed.emit(kind)

    def _refresh_mode(self) -> None:
        self._hide_color_entry()
        for group, button in self._buttons.items():
            button.setChecked(group == self._group)
        if self._group is None:
            self._parameters.hide()
        else:
            _, kinds = _GROUPS[self._group]
            for kind, button in self._kind_buttons.items():
                button.setVisible(kind in kinds)
                button.setChecked(kind == self._kind)
            self.load_mark_style(self._kind, self.style_for(self._kind))
            self._parameters.show()
        self.layout().activate()
        self.layout_changed.emit()

    def load_mark_style(self, kind: str, style: AnnotationStyle) -> None:
        if kind != self._kind:
            return
        self._shown_style = style
        has_variants = False
        for name, _, variant, kinds in _VARIANTS:
            button = self._variant_buttons[name]
            available = kind in kinds
            has_variants |= available
            button.setVisible(available)
            button.setChecked(available and style.variant == variant)

        brush = kind == "mosaic_brush"
        self._width_label.setVisible(brush)
        self._cell_label.setVisible(brush)
        self._mosaic_size.setVisible(brush)
        self._set_number(self._measure, _MEASURE.get(kind, _LINE_WIDTH), style, kind not in {"mosaic", "blur"})
        if brush:
            self._set_number(self._mosaic_size, _CELL_SIZE, style, False)

        colored = kind not in _COLORLESS
        has_kinds = self._group is not None and len(_GROUPS[self._group][1]) > 1
        self._measure_divider.setVisible(has_kinds or has_variants)
        self._color_divider.setVisible(colored)
        self._colors.setVisible(colored)
        if not colored:
            return
        current_color = QColor(style.color)
        contrast = "#163a40" if current_color.lightness() > 145 else "#ffffff"
        self._custom.setIcon(_tool_icon("editColor", contrast))
        self._custom.setToolTip(f"当前颜色 {style.color.upper()} · 点击输入 HEX")
        self._custom.setStyleSheet(
            f"QToolButton {{ background: {style.color}; border: 1px solid #63898a; border-radius: 5px; }}"
            "QToolButton:hover, QToolButton:focus { border: 2px solid #087e86; }"
        )
        for swatch, (_, color) in zip(self._swatches, _PALETTE):
            border = "2px solid #20585b" if color == style.color else "1px solid #b1ccce"
            swatch.setStyleSheet(
                f"QToolButton {{ background: {color}; border: {border}; border-radius: 3px; }}"
                "QToolButton:hover, QToolButton:focus { border: 2px solid #087e86; }"
            )

    @staticmethod
    def _set_number(field: _NumberField, measure: tuple[str, str, int, int], style: AnnotationStyle, show_unit: bool) -> None:
        key, description, minimum, maximum = measure
        with QSignalBlocker(field):
            field.setToolTip(f"{description} · 可输入数值或滚动调整")
            field.setAccessibleName(description)
            field.minimum, field.maximum = minimum, maximum
            field.unit = " px" if show_unit else ""
            field.setValue(getattr(style, key))

    def _select_variant(self, variant: str) -> None:
        # A click on the active icon must keep it selected.
        if variant == self._shown_style.variant and self._kind is not None:
            self.load_mark_style(self._kind, self._shown_style)
        else:
            self._change_style(variant=variant)

    def _change_measure(self, value: int) -> None:
        field = _MEASURE.get(self._kind, ("width",))[0]
        self._change_style(**{field: value})

    def _toggle_color_entry(self) -> None:
        if not self._color_entry.isHidden():
            self._hide_color_entry()
            return
        self._hex_color.setText(self._shown_style.color.upper())
        self._color_entry.show()
        self.layout().activate()
        self.layout_changed.emit()
        self._hex_color.setFocus()
        self._hex_color.selectAll()

    def _hide_color_entry(self) -> None:
        if not self._color_entry.isHidden():
            had_focus = self._hex_color.hasFocus()
            self._color_entry.hide()
            self.layout().activate()
            self.layout_changed.emit()
            if had_focus and self.parentWidget() is not None:
                self.parentWidget().setFocus()

    @staticmethod
    def _parse_hex_color(value: str) -> str | None:
        match = re.fullmatch(r"#?([0-9a-fA-F]{6})", value.strip())
        return f"#{match[1].lower()}" if match else None

    def _validate_hex_color(self, value: str) -> None:
        self._apply_color.setEnabled(self._parse_hex_color(value) is not None)

    def _apply_hex_color(self) -> None:
        color = self._parse_hex_color(self._hex_color.text())
        if color is not None:
            self._change_color(color)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                event.accept()
                return True
            if event.key() in (Qt.Key.Key_C, Qt.Key.Key_P):
                event.accept()
                return True
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if watched is self._hex_color:
                self._hide_color_entry()
            elif self._kind is not None:
                self.load_mark_style(self._kind, self._shown_style)
                if self.parentWidget() is not None:
                    self.parentWidget().setFocus()
            return True
        return super().eventFilter(watched, event)

    def _change_color(self, color: str) -> None:
        self._hide_color_entry()
        self._change_style(color=color)

    def _change_style(self, **options) -> None:
        kind = self._kind
        if kind is None:
            return
        style = replace(self._shown_style, **options)
        # The toolbar can be showing an existing mark. Only explicit edits update the preferences.
        for field, value in options.items():
            shared = _SHARED_FIELDS.get((kind, field))
            if shared is None:
                key, members = f"{_PREFIX}/{kind}/{field}", (kind,)
            else:
                group, members = shared
                key = f"{_PREFIX}/shared/{group}/{field}"
            if getattr(self._styles[kind], field) != value:
                self._settings.setValue(key, value)
                for member in members:
                    self._styles[member] = replace(self._styles[member], **{field: value})
        if style == self._shown_style:
            return
        self.load_mark_style(kind, style)
        self.options_changed.emit(kind, style)

    def set_history(self, can_undo: bool, can_redo: bool) -> None:
        self._undo.setEnabled(can_undo)
        self._redo.setEnabled(can_redo)

    def reset(self) -> None:
        self._group = None
        self.set_history(False, False)
        self._refresh_mode()
