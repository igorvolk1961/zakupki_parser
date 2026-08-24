"""Разбиение текста ТЗ на структурированные чанки (RAG-анализ).

Чанк не пересекает границу раздела ТЗ: текст делится по заголовкам разделов
(Markdown-заголовки ``#/##/…`` — результат конвертации MarkItDown — либо
эвристика по сырому тексту: нумерованные «N.», «N.N», маркеры «Раздел»,
«Общие положения», «Требования» …), длинные секции режутся по абзацам
с ``max_chars``. Если заголовков нет — абзацный чанкинг.
"""

from __future__ import annotations

import re

# Заголовок раздела: маркер-слово (Раздел/Общие положения/Требования/…) либо
# нумерация «N.»/«N.N» с заглавной буквой после номера («1. Общие положения»).
# Нумерованные пункты вида «1. товар должен…» (со строчной) заголовками не считаются.
_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"раздел[^\n]*|"
    r"общие (?:положения|сведения)[^\n]*|"
    r"требования[^\n]*|"
    r"состав и содержание[^\n]*|"
    r"порядок оказания[^\n]*|"
    r"\d+(?:\.\d+)*[\.\)]?\s+[А-ЯA-Z]"
    r")",
    re.IGNORECASE,
)

# Заголовок раздела в Markdown-представлении документа (MarkItDown): «#», «##», …
# Такие заголовки появляются из стилей документа (Heading 1/2/3 в docx, layout в PDF)
# и являются более надёжной границей раздела, чем эвристика по сырому тексту.
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")


def _is_heading(line: str) -> bool:
    if not line or not line.strip():
        return False
    return _HEADING_RE.match(line) is not None


def _is_section_heading(line: str) -> bool:
    """Граница раздела: Markdown-заголовок либо эвристика по сырому тексту."""
    if not line or not line.strip():
        return False
    return _MARKDOWN_HEADING_RE.match(line) is not None or _HEADING_RE.match(line) is not None


def _split_by_paragraphs(block: str, max_chars: int) -> list[str]:
    """Разбить текст по абзацам так, чтобы чанки не превышали ``max_chars``."""
    paragraphs = [p.strip() for p in block.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        para_len = len(para)
        if current and current_len + para_len + 2 > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        # Один абзац больше max_chars — режем по строкам.
        if para_len > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current, current_len = [], 0
            lines = para.splitlines()
            part: list[str] = []
            part_len = 0
            for line in lines:
                if part and part_len + len(line) + 1 > max_chars:
                    chunks.append("\n".join(part))
                    part, part_len = [], 0
                part.append(line)
                part_len += len(line) + 1
            if part:
                chunks.append("\n".join(part))
            continue
        current.append(para)
        current_len += para_len + 2
    if current:
        chunks.append("\n\n".join(current))
    return [c for c in chunks if c.strip()]


def split_tz_sections(text: str, max_chars: int = 1500) -> list[str]:
    """Структурированные чанки ТЗ (без пересечения границ разделов).

    Каждый чанк — «заголовок секции + тело» (заголовок повторяется в чанках
    длинной секции, чтобы контекст раздела не терялся).
    """
    if not text or not text.strip():
        return []
    lines = text.splitlines()
    # Группируем строки по заголовкам разделов.
    sections: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    body: list[str] = []
    for line in lines:
        if _is_section_heading(line):
            if heading is not None or body:
                sections.append((heading, body))
            heading = line.strip()
            body = []
        else:
            body.append(line)
    sections.append((heading, body))

    chunks: list[str] = []
    for section_heading, section_body in sections:
        header = section_heading or ""
        body_text = "\n".join(section_body).strip()
        block = f"{header}\n\n{body_text}" if header else body_text
        if not block.strip():
            continue
        if len(block) <= max_chars:
            chunks.append(block)
            continue
        # Длинная секция: режем по абзацам; заголовок повторяем в каждом чанке.
        for part in _split_by_paragraphs(body_text, max_chars - len(header) - 2):
            chunk = f"{header}\n\n{part}" if header else part
            if chunk.strip():
                chunks.append(chunk)
    return chunks if chunks else _split_by_paragraphs(text, max_chars)
