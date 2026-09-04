"""Сопоставление требований закупки с требованиями 44-ФЗ.

Извещение в основном повторяет нормы 44-ФЗ (ст. 31 «Требования к участникам» и
смежные статьи) — это «закон-дайджест», отвлекающий от специфики закупки. Зонная
классификация по «похожести на норму» (два порога):

* покрытие ``≥`` верхнего → дословный пересказ нормы → **исключаем**;
* покрытие ``≤`` нижнего → «не норма» → **пока всегда оставляем** (фильтр по
  «настоящему значению» параметра отложен);
* между порогами → неоднозначно: помечаем ``embed_review`` для последующей
  обработки эмбеддингами (если включено в профиле);
* ``negated`` (отклонения «не установлено», «не требуется»…) — всегда оставляем.

Здесь — детерминированная часть: загрузка реестра требований закона (JSON),
признак «отрицание» (маркер → ``negated``, без дублирующего значения «НЕТ») и
двухпороговое сопоставление с корпусом нормы (``law_match``).
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


def _best_match(proc_text: str, law_texts: list[str]) -> float:
    """Максимальная доля терминов эталонного текста закона, покрытая текстом закупки.

    Слишком короткие тексты норм (≈заголовки) отбрасываем: пара слов вроде
    «Дополнительные требования.» давала бы покрытие 1.0 на любом «дополнительном».
    """
    proc_terms = _terms(proc_text)
    if not proc_terms:
        return 0.0
    best = 0.0
    for law in law_texts:
        law_terms = _terms(law)
        if len(law_terms) < 3:
            continue
        score = len(proc_terms & law_terms) / len(law_terms)
        best = max(best, score)
    return best


def universal_overlap(proc_text: str, doc: dict[str, Any]) -> float:
    """0..1 — насколько текст покрывает какое-либо универсальное требование закона (ч. 1)."""
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


# Корпус нормы «Требования к участникам»: тексты всех частей ст.31 и их пункты —
# то, с чем сравниваем требование закупки (пересказ нормы). НЕ только ч.1.
_LAW_MATCH_RESTATE = 0.75  # покрытие ≥ → дословный пересказ нормы → исключить
_LAW_MATCH_UNCLEAR = 0.30  # покрытие ≤ → низкая зона (не норма)
_LAW_VALUE_TEXT_MAX = 150  # макс. длина текста «настоящего значения» параметра

# Конкретное значение закупки (дата/время/№/сумма) — то, что является настоящим
# значением параметра, а не пересказом нормы.
_CONCRETE_VALUE_RE = re.compile(
    r"\d{2}\.\d{2}\.\d{2,4}"
    r"|\b\d{1,2}:\d{2}\b"
    r"|№\s*\d+"
    r"|\b\d+\s*[₽%]\b",
    re.IGNORECASE,
)


def _restatement_corpus(doc: dict[str, Any]) -> list[str]:
    """Все тексты нормы (части + пункты) из реестра — то, что может пересказываться."""
    texts: list[str] = []
    for part in doc.get("parts") or []:
        if part.get("text"):
            texts.append(part["text"])
        for it in part.get("items") or []:
            if it.get("text"):
                texts.append(it["text"])
    return texts


def law_match(text: str, doc: dict[str, Any]) -> float:
    """0..1 — насколько требование похоже на норму 44-ФЗ (пересказ)."""
    return _best_match(text, _restatement_corpus(doc))


def is_real_value(item: dict[str, Any]) -> bool:
    """Является ли ``additional`` настоящим значением параметра (коротким + со значением).

    Порог длины текста: настоящие значения (дата/сумма/№, короткий ответ) — короткие и
    содержат конкретное значение; длинный текст без конкретного значения — пересказ нормы.
    """
    add = (item.get("additional") or "").strip()
    if not add or add == "НЕТ":
        return False
    if len(add) > _LAW_VALUE_TEXT_MAX:
        return False
    return bool(_CONCRETE_VALUE_RE.search(add))


def annotate_requirements(
    structure: dict[str, Any], doc: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Зонная классификация требований (два порога похожести на норму).

    * покрытие ``≥`` верхнего → дословный пересказ нормы → исключить;
    * покрытие ``≤`` нижнего → «не норма»: пока всегда оставляем (фильтр «настоящего
      значения» параметра отложен — ``is_real_value`` готов к включению);
    * между порогами → неоднозначно: пометить ``embed_review`` для последующей
      обработки эмбеддингами (если включено в профиле).
    * ``negated`` (отклонения) — всегда оставляем с флагом ``negated``.
    """
    if doc is None:
        doc = load_law_requirements()
    for key in list(structure.keys()):
        entries = structure.get(key)
        if not isinstance(entries, list):
            continue
        kept: list[Any] = []
        for item in entries:
            if not isinstance(item, dict):
                kept.append(item)
                continue
            neg = is_negated(item) or bool(item.get("negated"))
            # убрать дубль: значение-маркер уже выражаем флагом negated.
            if (item.get("additional") or "") == "НЕТ":
                item.pop("additional", None)
            item.pop("show", None)
            item.pop("universal", None)
            if neg:
                item["negated"] = True
                kept.append(item)
                continue
            score = law_match(item.get("text") or "", doc)
            if score >= _LAW_MATCH_RESTATE:
                continue  # дословный пересказ нормы — исключить
            if score <= _LAW_MATCH_UNCLEAR:
                kept.append(item)  # нижняя зона — пока всегда оставляем (решение отложено)
                continue
            item["embed_review"] = True  # неоднозначно — на проверку эмбеддингами
            kept.append(item)
        if kept:
            structure[key] = kept
        else:
            del structure[key]
    return structure


__all__ = [
    "annotate_requirements",
    "is_negated",
    "is_real_value",
    "is_universal",
    "law_match",
    "load_law_requirements",
    "universal_items",
    "universal_overlap",
]
