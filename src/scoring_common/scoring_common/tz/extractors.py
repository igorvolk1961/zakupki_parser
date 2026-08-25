"""Извлечение текста из файлов ТЗ: plain-text, DOCX, PDF (MarkItDown)."""

from __future__ import annotations

import io
from typing import Any

from scoring_common.tz.files import _PLAIN_TEXT_EXTENSIONS, _normalize


def _decode(raw: bytes, name: str) -> str | None:
    """Извлечь текст из байт по расширению (docx/pdf — Markdown через MarkItDown)."""
    ext = _normalize(name)
    for candidate in _PLAIN_TEXT_EXTENSIONS:
        if ext.endswith(candidate):
            for encoding in ("utf-8", "cp1251"):
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return None
    if ext.endswith(".docx"):
        return _extract_docx(raw)
    if ext.endswith(".pdf"):
        return _extract_pdf(raw)
    return None


# Ленивый MarkItDown (Microsoft): конвертация docx/pdf в Markdown с сохранением
# структуры — заголовки разделов (Heading 1/2/3, layout PDF) и таблицы. Это даёт
# RAG-чанкеру надёжные границы разделов и не теряет табличные требования ТЗ.
_markitdown: Any | bool | None = None


def _markitdown_instance() -> Any | None:
    global _markitdown
    if _markitdown is None:
        try:
            from markitdown import MarkItDown

            _markitdown = MarkItDown()
        except Exception:  # noqa: BLE001 - библиотека недоступна (best-effort)
            _markitdown = False
    return _markitdown if _markitdown is not False else None


def _convert_markdown(raw: bytes, extension: str) -> str | None:
    """Конвертировать документ в Markdown (заголовки/таблицы сохраняются)."""
    md = _markitdown_instance()
    if md is None:
        return None
    try:
        result = md.convert_stream(io.BytesIO(raw), file_extension=extension)
        text = (result.text_content or "").strip()
        return text or None
    except Exception:  # noqa: BLE001 - битый файл/неизвестный формат
        return None


def _extract_docx(raw: bytes) -> str | None:
    """Markdown из DOCX (mammoth: заголовки по стилям, таблицы сохраняются)."""
    return _convert_markdown(raw, ".docx")


def _extract_pdf(raw: bytes) -> str | None:
    """Markdown из PDF (pdfplumber: таблицы по layout, fallback pdfminer)."""
    return _convert_markdown(raw, ".pdf")
