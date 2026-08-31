"""Очистка извлечённого текста ТЗ от мусора."""

from __future__ import annotations

import re

# Строка GFM-таблицы (MarkItDown docx/pdf): начинается с ``|`` (после пробелов)
# и заканчивается ``|``. Ячейки в таких таблицах — по одному ``|``-разделителю.
_PIPE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
# Разделитель заголовка в pipe-таблице: ячейки вида ``---``, ``:---``, ``---:``, ``:---:``.
_DASH_CELL_RE = re.compile(r"^:?-{2,}:?$")


def _is_pipe_row(line: str) -> bool:
    return bool(_PIPE_ROW_RE.match(line))


def _pipe_cells(row: str) -> list[str]:
    """Ячейки строки pipe-таблицы (с обрезкой пробелов)."""
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _is_dash_row(cells: list[str]) -> bool:
    """Строка-разделитель вида ``| --- | --- |``."""
    return bool(cells) and all(bool(c) and _DASH_CELL_RE.fullmatch(c) for c in cells)


def _table_to_text(rows: list[str]) -> str:
    """Преобразовать блок pipe-таблицы в читаемые построчные записи.

    MarkItDown отдаёт таблицы DOCX/PDF как GFM-pipe-таблицы (иногда с пустой
    строкой-заглушкой ``|  |  |`` вместо заголовка столбцов). Чтобы таблица была
    пригодна и для анализа (чанкер/LLM), и для просмотра, каждая строка данных
    превращается в ``Заголовок: значение | …`` — самодостаточную строку, которую
    чанкер может резать построчно без потери смысла.
    """
    body: list[list[str]] = []
    for cells in (_pipe_cells(r) for r in rows):
        if _is_dash_row(cells):
            continue
        if all(not c for c in cells):
            continue
        body.append(cells)
    if not body:
        return ""
    ncols = max(len(r) for r in body)
    body = [r + [""] * (ncols - len(r)) for r in body]
    header, data_rows = body[0], body[1:]
    if not data_rows:
        # Только заголовки — вернём их списком.
        return " | ".join(c or "—" for c in header)
    lines: list[str] = []
    for row in data_rows:
        parts = [f"{h}: {v}" if h else v for h, v in zip(header, row, strict=True) if (h or v)]
        lines.append(" | ".join(parts) if parts else " | ".join(v or "—" for v in row))
    return "\n".join(lines)


def _normalize_tables(text: str) -> str:
    """Найти блоки GFM-pipe-таблиц и заменить их читаемыми строками.

    Блок — подряд идущие строки вида ``| a | b |``. Таблицей считаем блок из
    ≥2 таких строк, если в нём есть строка-разделитель ``| --- |`` (надёжный
    признак таблицы из MarkItDown) либо ≥3 строк (таблица без разделителя).
    Одиночные ``|``-строки (не таблица) остаются как есть.
    """
    lines = text.splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if _is_pipe_row(lines[i]):
            j = i
            while j < n and _is_pipe_row(lines[j]):
                j += 1
            block = lines[i:j]
            has_dash = any(_is_dash_row(_pipe_cells(line)) for line in block)
            if len(block) >= 2 and (has_dash or len(block) >= 3):
                converted = _table_to_text(block)
                if converted:
                    out.append(converted)
                    i = j
                    continue
            out.extend(block)
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def clean_text(text: str) -> str:
    """Очистить извлечённый текст от мусора."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Управляющие символы (кроме переноса строки и табуляции).
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # GFM-таблицы (MarkItDown docx/pdf) → читаемые построчные записи
    # «заголовок: значение», чтобы таблицы были анализируемыми и для чанкера,
    # и для LLM, и для просмотра в карточке (вместо сырых ``| … |``).
    text = _normalize_tables(text)
    # Схлопывание пробелов/табов и пустых строк.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Отбрасывание "мусорных" длинных строк без пробелов (base64 и т.п.).
    text = "\n".join(
        line for line in text.splitlines() if not (len(line) > 300 and " " not in line)
    )
    return text.strip()
