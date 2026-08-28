"""Извлечение текста из файлов ТЗ: plain-text, DOCX, легаси DOC, PDF."""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from scoring_common.tz.files import _PLAIN_TEXT_EXTENSIONS, _normalize

# Лимит времени на один вызов внешнего конвертера .doc (LibreOffice/catdoc/antiword).
_DOC_CONVERT_TIMEOUT = 90.0


def _decode(raw: bytes, name: str) -> str | None:
    """Извлечь текст из байт по расширению (docx/pdf/dot — Markdown/текст)."""
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
    if ext.endswith(".doc"):
        return _extract_doc(raw)
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


def _extract_doc(raw: bytes, timeout: float = _DOC_CONVERT_TIMEOUT) -> str | None:
    """Текст из легаси .doc (бинарный Word/OLE2).

    MarkItDown не понимает .doc, поэтому текст извлекаем внешним конвертером
    (по порядку: LibreOffice headless -> catdoc -> antiword). Возвращает None,
    если ни один конвертер недоступен или не дал текст (best-effort).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        doc = Path(tmpdir) / "document.doc"
        doc.write_bytes(raw)
        # LibreOffice: создаёт document.txt в outdir (UTF-8).
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            try:
                subprocess.run(
                    [
                        soffice,
                        "--headless",
                        f"-env:UserInstallation=file://{tmpdir}/lo",
                        "--convert-to",
                        "txt:Text (encoded):UTF8",
                        "--outdir",
                        tmpdir,
                        str(doc),
                    ],
                    check=True,
                    timeout=timeout,
                    capture_output=True,
                )
            except Exception:  # noqa: BLE001 - LibreOffice недоступен/битый файл
                pass
            else:
                text = (
                    (Path(tmpdir) / "document.txt")
                    .read_text(encoding="utf-8", errors="ignore")
                    .strip()
                )
                if text:
                    return text
        # Фолбэки: catdoc (явный UTF-8) и antiword (stdout).
        for argv in (["catdoc", "-d", "utf-8", str(doc)], ["antiword", str(doc)]):
            if not shutil.which(argv[0]):
                continue
            try:
                proc = subprocess.run(argv, check=False, timeout=timeout, capture_output=True)
            except Exception:  # noqa: BLE001
                continue
            text = proc.stdout.decode("utf-8", errors="ignore").strip()
            if text:
                return text
    return None
