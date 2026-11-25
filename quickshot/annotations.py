"""Editable screenshot marks in logical coordinates and native-resolution exports."""

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from math import ceil, floor, hypot

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
    QRegion,
    QTransform,
)


_DEFAULT_COLOR = "#e3494e"
_EFFECTS = {"mosaic", "blur"}
_SHAPES = {"rectangle", "ellipse"}
_EFFECT_CACHE_SIZE = 12


@dataclass(frozen=True)
class AnnotationStyle:
    color: str = _DEFAULT_COLOR
    width: int = 3
    size: int = 22
    variant: str = "solid"


@dataclass(frozen=True)
class Mark:
    kind: str
    points: tuple[QPointF, ...]
    style: AnnotationStyle
    text: str = ""


@dataclass(frozen=True)
class _Edit:
    index: int
    before: Mark | None
    after: Mark
    offset: QPointF


def _translated(mark: Mark, delta: QPointF) -> Mark:
    if delta.isNull():
        return mark
    return replace(mark, points=tuple(point + delta for point in mark.points))


def _rect(mark: Mark) -> QRectF:
    if len(mark.points) < 2:
        return QRectF()
    return QRectF(mark.points[0], mark.points[-1]).normalized()


def _width(mark: Mark) -> float:
    return float(max(1, mark.style.width))


def annotation_font(style: AnnotationStyle) -> QFont:
    """Use the same glyph metrics for editing and rendering a text annotation."""
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(max(1, style.size))
    font.setWeight(QFont.Weight.DemiBold)
    return font


def _text_path(mark: Mark) -> QPainterPath:
    path = QPainterPath()
    font = annotation_font(mark.style)
    metrics = QFontMetricsF(font)
    anchor = mark.points[0]
    baseline = anchor.y() + metrics.ascent()
    for line in mark.text.splitlines():
        path.addText(QPointF(anchor.x(), baseline), font, line)
        baseline += metrics.lineSpacing()
    return path


def _arrow_paths(mark: Mark) -> tuple[QPainterPath, QPainterPath]:
    stroke, fill = QPainterPath(), QPainterPath()
    if len(mark.points) < 2:
        return stroke, fill
    start, end = mark.points[0], mark.points[-1]
    offset = end - start
    length = hypot(offset.x(), offset.y())
    if length == 0:
        return stroke, fill

    direction = offset / length
    two_heads = mark.style.variant == "double"
    open_head = mark.style.variant == "open"
    head_length = min(max(9.0, _width(mark) * 4), length * (0.4 if two_heads else 0.65))
    wing = QPointF(-direction.y(), direction.x()) * (head_length * 0.48)
    stroke.moveTo(start + direction * head_length * 0.7 if two_heads else start)
    stroke.lineTo(end if open_head else end - direction * head_length * 0.7)

    heads = [(end, end - direction * head_length)]
    if two_heads:
        heads.append((start, start + direction * head_length))
    for tip, base in heads:
        if open_head:
            stroke.moveTo(base + wing)
            stroke.lineTo(tip)
            stroke.lineTo(base - wing)
        else:
            fill.addPolygon(QPolygonF([base + wing, tip, base - wing]))
            fill.closeSubpath()
    return stroke, fill


def _polyline(points: tuple[QPointF, ...], clip: QRectF | None, margin: float) -> QPainterPath:
    path = QPainterPath()
    if clip is None or len(points) < 96:
        path.addPolygon(QPolygonF(points))
        return path

    # The rasterizer still strokes off-screen segments of long freehand paths.
    # Keep whole connected runs near the clip so their joins and caps match.
    left, top, right, bottom = clip.adjusted(-margin, -margin, margin, margin).getCoords()
    previous_x, previous_y = points[0].x(), points[0].y()
    run = None
    for index, point in enumerate(points[1:], 1):
        x, y = point.x(), point.y()
        outside = (
            (x < left and previous_x < left) or (x > right and previous_x > right)
            or (y < top and previous_y < top) or (y > bottom and previous_y > bottom)
        )
        if outside:
            if run is not None:
                path.addPolygon(QPolygonF(points[run:index]))
                run = None
        elif run is None:
            run = index - 1
        previous_x, previous_y = x, y
    if run is not None:
        path.addPolygon(QPolygonF(points[run:]))
    return path


