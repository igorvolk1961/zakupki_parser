"""Извлечение и классификация требований к участнику из всех документов закупки.

Отличие от ``scoring_common.tz`` (поиск только в ТЗ): требования к участнику ищутся
в любом приложенном к закупке документе. Детерминированная часть (без LLM).

Основной (table-)путь для PDF: разбираем таблицу раздела «Требования к участникам»
(см. ``scoring_common.tables``): каждая строка таблицы — отдельное требование; если
в строке 3 ячейки, третья — дополнительный параметр (``additional``).

Значения-маркеры («не установлено», «не применяется/-ются», «не предоставляется/-ются»,
«не требуется/-ются») заменяются на «НЕТ» ЕДИНООБРАЗНО: и в требованиях из таблиц, и в
требованиях, изложенных плоским (иерархическим) текстом (см. ``_replace_marker_values``).

Если таблица не найдена — fallback-путь: ищем разделы требований по имени файла
(весь документ) или по заголовку раздела (``требования к участнику``/``к исполнителю``/
``к составу заявки``/``требования, предъявляемые к участнику закупки``); во всех
шаблонах важен порядок слов и отсутствие других слов между указанными частями; если
ни одного раздела по заголовкам не найдено — ищем эти шаблоны в текстах документов.

Каждый найденный раздел/строку классифицируем по ключевым словам в один из трёх
типов (licenses / experience / minprom) либо в ``other`` и собираем структуру
``{licenses, experience, minprom, other}``, где каждый тип — список объектов
``{text, data=None, file_name}`` (по одному на раздел, без слияния текстов).

Если ничего не найдено — возвращается пустой объект ``{}``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from scoring_common.law_requirements import annotate_requirements
from scoring_common.tables import _MARKER_VALUE_RE, pdf_to_markdown_tables
from scoring_common.tz import extract_text
from scoring_common.tz.archives import _archive_inner_names
from scoring_common.tz.download import _download
from scoring_common.tz.files import FileRef, collect_files, is_archive
from scoring_common.tz.text import clean_text

logger = logging.getLogger(__name__)

# Мера длины текста одного раздела: сверх меры обрезаем, чтобы не класть в поле БД
# огромное полотно (например, скан-документ без структуры).
_MAX_SECTION_CHARS = 100_000

# Шаблоны «требования к участнику/исполнителю/составу заявки» (падеже-независимо).
# ``требован`` покрывает «требование/требования/…», ``[а-яё]*`` — любое окончание.
# Порядок слов и ОТСУТСТВИЕ других слов между указанными частями важны: между
# токенами допускаются только пробелы (и необязательная запятая в четвёртом
# шаблоне), но не другие слова.
_REQUIREMENT_PATTERNS: tuple[str, ...] = (
    r"требован[а-яё]*\s+к\s+участник[а-яё]*",
    r"требован[а-яё]*\s+к\s+исполнит[а-яё]*",
    r"требован[а-яё]*\s+к\s+составу\s+заявк[а-яё]*",
    r"требован[а-яё]*\s*,?\s*предъявляем[а-яё]*\s+к\s+участник[а-яё]*\s+закупк[а-яё]*",
)
_REQUIREMENT_RES = tuple(re.compile(p, re.IGNORECASE) for p in _REQUIREMENT_PATTERNS)

# Ключевые слова классификации разделов по типу требования (recall-oriented: лишний
# раздел в «other» не критичен, точную структуру добирает LLM на этапе data).
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "licenses": (
        "лицензи",
        r"\bсро\b",
        "саморегулиру",
        "допуск",
        "разрешени",
        "свидетельств",
        "сертификат",
        "членств",
        "аккредитац",
        "аттестац",
    ),
    "experience": (
        "опыт исполнени",
        "опыт выполнени",
        "подтвержденн",
        "2571",
        "реестр контрактов",
        "реестр договор",
        "сканы договор",
        "копии контракт",
        "копии договор",
    ),
    "minprom": (
        "минпромторг",
        "реестр промышленн",
        "запрет иностранн",
        "происхождени продукци",
        "не установлено",
        "реестр российск",
    ),
}
_PRIORITY = {"licenses": 0, "experience": 1, "minprom": 2}

# Границы разделов: Markdown-заголовки (``#/##/…``) и эвристика по нумерации/
# словам, аналогично ``analysis_service.pipeline.chunker`` (без импорта анализа).
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_NUM_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+)*[\.\)]?\s+[А-ЯA-Z]")
_KEYWORD_HEADING_RE = re.compile(
    r"^\s*(?:раздел[^\n]*|общие\s+(?:положения|сведения)[^\n]*|"
    r"требования[^\n]*|состав\s+и\s+содержание[^\n]*|порядок\s+оказания[^\n]*)",
    re.IGNORECASE,
)


def _normalize_name(name: str | None) -> str:
    """Имя файла в нижнем регистре, разделители ``_``/``-`` приведены к пробелу."""
    return re.sub(r"[_-]+", " ", (name or "").lower())


def _matches_requirement(text: str) -> bool:
    """Содержит ли текст один из шаблонов требований к участнику."""
    return any(pat.search(text) for pat in _REQUIREMENT_RES)


def _is_section_heading(line: str) -> bool:
    if not line or not line.strip():
        return False
    return bool(
        _MARKDOWN_HEADING_RE.match(line)
        or _NUM_HEADING_RE.match(line)
        or _KEYWORD_HEADING_RE.match(line)
    )


def split_sections(markdown: str) -> list[dict[str, str]]:
    """Разбить Markdown-текст документа на разделы ``{heading, text}``.

    Заголовок раздела — Markdown-заголовок, нумерованный пункт («1. Общие положения»)
    либо маркер-слово («Раздел»/«Требования»/…). Если заголовков нет — один раздел
    из всего текста.
    """
    sections: list[dict[str, str]] = []
    heading: str | None = None
    body: list[str] = []
    for line in markdown.splitlines():
        if _is_section_heading(line):
            if heading is not None or body:
                sections.append({"heading": heading or "", "text": "\n".join(body).strip()})
            heading = line.strip()
            body = []
        else:
            body.append(line)
    if heading is not None or body:
        sections.append({"heading": heading or "", "text": "\n".join(body).strip()})
    return sections


def enumerate_document_refs(
    record: dict[str, Any], timeout: float = 30.0, verify_ssl: bool = True
) -> list[FileRef]:
    """Все извлекаемые документы закупки: прямые файлы + записи архивов."""
    refs: list[FileRef] = []
    for ref in collect_files(record):
        if is_archive(ref.name):
            for inner in _archive_inner_names(ref.url, timeout=timeout, verify_ssl=verify_ssl):
                if inner:
                    refs.append(FileRef(inner, f"{ref.url}#{inner}"))
        else:
            refs.append(ref)
    return refs


def _extract_doc_text(ref: FileRef, timeout: float, verify_ssl: bool) -> str | None:
    """Извлечь текст документа (best-effort: сбой одного файла не роняет остальные)."""
    try:
        text = extract_text(ref, timeout=timeout, verify_ssl=verify_ssl)
        return clean_text(text) if text else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось извлечь текст документа %s: %s", ref.name, exc)
        return None


def _candidate_sections(
    record: dict[str, Any], timeout: float = 30.0, verify_ssl: bool = True
) -> list[dict[str, str]]:
    """Найти разделы требований к участнику (по имени файла/заголовку → фолбэк по тексту).

    Возвращает список ``{"source": "<имя документа>", "text": "<Markdown раздела>"}``.
    """
    candidates: list[dict[str, str]] = []
    # Резерв для фолбэка: документы, у которых не нашлось раздела по заголовку.
    fallback_docs: list[tuple[str, list[dict[str, str]]]] = []

    for ref in enumerate_document_refs(record, timeout=timeout, verify_ssl=verify_ssl):
        text = _extract_doc_text(ref, timeout=timeout, verify_ssl=verify_ssl)
        if not text:
            continue
        if len(text) > _MAX_SECTION_CHARS:
            text = text[:_MAX_SECTION_CHARS]
        base_name = ref.name.rsplit("/", 1)[-1]
        name_matches = _matches_requirement(_normalize_name(base_name))
        sections = split_sections(text)
        doc_candidates: list[dict[str, str]] = []
        for sec in sections:
            full = (
                (sec["heading"] + "\n\n" + sec["text"]).strip() if sec["heading"] else sec["text"]
            )
            if not full:
                continue
            if name_matches or (sec["heading"] and _matches_requirement(_normalize_name(full))):
                doc_candidates.append({"source": base_name, "text": full})
        if doc_candidates:
            candidates.extend(doc_candidates)
        else:
            fallback_docs.append((base_name, sections))

    if candidates:
        return candidates

        # Фолбэк: заголовки нигде не найдены — ищем шаблоны в текстах разделов.
        for base_name, sections in fallback_docs:
            for sec in sections:
                full = (
                    (sec["heading"] + "\n\n" + sec["text"]).strip()
                    if sec["heading"]
                    else sec["text"]
                )
                if full and _matches_requirement(full):
                    candidates.append({"source": base_name, "text": full})
    return candidates


def _classify_section(text: str) -> str:
    """Тип требования по ключевым словам: licenses | experience | minprom | other."""
    lower = text.lower()
    counts = {key: sum(1 for kw in kws if re.search(kw, lower)) for key, kws in _KEYWORDS.items()}
    best = max(counts, key=lambda k: (counts[k], -_PRIORITY[k]))
    return best if counts[best] > 0 else "other"


def _replace_marker_values(text: str) -> str:
    """Заменить значения-маркеры («не установлено», «не требуется»…) на «НЕТ».

    Работает и в табличной ячейке, и в плоском/иерархическом тексте: маркер как
    значение распознаётся в конце строки (``_MARKER_VALUE_RE``).
    """
    return " ".join(_MARKER_VALUE_RE.sub("НЕТ", text).split())


def _piece_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [c.strip() for c in stripped.strip("|").split("|")]


def _is_sep_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c != "")


def _parse_pipe_tables(markdown: str) -> list[list[list[str]]]:
    """Markdown-таблицы (GFM) в список ``[[ячейки], …]``."""
    lines = markdown.splitlines()
    tables: list[list[list[str]]] = []
    i, n = 0, len(lines)
    while i < n:
        if _piece_row(lines[i]) is None:
            i += 1
            continue
        j = i
        while j < n and _piece_row(lines[j]) is not None:
            j += 1
        rows: list[list[str]] = []
        for k in range(i, j):
            pieces = _piece_row(lines[k])
            if pieces and not _is_sep_row(pieces):
                rows.append(pieces)
        if rows:
            tables.append(rows)
        i = j
    return tables


# Заголовок таблицы-раздела: единственная непустая ячейка со словами «требования… участник».
_REQ_HEADER_RE = re.compile(r"требован[а-яё]*.{0,40}участник[а-яё]*", re.IGNORECASE | re.DOTALL)


def _is_single_cell_row(row: list[str]) -> str | None:
    nonempty = [c for c in row if c]
    return nonempty[0] if len(nonempty) == 1 else None


def _requirement_section_rows(markdown: str) -> list[list[str]]:
    """Строки раздела «Требования к участникам» (каждая строка — одно требование).

    Правило: строка с одной непустой ячейкой — заголовок раздела; раздел — строки
    после него до следующей такой строки. Уже применяются вертикальная склейка и
    вынос маркеров (см. ``scoring_common.tables``).
    """
    tables = _parse_pipe_tables(markdown)
    start: tuple[int, int] | None = None
    for ti, table in enumerate(tables):
        for ri, row in enumerate(table):
            header = _is_single_cell_row(row)
            if header and _REQ_HEADER_RE.search(header):
                start = (ti, ri)
                break
        if start:
            break
    if not start:
        return []
    ti0, ri0 = start
    rows: list[list[str]] = []
    for ti in range(ti0, len(tables)):
        r0 = ri0 + 1 if ti == ti0 else 0
        for row in tables[ti][r0:]:
            if _is_single_cell_row(row):
                return rows
            rows.append(row)
    return rows


def _table_requirement_candidates(
    record: dict[str, Any], timeout: float = 30.0, verify_ssl: bool = True
) -> list[dict[str, Any]]:
    """Разобрать требования из таблиц PDF-документов (по одному требованию на строку).

    Строка из 3 ячеек: третья — дополнительный параметр требования (``additional``).
    Если третья ячейка — значение-маркер («не установлено», «не требуется» … → «НЕТ»),
    оно НЕ попадает в ``additional``, а помечается флагом ``negated`` (без дубля «НЕТ»).
    """
    candidates: list[dict[str, Any]] = []
    for ref in enumerate_document_refs(record, timeout=timeout, verify_ssl=verify_ssl):
        if not ref.name.lower().endswith(".pdf"):
            continue
        raw = _download(ref.url.split("#", 1)[0], timeout=timeout, verify_ssl=verify_ssl)
        if not raw:
            continue
        markdown = pdf_to_markdown_tables(raw)
        if not markdown:
            continue
        source = ref.name.rsplit("/", 1)[-1]
        for cells in _requirement_section_rows(markdown):
            text = " ".join(c for c in cells[:2] if c)
            if not text:
                continue
            item: dict[str, Any] = {"source": source, "text": text}
            if len(cells) >= 3 and cells[2].strip():
                third = _replace_marker_values(cells[2])
                if third == "НЕТ":
                    item["negated"] = True
                else:
                    item["additional"] = third
            candidates.append(item)
    return candidates


def build_structure(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Собрать json-структуру требований: ``{licenses, experience, minprom, other}``.

    Каждое поле — список объектов ``{text, data=None, file_name}``: один объект на
    найденный раздел требования (тексты разделов одного типа НЕ сливаются). Для
    требований из таблиц дополнительно сохраняется ``additional`` (3-я ячейка, если
    это реальный параметр) и ``negated`` (если 3-я ячейка — значение-маркер).
    ``data`` заполняется LLM на этапе анализа. Пусто — ``{}``.
    """
    if not candidates:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {"other": []}
    for cand in candidates:
        grouped.setdefault(_classify_section(cand["text"]), []).append(cand)

    structure: dict[str, Any] = {}
    for key in ("licenses", "experience", "minprom", "other"):
        entries = grouped.get(key)
        if entries:
            items: list[dict[str, Any]] = []
            for e in entries:
                item: dict[str, Any] = {
                    "text": _replace_marker_values(e["text"]),
                    "data": None,
                    "file_name": e.get("source") or "",
                }
                if e.get("additional"):
                    item["additional"] = e["additional"]
                if e.get("negated"):
                    item["negated"] = True
                items.append(item)
            structure[key] = items
    return structure


def extract_requirements(
    record: dict[str, Any], timeout: float = 30.0, verify_ssl: bool = True
) -> dict[str, Any]:
    """Извлечь структуру требований к участнику из всех документов карточки.

    Предпочтителен table-путь: если в PDF-документе найден раздел-таблица
    «Требования к участникам», каждая строка таблицы становится отдельным
    требованием. Иначе — legacy-поиск разделов по имени файла/заголовку.

    Детерминированный этап: заполняет только ``text`` (и ``additional``/``negated``),
    ``data`` остаётся ``None``; в структуре остаются только требования с отрицанием
    («не установлено», «не требуется» и т.п.), остальное удаляется
    (см. ``scoring_common.law_requirements``). Если ничего не найдено — ``{}``.
    """
    if candidates := _table_requirement_candidates(record, timeout=timeout, verify_ssl=verify_ssl):
        structure = build_structure(candidates)
    else:
        structure = build_structure(
            _candidate_sections(record, timeout=timeout, verify_ssl=verify_ssl)
        )
    return annotate_requirements(structure)


__all__ = ["extract_requirements", "build_structure", "split_sections", "enumerate_document_refs"]
