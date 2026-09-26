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
    return _best_match_detailed(proc_text, law_texts)[0]


def _best_match_detailed(proc_text: str, law_texts: list[str]) -> tuple[float, float]:
    """(recall, precision) для лучшего по recall совпадения с текстами закона.

    ``recall`` — доля терминов нормы, покрытая текстом закупки (как ``_best_match``).
    ``precision`` — обратная доля: сколько из терминов ИМЕННО ЭТОГО текста закупки
    объясняется данным пунктом нормы. Нужна, чтобы отличить «дословный пересказ»
    (высокие и recall, и precision — весь текст закупки — это норма) от «ссылки на
    статью закона + дальше специфичное значение» (высокий recall — типовая фраза
    ссылки покрыта, но низкий precision — бо́льшая часть текста закупки — это НЕ
    норма, а именно специфика). См. ``annotate_requirements``.
    """
    proc_terms = _terms(proc_text)
    if not proc_terms:
        return 0.0, 0.0
    best_recall, best_precision = 0.0, 0.0
    for law in law_texts:
        law_terms = _terms(law)
        if len(law_terms) < 3:
            continue
        overlap = len(proc_terms & law_terms)
        recall = overlap / len(law_terms)
        if recall > best_recall:
            best_recall = recall
            best_precision = overlap / len(proc_terms)
    return best_recall, best_precision


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
_LAW_MATCH_RESTATE = 0.75  # recall ≥ → дословный пересказ нормы → исключить...
# ...НО только если ещё и precision ≥ этого порога: иначе типовая фраза-ссылка на
# статью («предусмотренные п.1 ч.1 ст.31 Закона») перед специфичным, длинным и
# конкретным значением (напр. конкретная лицензия с номером постановления и
# кодами отходов) исключала бы это значение целиком — recall по короткой фразе
# ссылки был бы высоким при почти любом продолжении. Найдено на реальных данных.
_LAW_MATCH_PRECISION_MIN = 0.5
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
    """0..1 — насколько требование похоже на норму 44-ФЗ (пересказ, recall)."""
    return _best_match(text, _restatement_corpus(doc))


def law_match_detailed(text: str, doc: dict[str, Any]) -> tuple[float, float]:
    """(recall, precision) относительно наиболее похожего пункта/части нормы.

    См. ``_best_match_detailed`` — используется решением об исключении в
    ``annotate_requirements`` (одного recall недостаточно, чтобы отличить
    «дословный пересказ» от «ссылка на статью + специфичное значение»).
    """
    return _best_match_detailed(text, _restatement_corpus(doc))


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

    * recall ``≥`` верхнего И precision ``≥`` своего порога → дословный пересказ
      нормы → исключить (оба порога — иначе ссылка на статью закона перед
      длинным специфичным значением исключала бы значение целиком, см.
      ``law_match_detailed``);
    * recall ``≤`` нижнего → «не норма»: пока всегда оставляем (фильтр «настоящего
      значения» параметра отложен — ``is_real_value`` готов к включению);
    * между порогами (по recall) → неоднозначно: пометить ``embed_review`` для
      последующей обработки эмбеддингами (если включено в профиле).
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
            # Табличные строки (``_table_requirement_candidates``): у части
            # строк ``text`` — только номер + заголовок-ссылка на статью
            # закона, а САМО значение — в ``additional`` (3-я ячейка); у
            # других (нет отдельной 3-й ячейки) заголовок и значение слиты в
            # одном ``text``. Сравниваем ту часть, где есть значение.
            item_text = item.get("additional") or item.get("text") or ""
            recall, precision = law_match_detailed(item_text, doc)
            # Исключаем, только если пересказ ЗАНИМАЕТ БОЛЬШУЮ ЧАСТЬ текста
            # требования (precision), а не только если типовая фраза-ссылка на
            # статью («…предусмотренные п.1 ч.1 ст.31 Закона о контрактной
            # системе») покрыта целиком (recall) — иначе такая ссылка перед
            # длинным специфичным значением (найдено на реальных данных:
            # конкретная лицензия с номером постановления и кодами отходов)
            # исключала бы значение целиком из-за высокого recall при почти
            # любом продолжении текста.
            if recall >= _LAW_MATCH_RESTATE and precision >= _LAW_MATCH_PRECISION_MIN:
                continue  # дословный пересказ нормы — исключить
            if recall <= _LAW_MATCH_UNCLEAR:
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
    "law_match_detailed",
    "load_law_requirements",
    "universal_items",
    "universal_overlap",
]
