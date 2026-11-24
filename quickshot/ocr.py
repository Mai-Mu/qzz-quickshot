"""Run bundled Tesseract and decode its selectable text geometry."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from threading import Event
from time import monotonic
from unicodedata import east_asian_width
from xml.etree import ElementTree

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage


_LANGUAGES = ("chi_sim", "eng")
_BOX = re.compile(r"\b(?:x_bboxes|bbox)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)")
_LINE_CLASSES = {"ocr_line", "ocr_header", "ocr_caption", "ocr_textfloat"}
_PROCESS_TIMEOUT_SECONDS = 25


class OcrError(RuntimeError):
    """OCR could not finish on the current computer or image."""


@dataclass(frozen=True, slots=True)
class TextBox:
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True, slots=True)
class Glyph:
    text: str
    box: TextBox
    prefix: str = ""


@dataclass(frozen=True, slots=True)
class TextLine:
    box: TextBox
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class OcrText:
    glyphs: tuple[Glyph, ...] = ()
    lines: tuple[TextLine, ...] = ()

    def selected_text(self, start: int, end: int) -> str:
        selected = self.glyphs[start:end]
        if not selected:
            return ""
        return selected[0].text + "".join(glyph.prefix + glyph.text for glyph in selected[1:])


def _box(element: ElementTree.Element) -> TextBox | None:
    match = _BOX.search(element.get("title", ""))
    if match is None:
        return None
    box = TextBox(*(int(value) for value in match.groups()))
    return box if box.right > box.left and box.bottom > box.top else None


def _word_glyphs(word: ElementTree.Element) -> list[Glyph]:
    glyphs = []
    for element in word.iter():
        if "ocrx_cinfo" not in element.get("class", "").split():
            continue
        box = _box(element)
        text = "".join(element.itertext()).strip()
        if box is not None and text:
            glyphs.append(Glyph(text, box))
    if glyphs:
        return glyphs

    box = _box(word)
    text = "".join(word.itertext()).strip()
    return [Glyph(text, box)] if box is not None and text else []


def _word_space(previous: Glyph, current: Glyph) -> str:
    wide_characters = sum(east_asian_width(char) in ("W", "F") for char in (previous.text[-1], current.text[0]))
    if wide_characters:
        # Tesseract may split a continuous CJK phrase into multiple OCR words.
        gap = current.box.left - previous.box.right
        heights = (previous.box.bottom - previous.box.top, current.box.bottom - current.box.top)
        space_threshold = 0.75 * max(heights) if wide_characters == 2 else 0.4 * min(heights)
        return " " if gap >= space_threshold else ""
    return " "


def parse_hocr(document: bytes) -> OcrText:
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as error:
        raise OcrError("无法读取文字识别结果") from error

    glyphs: list[Glyph] = []
    lines: list[TextLine] = []
    for element in root.iter():
        if not (_LINE_CLASSES & set(element.get("class", "").split())):
            continue

        start = len(glyphs)
        for word in element.iter():
            if "ocrx_word" not in word.get("class", "").split():
                continue
            letters = _word_glyphs(word)
            if not letters:
                continue
            first = letters[0]
            prefix = _word_space(glyphs[-1], first) if len(glyphs) > start else "\n"
            glyphs.append(Glyph(first.text, first.box, prefix))
            glyphs.extend(letters[1:])

        if len(glyphs) > start:
            line = glyphs[start:]
            bounds = TextBox(
                min(glyph.box.left for glyph in line),
                min(glyph.box.top for glyph in line),
                max(glyph.box.right for glyph in line),
                max(glyph.box.bottom for glyph in line),
            )
            lines.append(TextLine(bounds, start, len(glyphs)))

    return OcrText(tuple(glyphs), tuple(lines))


def _find_engine() -> tuple[Path, tuple[str, ...]]:
    bundle = getattr(sys, "_MEIPASS", None)
    root = Path(bundle) if bundle else Path(__file__).resolve().parent.parent
    name = "tesseract.exe" if os.name == "nt" else "tesseract"
    executable = root / "ocr_runtime" / name
    if executable.is_file():
        models = executable.parent / "tessdata"
        if not all((models / f"{language}.traineddata").is_file() for language in _LANGUAGES):
            raise OcrError("本地 OCR 缺少简体中文或英文模型")
        return executable, ("--tessdata-dir", "tessdata")

    developer_engine = shutil.which(name) if not bundle else None
    if developer_engine is None:
        raise OcrError("找不到本地 OCR 引擎")
    return Path(developer_engine).resolve(), ()


def recognize(image: QImage, cancelled: Event | None = None) -> OcrText:
    if cancelled is not None and cancelled.is_set():
        return OcrText()
    executable, model_arguments = _find_engine()

    # Stream pixels to the local OCR child without writing screenshots to disk.
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "BMP"):
        raise OcrError("无法读取贴图")
    image_bytes = bytes(buffer.data())
    buffer.close()
    if cancelled is not None and cancelled.is_set():
        return OcrText()

    arguments = [
        str(executable), "stdin", "stdout", *model_arguments,
        "-l", "+".join(_LANGUAGES), "--psm", "11",
        "-c", "tessedit_create_hocr=1", "-c", "hocr_char_boxes=1",
    ]
    environment = os.environ.copy()
    environment["OMP_THREAD_LIMIT"] = "2"
    try:
        with subprocess.Popen(
            arguments,
            cwd=executable.parent,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:
            deadline = monotonic() + _PROCESS_TIMEOUT_SECONDS
            pending_input = image_bytes
            while True:
                try:
                    stdout, _ = process.communicate(input=pending_input, timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    pending_input = None
                    stopped = cancelled is not None and cancelled.is_set()
                    if stopped or monotonic() >= deadline:
                        process.kill()
                        process.communicate()
                        if stopped:
                            return OcrText()
                        raise OcrError("文字识别超时")

            if process.returncode != 0:
                raise OcrError("本地 OCR 无法识别这张图片")
    except OSError as error:
        raise OcrError("无法启动本地 OCR 引擎") from error

    return parse_hocr(stdout)
