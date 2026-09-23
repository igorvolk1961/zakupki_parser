"""Очистка извлечённого текста ТЗ от мусора."""

from __future__ import annotations

import re

# Строка GFM-таблицы (MarkItDown docx/pdf): начинается с ``|`` (после пробелов)
# и заканчивается ``|``. Ячейки в таких таблицах — по одному ``|``-разделителю.
_PIPE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
# Разделитель заголовка в pipe-таблице: ячейки вида ``---``, ``:---``, ``---:``, ``:---:``.
_DASH_CELL_RE = re.compile(r"^:?-{2,}:?$")

# Заголовок-заглушка pandas/markitdown при экспорте xlsx без явных названий колонок
# («Unnamed: 0», «Unnamed: 12») и пропуск данных («NaN») — не несут содержания,
# только засоряют контекст LLM и таблицу в карточке.
_UNNAMED_HEADER_RE = re.compile(r"^unnamed:\s*\d+$", re.IGNORECASE)
_NAN_CELL_RE = re.compile(r"^nan$", re.IGNORECASE)


def _clean_header_cell(cell: str) -> str:
    return "" if _UNNAMED_HEADER_RE.fullmatch(cell.strip()) else cell


def _clean_data_cell(cell: str) -> str:
    return "" if _NAN_CELL_RE.fullmatch(cell.strip()) else cell


def _drop_empty_columns(
    header: list[str], data_rows: list[list[str]]
) -> tuple[list[str], list[list[str]]]:
    """Убрать столбцы, у которых пуст и заголовок, и все ячейки данных.

    После очистки ``Unnamed: N``/``NaN`` в исходно шумной xlsx-таблице такие
    столбцы — чистый мусор экспорта, а не содержательная пустая колонка
    (у настоящей пустой колонки заголовок обычно есть, данных просто нет).
    """
    keep = [i for i in range(len(header)) if header[i] or any(row[i] for row in data_rows)]
    if len(keep) == len(header):
        return header, data_rows
    return [header[i] for i in keep], [[row[i] for i in keep] for row in data_rows]


def _is_pipe_row(line: str) -> bool:
    return bool(_PIPE_ROW_RE.match(line))


def _pipe_cells(row: str) -> list[str]:
    """Ячейки строки pipe-таблицы (с обрезкой пробелов)."""
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _is_dash_row(cells: list[str]) -> bool:
    """Строка-разделитель вида ``| --- | --- |``."""
    return bool(cells) and all(bool(c) and _DASH_CELL_RE.fullmatch(c) for c in cells)


def _table_to_markdown(rows: list[str]) -> str:
    """Привести блок pipe-таблицы к корректной GFM-таблице.

    MarkItDown отдаёт таблицы DOCX/PDF как GFM-pipe-таблицы, иногда с пустой
    строкой-заглушкой ``|  |  |`` и лишним ``| --- |``. Чиним: убираем пустые
    строки и разделители-заглушки, первой осмысленной строкой делаем заголовок
    столбцов, а разделитель возвращаем ровно один. Markdown-разметка
    сохраняется — таблицу можно отрендерить в карточке и прочитать LLM.
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
    header = [_clean_header_cell(h) for h in header]
    data_rows = [[_clean_data_cell(c) for c in row] for row in data_rows]
    header, data_rows = _drop_empty_columns(header, data_rows)
    if not header:
        return ""
    ncols = len(header)
    lines = [f"| {' | '.join(header)} |", "| " + " | ".join(["---"] * ncols) + " |"]
    for row in data_rows:
        lines.append(f"| {' | '.join(row)} |")
    return "\n".join(lines)


def _normalize_tables(text: str) -> str:
    """Найти блоки GFM-pipe-таблиц и привести их к корректной разметке.

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
                converted = _table_to_markdown(block)
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
    # GFM-таблицы (MarkItDown docx/pdf) → корректная markdown-разметка таблиц
    # (без пустых строк-заглушек и лишних разделителей), чтобы таблицы можно
    # было и отрендерить в карточке, и прочитать чанкеру/LLM.
    text = _normalize_tables(text)
    # Схлопывание пробелов/табов и пустых строк.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Отбрасывание "мусорных" длинных строк без пробелов (base64 и т.п.).
    text = "\n".join(
        line for line in text.splitlines() if not (len(line) > 300 and " " not in line)
    )
    return text.strip()
