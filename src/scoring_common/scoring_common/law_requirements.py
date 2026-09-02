"""Сопоставление требований закупки с универсальными требованиями 44-ФЗ.

Логика показа для тендеролога:

* требования, устанавливаемые законом (ст. 31 44-ФЗ, часть 1), обязательны и
  универсальны — по умолчанию их НЕ показываем (это норма, отвлекает от
  специфики закупки);
* НО если закупка явно указывает, что соответствующее требование «не установлено»,
  «не применяется», «не требуется» и т.п. — показываем: это отклонение от нормы,
  важно для тендеролога;
* специфические требования (не покрытые законом) — показываем всегда.

Здесь — детерминированная часть: загрузка реестра требований закона (JSON),
поиск совпадения требования закупки с универсальным и признак «отрицание»
(маркер → «НЕТ»). Куда девать результат — на уровне показа/аналитики.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_LAW_FILE_NAME = "44фз-28-12-2025-требования-к-участникам.json"


def _default_law_path() -> Any:
    """Источник реестра закона: пакетный ресурс ``resources/`` → dev-каталог ``docs/``."""
    # 1) ресурс пакета (editable/wheel): scoring_common/resources/<файл>
    try:
        from importlib.resources import files

        res = files("scoring_common").joinpath("resources", _LAW_FILE_NAME)
        if res.is_file():
            return res
    except Exception:  # noqa: BLE001 - окружения без ресурсов (zip-import и т.п.)
        pass
    # 2) resources/ рядом с пакетом (исходник)
    local = Path(__file__).resolve().parent / "resources" / _LAW_FILE_NAME
    if local.exists():
        return local
    # 3) dev: docs/references (вверх по дереву репозитория)
    for parent in Path(__file__).resolve().parents:
        cand = parent / "docs" / "references" / _LAW_FILE_NAME
        if cand.exists():
            return cand
    return local


# Номер части статьи 31, чьи пункты — универсальные требования.
_UNIVERSAL_PARTS = ("1",)
# Порог полноты покрытия: доля «своих» значимых терминов требования закона,
# присутствующих в тексте требования закупки, при которой требование признаётся
# универсальным (дословное повторение нормы). Порог высокий: близкие пересказы
# и ссылки на норму (заявки, перечни) остаются спецификой и показываются.
_MATCH_THRESHOLD = 0.92

# Общий («шумовой») юридический словарный запас — не показателен для сопоставления
# и потому выкидывается из множества значимых терминов.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "участник",
        "участника",
        "участники",
        "участникам",
        "участниках",
        "закупка",
        "закупки",
        "закупок",
        "закупке",
        "закупках",
        "поставщик",
        "подрядчик",
        "исполнитель",
        "исполнителя",
        "заказчик",
        "заказчика",
        "российский",
        "российская",
        "российской",
        "федерации",
        "федеральный",
        "федерального",
        "закон",
        "закона",
        "законодательством",
        "устанавливает",
        "установлены",
        "установлено",
        "соответствии",
        "соответствие",
        "соответствия",
        "требование",
        "требования",
        "требованию",
        "требований",
        "предусмотрен",
        "настоящего",
        "юридического",
        "физического",
        "осуществляющ",
        "подрядчику",
        "исполнителю",
        "лица",
        "лицо",
        "лиц",
        "которое",
        "который",
        "которые",
        "указанных",
        "случае",
        "условии",
        "предмета",
        "предметом",
        "контракт",
        "контракта",
        "товара",
        "товаров",
        "работ",
        "работы",
        "услуг",
        "услуги",
        "оказания",
        "выполнения",
        "включены",
        "перечень",
        "отношения",
        "связанных",
        "одной",
        "даты",
        "период",
    }
)

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+|/[а-яёa-z0-9]+")
_PUNCT_RE = re.compile(r"[^\w\s/]", flags=re.UNICODE)


def load_law_requirements(path: str | Path | None = None) -> dict[str, Any]:
    """Загрузить реестр требований закона (JSON: ``docs/references`` или ресурс ``data/``)."""
    import json

    source = Path(path) if path else _default_law_path()
    try:
        with source.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def universal_items(doc: dict[str, Any]) -> list[str]:
    """Пункты универсальных требований (части, заданные ``_UNIVERSAL_PARTS``)."""
    items: list[str] = []
    for part in doc.get("parts") or []:
        if part.get("number") in _UNIVERSAL_PARTS:
            for it in part.get("items") or []:
                if it.get("text"):
                    items.append(it["text"])
    return items


def _terms(text: str) -> set[str]:
    """Значимые термы текста: словоформы без шума и коротких/цифровых токенов."""
    tokens = _TOKEN_RE.findall(_PUNCT_RE.sub(" ", text.lower()))
    terms: set[str] = set()
    for tok in tokens:
        if len(tok) < 4 or tok in _STOPWORDS or tok.isdigit():
            continue
        terms.add(tok[:8])  # срез — простое «стеммирование» по основе слова
    return terms


def _best_match(proc_text: str, law_items: list[str]) -> float:
    """Максимальная доля терминов эталонного требования, покрытая текстом закупки."""
    proc_terms = _terms(proc_text)
    if not proc_terms:
        return 0.0
    best = 0.0
    for law in law_items:
        law_terms = _terms(law)
        if not law_terms:
            continue
        score = len(proc_terms & law_terms) / len(law_terms)
        best = max(best, score)
    return best


def universal_overlap(proc_text: str, doc: dict[str, Any]) -> float:
    """0..1 — насколько текст покрывает какое-либо универсальное требование закона."""
    return _best_match(proc_text, universal_items(doc))


def is_universal(proc_text: str, doc: dict[str, Any]) -> bool:
    """Является ли требование закупки дословным повторением универсального (ст. 31 ч. 1)."""
    return universal_overlap(proc_text, doc) >= _MATCH_THRESHOLD


def is_negated(item: dict[str, Any]) -> bool:
    """Является ли требование «отрицанием» нормы.

    Маркеры («не установлено», «не требуется» и т.п.) конвертируются в «НЕТ»
    на этапе извлечения (в ``additional`` либо в тексте) — проверяем именно «НЕТ»,
    чтобы не ловить обороты вида «не установлено иное».
    """
    return "НЕТ" in (item.get("additional") or "") or "НЕТ" in (item.get("text") or "")


def annotate_requirements(
    structure: dict[str, Any], doc: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Добавить каждому требованию флаги ``universal``/``negated`` для показа.

    Правило показа: ``show = not universal or negated``.
    """
    if doc is None:
        doc = load_law_requirements()
    law_items = universal_items(doc)
    for entries in structure.values():
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            uni = _best_match(item.get("text") or "", law_items) >= _MATCH_THRESHOLD
            neg = is_negated(item)
            item["universal"] = uni
            item["negated"] = neg
            item["show"] = not uni or neg
    return structure


__all__ = [
    "annotate_requirements",
    "is_negated",
    "is_universal",
    "load_law_requirements",
    "universal_items",
    "universal_overlap",
]
