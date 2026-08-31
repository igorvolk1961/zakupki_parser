"""Извлечение текста из файлов ТЗ: plain-text, DOCX, легаси DOC, PDF."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from scoring_common.tz.files import _PLAIN_TEXT_EXTENSIONS, _normalize

logger = logging.getLogger(__name__)

# Лимит времени на один вызов внешнего конвертера .doc (LibreOffice/catdoc/antiword).
_DOC_CONVERT_TIMEOUT = 90.0


def _decode(raw: bytes, name: str) -> str | None:
    """Извлечь текст из байт по расширению (docx/pdf/dot — Markdown/текст).

    Если расширение неизвестно или отсутствует (например, у Росэлторг имя файла
    «Техническое задание» без расширения, а URL вида ``/api/v1/documents/<uuid>``),
    формат определяется по содержимому — иначе такие файлы никогда не читаются.
    """
    ext = _normalize(name)
    for candidate in _PLAIN_TEXT_EXTENSIONS:
        if ext.endswith(candidate):
            return _decode_text(raw)
    if ext.endswith(".docx"):
        return _extract_docx(raw)
    if ext.endswith(".doc"):
        return _extract_doc(raw)
    if ext.endswith(".pdf"):
        return _extract_pdf(raw)
    # Нераспознанное/отсутствующее расширение — формат по содержимому.
    return _decode_by_signature(raw)


def _decode_text(raw: bytes) -> str | None:
    """Декодировать байты как plain-text (utf-8 → cp1251)."""
    for encoding in ("utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _decode_by_signature(raw: bytes) -> str | None:
    """Определить формат по магическим байтам, когда расширение неизвестно.

    Покрывает файлы ЭТП с именами без расширения: PDF (``%PDF``), OOXML/zip
    (``PK`` — почти всегда .docx), легаси OLE2 (``.doc``), иначе — plain-text.
    """
    if raw.startswith(b"%PDF"):
        return _extract_pdf(raw)
    if raw.startswith(b"PK\x03\x04"):
        return _extract_docx(raw)
    if raw.startswith(b"\xd0\xcf\x11\xe0"):
        return _extract_doc(raw)
    return _decode_text(raw)


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
        if not text:
            logger.warning("Конвертация %s вернула пустой текст (скан PDF/битый файл?)", extension)
        return text or None
    except Exception as exc:  # noqa: BLE001 - битый файл/неизвестный формат
        logger.warning("Не удалось конвертировать %s в Markdown: %s", extension, exc)
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