def _paths(mark: Mark, clip: QRectF | None = None) -> tuple[QPainterPath, QPainterPath]:
    stroke, fill = QPainterPath(), QPainterPath()
    if not mark.points:
        return stroke, fill
    if mark.kind == "text":
        return stroke, _text_path(mark)
    if mark.kind in _SHAPES:
        bounds = _rect(mark)
        if not bounds.isEmpty():
            if mark.kind == "rectangle":
                stroke.addRect(bounds)
            else:
                stroke.addEllipse(bounds)
        return stroke, stroke if mark.style.variant == "filled" else fill
    if mark.kind == "arrow":
        return _arrow_paths(mark)

    points = mark.points
    if mark.kind == "line":
        points = (points[0], points[-1])
    if len(points) > 1 and (points[0] != points[-1] or any(point != points[0] for point in points[1:-1])):
        stroke = _polyline(points, clip, _width(mark) / 2 + 2)
    if stroke.isEmpty():
        radius = _width(mark) / 2
        fill.addEllipse(points[0], radius, radius)
    return stroke, fill


def _pen(ink: QColor | QBrush, width: float) -> QPen:
    pen = QPen(ink, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _hit(point: QPointF, mark: Mark, tolerance: float = 3.5) -> bool:
    if mark.kind in _EFFECTS:
        return _rect(mark).contains(point)
    stroke, fill = _paths(mark)
    if mark.kind == "text":
        return not fill.isEmpty() and fill.boundingRect().adjusted(
            -tolerance, -tolerance, tolerance, tolerance
        ).contains(point)
    if fill.contains(point):
        return True
    stroker = QPainterPathStroker(_pen(QColor(), _width(mark) + tolerance * 2))
    if not stroke.isEmpty() and stroker.createStroke(stroke).contains(point):
        return True
    if not fill.isEmpty() and tolerance:
        stroker.setWidth(tolerance * 2)
        return stroker.createStroke(fill).contains(point)
    return False


def _scale(source: QImage, viewport: QSize) -> tuple[float, float]:
    return source.width() / viewport.width(), source.height() / viewport.height()


def _pixel_bounds(rect: QRectF, source: QImage, scale: tuple[float, float]) -> QRect:
    if rect.isEmpty():
        return QRect()
    scale_x, scale_y = scale
    left, top = floor(rect.left() * scale_x), floor(rect.top() * scale_y)
    right, bottom = ceil(rect.right() * scale_x), ceil(rect.bottom() * scale_y)
    return QRect(left, top, right - left, bottom - top).intersected(source.rect())


def _logical_bounds(rect: QRect, scale: tuple[float, float]) -> QRectF:
    scale_x, scale_y = scale
    return QRectF(rect.x() / scale_x, rect.y() / scale_y, rect.width() / scale_x, rect.height() / scale_y)


def _ink_bounds(stroke: QPainterPath, fill: QPainterPath, margin: float) -> QRectF:
    stroked_bounds = (
        stroke.controlPointRect().adjusted(-margin, -margin, margin, margin)
        if not stroke.isEmpty() else QRectF()
    )
    filled_bounds = fill.controlPointRect().adjusted(-2, -2, 2, 2) if not fill.isEmpty() else QRectF()
    return stroked_bounds.united(filled_bounds)


def _mark_bounds(mark: Mark) -> QRectF:
    if not mark.points:
        return QRectF()
    if mark.kind in _EFFECTS:
        return _rect(mark)

    margin = _width(mark) / 2 + 2
    if mark.kind in _SHAPES:
        bounds = _rect(mark)
        return bounds.adjusted(-margin, -margin, margin, margin) if not bounds.isEmpty() else bounds
    if mark.kind in {"arrow", "text"}:
        stroke, fill = _paths(mark)
        return _ink_bounds(stroke, fill, margin)

    bounds = QPolygonF(mark.points).boundingRect()
    return bounds.adjusted(-margin, -margin, margin, margin)


def _average_cells(source: QImage, bounds: QRect, columns: int, rows: int) -> QImage:
    cells = source.copy(bounds).scaled(
        columns, rows, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    cells.setDevicePixelRatio(1.0)
    return cells


class AnnotationLayer:
    def __init__(self) -> None:
        self._marks: list[Mark] = []
        self._mark_extents: list[QRectF] = []
        self._undo: list[_Edit] = []
        self._redo: list[_Edit] = []
        self._offset = QPointF()
        self._render_key: tuple | None = None
        self._rendered = QImage()
        self._render_bounds = QRect()
        self._rendered_count = 0
        self._effects: OrderedDict[tuple, QImage] = OrderedDict()

    def clear(self) -> None:
        self._marks.clear()
        self._mark_extents.clear()
        self._undo.clear()
        self._redo.clear()
        self._offset = QPointF()
        self._effects.clear()
        self._invalidate()

    def _invalidate(self) -> None:
        self._render_key = None
        self._rendered = QImage()
        self._render_bounds = QRect()
        self._rendered_count = 0

    @staticmethod
    def mark_bounds(mark: Mark) -> QRectF:
        """Return the local area affected by a mark, including stroke edges."""
        return _mark_bounds(mark)

    def add(self, mark: Mark) -> int:
        index = len(self._marks)
        self._marks.append(mark)
        self._mark_extents.append(_mark_bounds(mark))
        self._undo.append(_Edit(index, None, mark, self._offset))
        self._redo.clear()
        return index

    def get(self, index: int) -> Mark:
        return self._marks[index]

    def replace(self, index: int, mark: Mark) -> bool:
        if not 0 <= index < len(self._marks) or self._marks[index] == mark:
            return False
        self._undo.append(_Edit(index, self._marks[index], mark, self._offset))
        self._redo.clear()
        self._marks[index] = mark
        self._mark_extents[index] = _mark_bounds(mark)
        self._invalidate()
        return True

    def hit_test(self, point: QPointF, kind: str) -> int | None:
        for index in range(len(self._marks) - 1, -1, -1):
            mark = self._marks[index]
            if mark.kind == kind and _hit(point, mark):
                return index
            if mark.kind == "eraser" and _hit(point, mark, tolerance=0):
                return None
        return None

    def translate(self, delta: QPointF) -> None:
        """Move the active marks with the selection, retaining edit history."""
        if delta.isNull() or not (self._marks or self._redo):
            return
        self._offset = self._offset + delta
        self._marks = [_translated(mark, delta) for mark in self._marks]
        self._mark_extents = [extent.translated(delta) for extent in self._mark_extents]
        self._invalidate()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        edit = self._undo.pop()
        if edit.before is None:
            self._marks.pop(edit.index)
            self._mark_extents.pop(edit.index)
        else:
            mark = _translated(edit.before, self._offset - edit.offset)
            self._marks[edit.index] = mark
            self._mark_extents[edit.index] = _mark_bounds(mark)
        self._redo.append(edit)
        self._invalidate()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        edit = self._redo.pop()
        mark = _translated(edit.after, self._offset - edit.offset)
        if edit.before is None:
            self._marks.insert(edit.index, mark)
            self._mark_extents.insert(edit.index, _mark_bounds(mark))
        else:
            self._marks[edit.index] = mark
            self._mark_extents[edit.index] = _mark_bounds(mark)
        self._undo.append(edit)
        self._invalidate()
        return True

    def has_annotations(self) -> bool:
        return bool(self._marks)

    def _effect(self, source: QImage, bounds: QRect, mark: Mark, scale: tuple[float, float]) -> QImage:
        size = max(2, mark.style.size)
        key = (source.cacheKey(), bounds.getRect(), mark.kind, size, scale)
        cached = self._effects.get(key)
        if cached is not None:
            self._effects.move_to_end(key)
            return cached

        # Both effects average the original pixels when reducing the image. Their
        # different interpolation on enlargement preserves or softens the cells.
        width = max(1, ceil(bounds.width() / (size * scale[0])))
        height = max(1, ceil(bounds.height() / (size * scale[1])))
        return self._cache_effect(key, _average_cells(source, bounds, width, height))

    def _cache_effect(self, key: tuple, patch: QImage) -> QImage:
        self._effects[key] = patch
        if len(self._effects) > _EFFECT_CACHE_SIZE:
            self._effects.popitem(last=False)
        return patch

    def _mosaic_cells(self, source: QImage, bounds: QRect, cell_width: int, cell_height: int) -> QImage:
        key = (source.cacheKey(), bounds.getRect(), "mosaic_brush", cell_width, cell_height)
        cached = self._effects.get(key)
        if cached is not None:
            self._effects.move_to_end(key)
            return cached

        columns, edge_width = divmod(bounds.width(), cell_width)
        rows, edge_height = divmod(bounds.height(), cell_height)
        if not edge_width and not edge_height:
            return self._cache_effect(key, _average_cells(source, bounds, columns, rows))

        # Viewport edges may end with a partial cell. Sample it separately so it
        # cannot shift the rest of the grid when a dirty region reaches the edge.
        cells = QImage(columns + bool(edge_width), rows + bool(edge_height), QImage.Format.Format_ARGB32_Premultiplied)
        regular_width, regular_height = columns * cell_width, rows * cell_height
        painter = QPainter(cells)
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            for dx, width, count_x in ((0, regular_width, columns), (regular_width, edge_width, 1)):
                for dy, height, count_y in ((0, regular_height, rows), (regular_height, edge_height, 1)):
                    if width and height:
                        area = QRect(bounds.x() + dx, bounds.y() + dy, width, height)
                        painter.drawImage(dx // cell_width, dy // cell_height, _average_cells(source, area, count_x, count_y))
        finally:
            painter.end()
        return self._cache_effect(key, cells)

    def _mosaic_ink(
        self, source: QImage, extent: QRectF, cell_size: int, scale: tuple[float, float]
    ) -> QBrush | None:
        visible = _pixel_bounds(extent, source, scale)
        if visible.isEmpty():
            return None
        scale_x, scale_y = scale
        cell_width = max(1, round(max(2, cell_size) * scale_x))
        cell_height = max(1, round(max(2, cell_size) * scale_y))
        left = visible.x() // cell_width * cell_width
        top = visible.y() // cell_height * cell_height
        right = min(source.width(), (visible.right() // cell_width + 1) * cell_width)
        bottom = min(source.height(), (visible.bottom() // cell_height + 1) * cell_height)
        snapped = QRect(left, top, right - left, bottom - top)
        ink = QBrush(self._mosaic_cells(source, snapped, cell_width, cell_height))
        # The texture origin is always on the source frame's grid, even when
        # composing into a clipped cache tile or the final screenshot crop.
        transform = QTransform.fromTranslate(left / scale_x, top / scale_y)
        transform.scale(cell_width / scale_x, cell_height / scale_y)
        ink.setTransform(transform)
        return ink

    def _paint_mark(
        self, painter: QPainter, mark: Mark, source: QImage, scale: tuple[float, float],
        clip: QRectF | None = None,
    ) -> None:
        painter.save()
        try:
            if mark.kind in _EFFECTS:
                rect = _rect(mark)
                bounds = _pixel_bounds(rect, source, scale)
                if not bounds.isEmpty():
                    painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, mark.kind == "blur")
                    painter.drawImage(_logical_bounds(bounds, scale), self._effect(source, bounds, mark, scale))
                return

            stroke, fill = _paths(mark, clip)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if mark.kind == "mosaic_brush":
                extent = _ink_bounds(stroke, fill, _width(mark) / 2 + 2)
                if clip is not None:
                    extent = extent.intersected(clip)
                ink = self._mosaic_ink(source, extent, mark.style.size, scale)
                if ink is not None:
                    painter.setBrushOrigin(QPointF())
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                    if not fill.isEmpty():
                        painter.fillPath(fill, ink)
                    if not stroke.isEmpty():
                        painter.strokePath(stroke, _pen(ink, _width(mark)))
                return

            color = QColor(mark.style.color)
            if not color.isValid():
                color = QColor(_DEFAULT_COLOR)
            if mark.kind == "eraser":
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
                color = QColor(Qt.GlobalColor.white)
            elif mark.kind == "highlight":
                color.setAlphaF(color.alphaF() * 0.38)
            if not fill.isEmpty():
                painter.fillPath(fill, color)
            if not stroke.isEmpty():
                painter.strokePath(stroke, _pen(color, _width(mark)))
        finally:
            painter.restore()

    def _paint_overlay(
        self,
        image: QImage,
        source: QImage,
        bounds: QRect,
        selection: QRectF,
        scale: tuple[float, float],
        marks: Iterable[Mark],
    ) -> None:
        painter = QPainter(image)
        try:
            painter.translate(-bounds.x(), -bounds.y())
            painter.scale(*scale)
            painter.setClipRect(selection)
            clip = _logical_bounds(bounds, scale).intersected(selection)
            for mark in marks:
                self._paint_mark(painter, mark, source, scale, clip)
        finally:
            painter.end()

    def _committed_overlay(
        self, source: QImage, selection: QRectF, scale: tuple[float, float]
    ) -> tuple[QRect, QImage]:
        key = (source.cacheKey(), selection.getRect(), scale)
        if key != self._render_key:
            self._invalidate()
            self._render_key = key
        if self._rendered_count == len(self._marks):
            return self._render_bounds, self._rendered

        # An appended mark only needs painting once. Grow the cached image if it
        # reaches past the existing ink, and reuse its already composed pixels.
        bounds = self._render_bounds
        additions = self._marks[self._rendered_count:]
        for mark, extent in zip(additions, self._mark_extents[self._rendered_count:]):
            if mark.kind != "eraser":
                bounds = bounds.united(_pixel_bounds(selection.intersected(extent), source, scale))
        if bounds != self._render_bounds:
            expanded = QImage(bounds.size(), QImage.Format.Format_ARGB32_Premultiplied)
            expanded.fill(Qt.GlobalColor.transparent)
            if not self._rendered.isNull():
                painter = QPainter(expanded)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                painter.drawImage(self._render_bounds.topLeft() - bounds.topLeft(), self._rendered)
                painter.end()
            self._render_bounds, self._rendered = bounds, expanded
        if not bounds.isEmpty():
            self._paint_overlay(self._rendered, source, bounds, selection, scale, additions)
        self._rendered_count = len(self._marks)
        return bounds, self._rendered

    @staticmethod
    def _blit(
        painter: QPainter, image: QImage, bounds: QRect, part: QRect, scale: tuple[float, float]
    ) -> None:
        if not part.isEmpty():
            painter.drawImage(
                _logical_bounds(part, scale), image,
                QRectF(part.translated(-bounds.x(), -bounds.y())),
            )

    def _preview_patch(
        self,
        source: QImage,
        region: QRect,
        selection: QRectF,
        scale: tuple[float, float],
        replacement: tuple[int, Mark],
        replacement_extent: QRectF,
    ) -> QImage:
        patch = QImage(region.size(), QImage.Format.Format_ARGB32_Premultiplied)
        patch.fill(Qt.GlobalColor.transparent)
        index, edited = replacement
        logical = _logical_bounds(region, scale)
        marks = (
            (edited if i == index else mark)
            for i, (mark, extent) in enumerate(zip(self._marks, self._mark_extents))
            if logical.intersects(replacement_extent if i == index else extent)
        )
        self._paint_overlay(patch, source, region, selection, scale, marks)
        return patch

    def draw(
        self,
        painter: QPainter,
        source: QImage,
        selection: QRectF,
        viewport: QSize,
        *,
        preview: Mark | None = None,
        replacement: tuple[int, Mark] | None = None,
    ) -> None:
        if source.isNull() or viewport.isEmpty() or (not self._marks and preview is None):
            return
        scale = _scale(source, viewport)
        clip = selection.intersected(painter.clipBoundingRect()) if painter.hasClipping() else None
        visible = selection if clip is None else clip
        visible_pixels = _pixel_bounds(visible, source, scale)
        if visible_pixels.isEmpty():
            return

        raster_preview = preview if preview is not None and preview.kind in {"eraser", "mosaic_brush"} else None
        painter.save()
        try:
            painter.setClipRect(selection, Qt.ClipOperation.IntersectClip)
            if self._marks:
                self._draw_committed(
                    painter, source, selection, scale, visible_pixels, raster_preview, replacement,
                )
            if preview is not None and preview.kind != "eraser" and (not self._marks or raster_preview is None):
                self._paint_mark(painter, preview, source, scale, clip)
        finally:
            painter.restore()

    def _draw_committed(
        self,
        painter: QPainter,
        source: QImage,
        selection: QRectF,
        scale: tuple[float, float],
        visible: QRect,
        preview: Mark | None,
        replacement: tuple[int, Mark] | None,
    ) -> None:
        bounds, committed = self._committed_overlay(source, selection, scale)
        visible_ink = visible.intersected(bounds)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            if preview is None and replacement is None:
                self._blit(painter, committed, bounds, visible_ink, scale)
                return

            # Recompose only the areas a live edit can change. Keeping the old
            # and new extents separate avoids redrawing the distance between them.
            patches = QRegion()
            replacement_extent = QRectF()
            if replacement is not None:
                index, mark = replacement
                replacement_extent = _mark_bounds(mark)
                for extent in (self._mark_extents[index], replacement_extent):
                    part = visible.intersected(_pixel_bounds(extent, source, scale))
                    patches = patches.united(QRegion(part))
            if preview is not None:
                affected = visible_ink if preview.kind == "eraser" else visible
                part = affected.intersected(_pixel_bounds(_mark_bounds(preview), source, scale))
                patches = patches.united(QRegion(part))

            for part in QRegion(visible_ink).subtracted(patches):
                self._blit(painter, committed, bounds, part, scale)
            for part in patches:
                if replacement is None:
                    if committed.isNull():
                        patch = QImage(part.size(), QImage.Format.Format_ARGB32_Premultiplied)
                        patch.fill(Qt.GlobalColor.transparent)
                    else:
                        patch = committed.copy(part.translated(-bounds.x(), -bounds.y()))
                else:
                    patch = self._preview_patch(source, part, selection, scale, replacement, replacement_extent)
                if preview is not None:
                    self._paint_overlay(patch, source, part, selection, scale, [preview])
                painter.drawImage(_logical_bounds(part, scale), patch)
        finally:
            painter.restore()

    def flatten(
        self,
        source: QImage,
        selection: QRectF,
        crop_pixels: QRect,
        viewport: QSize,
        *,
        preview: Mark | None = None,
        replacement: tuple[int, Mark] | None = None,
    ) -> QImage:
        bounds = crop_pixels.intersected(source.rect())
        if bounds.isEmpty() or viewport.isEmpty():
            return QImage()
        crop = source.copy(bounds)
        crop.setDevicePixelRatio(1.0)
        if not self._marks and preview is None:
            return crop

        crop = crop.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(crop)
        try:
            painter.translate(-bounds.x(), -bounds.y())
            painter.scale(*_scale(source, viewport))
            self.draw(painter, source, selection, viewport, preview=preview, replacement=replacement)
        finally:
            painter.end()
        return crop
