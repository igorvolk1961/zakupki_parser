"""Рендер layout-PDF в Markdown с сохранением GFM-таблиц (pdfplumber).

MarkItDown (текст ТЗ) кладёт таблицы ЕАИСТ/Росэлторг сплошным текстом без структуры,
а pdfplumber распознаёт их как сетку. Этот модуль отдаёт Markdown с настоящими
pipe-таблицами и применяет правила восстановления структуры:

* вертикальная склейка ячеек (rowspan): строка с пустой первой ячейкой — продолжение
  предыдущей строки, склеиваем их по колонкам (в т.ч. через границу страниц);
* вынос завершающего маркера («не установлено», «не применяется(-ются)»,
  «не предоставляется(-ются)», «не требуется(-ются)») в отдельную ячейку той же строки.
"""

from __future__ import annotations

import io
import re
import statistics
from itertools import groupby
from typing import Any, Final

import pdfplumber

# Маркеры «отсутствия требования» — в форме, не зависящей от числа
# («не требуется»/«не требуются», «не предоставляется»/«не предоставляются» и т.п.).
_MARKER_PHRASE = (
    r"не\s+(?:установлен[а-яё]*|применя(?:ется|ются)|"
    r"предоставля(?:ется|ются)|требу(?:ется|ются))"
)
# Вынос маркера в отдельную ячейку: маркер в конце (однострочной) ячейки.
_MARKER_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(_MARKER_PHRASE + r"$", re.IGNORECASE),
)
# Замена значения-маркера на «НЕТ» в произвольном (в т.ч. многострочном) тексте:
# маркер как значение подходит к концу строки (допускается завершающая пунктуация).
_MARKER_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    _MARKER_PHRASE + r"\b[ \t]*\.?$", re.IGNORECASE | re.MULTILINE
)


def _condense(s: str) -> str:
    return " ".join(s.split())


def _split_trailing_marker(row: list[str]) -> list[str]:
    """Вынести завершающий маркер в отдельную ячейку той же строки."""
    out: list[str] = []
    for cell in row:
        text = cell.rstrip()
        m = next((rx.search(text) for rx in _MARKER_RES if rx.search(text)), None)
        if m is None:
            out.append(cell)
            continue
        tail = m.group(0).rstrip()
        head = text[: m.start()].rstrip()
        if head:
            out.append(head)
        out.append(tail)
    return out


def _is_continuation(prev: list[str], cur: list[str]) -> bool:
    return bool(prev and cur) and (cur[0] == "") and (prev[0] != "")


def _merge_row(prev: list[str], cur: list[str]) -> list[str]:
    n = max(len(prev), len(cur))
    out: list[str] = []
    for j in range(n):
        p = prev[j] if j < len(prev) else ""
        c = cur[j] if j < len(cur) else ""
        out.append(_condense((p + " " + c).strip()) if c else p)
    return out


def _merge_rows(rows: list[list[str]]) -> list[list[str]]:
    merged: list[list[str]] = []
    for row in rows:
        if merged and _is_continuation(merged[-1], row):
            merged[-1] = _merge_row(merged[-1], row)
        else:
            merged.append(row)
    return merged


def _table_md(rows: list[list[str]]) -> str | None:
    if not rows:
        return None
    rows = _merge_rows([list(r) for r in rows])
    rows = [_split_trailing_marker(r) for r in rows]
    nc = max(len(r) for r in rows)
    rows = [r + [""] * (nc - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * nc) + " |"]
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _page_events(page: Any) -> list[tuple[float, str, Any]]:
    """События страницы по вертикали: ('P', текст) и ('T', списки строк таблиц)."""
    tables = page.find_tables()
    boxes = [t.bbox for t in tables]

    def in_table(w: dict[str, Any]) -> bool:
        cx = (w["x0"] + w["x1"]) / 2
        cy = (w["top"] + w["bottom"]) / 2
        return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)

    heights = [w["bottom"] - w["top"] for w in page.extract_words()]
    line_h = statistics.median(heights) if heights else 8.0

    words = [w for w in page.extract_words() if not in_table(w)]
    rows: list[tuple[int, dict[str, Any]]] = []
    for w in words:
        rows.append((round(w["top"] / 2) * 2, w))
    rows.sort(key=lambda e: (e[0], e[1]["x0"]))

    lines: list[tuple[int, str]] = []
    for key, grp in groupby(rows, key=lambda e: e[0]):
        ws = sorted((w for _k, w in grp), key=lambda w: w["x0"])
        txt = _condense(" ".join(w["text"] for w in ws))
        if txt:
            lines.append((key, txt))

    paragraphs: list[tuple[int, str]] = []
    cur: str | None = None
    cur_top = 0
    for key, txt in lines:
        if cur is not None and key - cur_top <= line_h * 1.5:
            cur += " " + txt
        else:
            if cur is not None:
                paragraphs.append((cur_top, cur))
            cur = txt
            cur_top = key
    if cur is not None:
        paragraphs.append((cur_top, cur))

    events: list[tuple[float, str, Any]] = [(top, "P", txt) for top, txt in paragraphs]
    for t in tables:
        data = [[_condense(c or "") for c in r] for r in t.extract()]
        data = _merge_rows(data)
        if data:
            events.append((t.bbox[1], "T", data))
    events.sort(key=lambda e: e[0])
    return events


def _merge_across_pages(pages: list[list[tuple[float, str, Any]]]) -> None:
    """Склеить продолжение последней таблицы страницы с первой таблицей следующей."""
    for i in range(len(pages) - 1):
        prev, nxt = pages[i], pages[i + 1]
        prev_t = max((j for j, e in enumerate(prev) if e[1] == "T"), default=None)
        nxt_t = next((j for j, e in enumerate(nxt) if e[1] == "T"), None)
        if prev_t is None or nxt_t is None:
            continue
        prev_rows, nxt_rows = prev[prev_t][2], nxt[nxt_t][2]
        if prev_rows and nxt_rows and _is_continuation(prev_rows[-1], nxt_rows[0]):
            prev_rows[-1] = _merge_row(prev_rows[-1], nxt_rows[0])
            nxt_rows.pop(0)
            if not nxt_rows:
                nxt.pop(nxt_t)


def pdf_to_markdown_tables(raw: bytes) -> str | None:
    """Markdown с GFM-таблицами из PDF-байт (или None, если PDF не разобран)."""
    try:
        pdf = pdfplumber.open(io.BytesIO(raw))
    except Exception:  # noqa: BLE001 - битый/не-PDF файл
        return None
    try:
        pages = [_page_events(page) for page in pdf.pages]
    finally:
        pdf.close()
    _merge_across_pages(pages)

    blocks: list[str] = []
    for events in pages:
        for _top, kind, payload in events:
            if kind == "T":
                md = _table_md(payload)
                if md:
                    blocks.append(md)
            else:
                blocks.append(payload)
    out = "\n\n".join(b.strip() for b in blocks if b.strip())
    out = "\n\n".join(x.strip() for x in out.split("\n\n"))
    return out.strip() or None


__all__ = ["pdf_to_markdown_tables", "_MARKER_RES", "_MARKER_VALUE_RE"]
