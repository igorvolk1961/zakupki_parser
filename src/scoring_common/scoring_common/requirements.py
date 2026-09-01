"""Извлечение и классификация требований к участнику из всех документов закупки.

Отличие от ``scoring_common.tz`` (поиск только в ТЗ): требования к участнику ищутся
в любом приложенном к закупке документе. Детерминированная часть (без LLM):

1. перебираем все документы (прямые файлы + записи архивов);
2. для каждого документа находим разделы требований: по имени файла (весь документ)
   или по заголовку раздела (``требования к участнику``/``к исполнителю``/``к составу
   заявки``);
3. если ни одного раздела по заголовкам не найдено — ищем эти шаблоны в текстах
   документов и берём разделы вокруг вхождений (фолбэк);
4. каждый найденный раздел классифицируем по ключевым словам в один из трёх типов
   (licenses / experience / minprom) либо в ``other`` и собираем структуру
   ``{licenses, experience, minprom, other}`` с полями ``{text, data=None}``.

Если шаблоны не найдены нигде — возвращается пустой объект ``{}``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from scoring_common.tz import extract_text
from scoring_common.tz.archives import _archive_inner_names
from scoring_common.tz.files import FileRef, collect_files, is_archive
from scoring_common.tz.text import clean_text

logger = logging.getLogger(__name__)

# Мера длины текста одного раздела: сверх меры обрезаем, чтобы не класть в поле БД
# огромное полотно (например, скан-документ без структуры).
_MAX_SECTION_CHARS = 100_000

# Шаблоны «требования к участнику/исполнителю/составу заявки» (падеже-независимо).
# ``требован`` покрывает «требование/требования/…», ``[а-яё]*`` — любое окончание.
_REQUIREMENT_PATTERNS: tuple[str, ...] = (
    r"требован[а-яё]*\s+к\s+участник[а-яё]*",
    r"требован[а-яё]*\s+к\s+исполнит[а-яё]*",
    r"требован[а-яё]*\s+к\s+составу\s+заявк[а-яё]*",
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


def _merge_texts(entries: list[dict[str, str]]) -> str:
    """Объединить тексты нескольких разделов одного типа в один Markdown."""
    return "\n\n---\n\n".join(e["text"] for e in entries if e["text"])


def _source_names(entries: list[dict[str, str]]) -> str | list[str]:
    """Имя(ена) файла, из которого(ых) взят текст раздела.

    Один уникальный источник — строка; несколько — список (без дубликатов).
    """
    names: list[str] = []
    for entry in entries:
        source = entry.get("source") or ""
        if source and source not in names:
            names.append(source)
    return names[0] if len(names) == 1 else names


def build_structure(candidates: list[dict[str, str]]) -> dict[str, Any]:
    """Собрать json-структуру требований: ``{licenses, experience, minprom, other}``.

    Каждая запись — ``{text, data=None, file_name}``: ``text`` объединяет все
    разделы типа (для трёх основных полей), ``file_name`` — название файла,
    из которого взята информация (список, если несколько файлов). Пусто — ``{}``.
    """
    if not candidates:
        return {}
    grouped: dict[str, list[dict[str, str]]] = {"other": []}
    for cand in candidates:
        grouped.setdefault(_classify_section(cand["text"]), []).append(cand)

    structure: dict[str, Any] = {}
    for key in ("licenses", "experience", "minprom"):
        entries = grouped.get(key)
        if entries:
            structure[key] = {
                "text": _merge_texts(entries),
                "data": None,
                "file_name": _source_names(entries),
            }
    if grouped.get("other"):
        structure["other"] = [
            {"text": e["text"], "data": None, "file_name": e.get("source") or ""}
            for e in grouped["other"]
        ]
    return structure


def extract_requirements(
    record: dict[str, Any], timeout: float = 30.0, verify_ssl: bool = True
) -> dict[str, Any]:
    """Извлечь структуру требований к участнику из всех документов карточки.

    Детерминированный этап: заполняет только ``text``, ``data`` остаётся ``None``.
    Если шаблоны не найдены нигде — возвращает ``{}``.
    """
    return build_structure(_candidate_sections(record, timeout=timeout, verify_ssl=verify_ssl))


__all__ = ["extract_requirements", "build_structure", "split_sections", "enumerate_document_refs"]
