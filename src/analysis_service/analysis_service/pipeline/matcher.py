"""Stage B: сопоставление фактов ТЗ с фактами профиля (детерминированные правила).

Этап извлечения фактов из текста ТЗ (Stage A, LLM) отделён от сравнения с профилем:
в промпт профиль не попадает. Здесь же — правила BR-03/BR-04/US-4.4 над
извлечёнными фактами и фактами профиля (лицензии, подтверждённый опыт). Чистый код,
без LLM: стоимость этапа ≈ 0. Нераспознанный вид лицензии не отсеивает закупку —
мягкий маркер «требует проверки» (recall-over-precision, решение за тендерологом).
"""

from __future__ import annotations

import re
from typing import Any

from analysis_service.pipeline.system_questions import (
    SYSTEM_QUESTIONS,
    SYSTEM_QUESTIONS_VERSION,
)

# Внутренние «виды» лицензий (дескрипторы для детекции из текста ТЗ). Это НЕ коды
# справочника license_types (у них теперь только name): дескрипторы нужны матчеру,
# чтобы понять, что за лицензия требуется, и сопоставить её с названиями в профиле.
LICENSE_KINDS = {
    "fstek",
    "fsb",
    "fsb_gostayna",
    "mincifry",
    "roscomnadzor",
    "minpromtorg",
    "mchs",
    "rosgvardia",
    "education",
}

# Лексические синонимы «вид лицензии» → дескриптор (дёшево и без LLM).
LICENSE_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"фстэк|техническ\w*\s+защит\w*\s+информаци|техзащит"), "fstek"),
    (re.compile(r"защит\w*\s+информаци"), "fstek"),
    # Гостайна должна быть распознана раньше общего «фсб» (иначе «УФСБ» утянет в fsb).
    (
        re.compile(r"гостайн|государственн\w*\s+тайн|степень\w*\s+секретно|совершенн\w*\s+секретн"),
        "fsb_gostayna",
    ),
    (re.compile(r"фсб|криптограф|шифрован|криптосредств"), "fsb"),
    (re.compile(r"минц"), "mincifry"),
    (re.compile(r"роскомнадзор|услуг\w*\s+связ"), "roscomnadzor"),
    (re.compile(r"минпромторг"), "minpromtorg"),
    (re.compile(r"мчс|пожарн"), "mchs"),
    (re.compile(r"росгварди|частн\w*\s+охранн|охранн\w*\s+деятельн"), "rosgvardia"),
    (re.compile(r"образован\w*|образоват\w*"), "education"),
]

# Маркеры попадания дескриптора в НАЗВАНИЕ лицензии профиля (license_types.name):
# если хотя бы один маркер встречается в любом названии лицензии — вид «есть».
LICENSE_KIND_MARKERS: dict[str, list[str]] = {
    "fstek": ["фстэк", "техзащит", "конфиденциальн"],
    "fsb": ["криптограф", "шифрован", "шифрованн", "фсб"],
    "fsb_gostayna": ["гостайн", "государственн", "секретн"],
    "mincifry": ["минц"],
    "roscomnadzor": ["роскомнадзор", "радиочастотн"],
    "minpromtorg": ["минпромторг"],
    "mchs": ["мчс", "пожар"],
    "rosgvardia": ["охранн"],
    "education": ["образован"],
}

# Человекочитаемые названия видов лицензий для сводки в отчёте (карточка закупки).
LICENSE_KIND_LABELS: dict[str, str] = {
    "fstek": "Лицензия ФСТЭК (техническая защита информации)",
    "fsb": "Лицензия ФСБ (криптография)",
    "fsb_gostayna": "Лицензия ФСБ (государственная тайна)",
    "mincifry": "Лицензия Минцифры",
    "roscomnadzor": "Лицензия Роскомнадзора (услуги связи)",
    "minpromtorg": "Разрешение Минпромторга",
    "mchs": "Лицензия МЧС (пожарная безопасность)",
    "rosgvardia": "Лицензия Росгвардии (частная охрана)",
    "education": "Лицензия на образовательную деятельность",
}

VERDICT_NONE = "no_stop_condition"
VERDICT_SOFT = "soft"
VERDICT_ABSOLUTE = "absolute"
VERDICT_UNAVAILABLE = "unavailable"

MARKERS: dict[str, str] = {
    VERDICT_ABSOLUTE: "🔴",
    VERDICT_SOFT: "🟡",
    VERDICT_NONE: "🟢",
    VERDICT_UNAVAILABLE: "⚪",
}

SEVERITY: dict[str, int] = {
    VERDICT_NONE: 0,
    VERDICT_SOFT: 1,
    VERDICT_ABSOLUTE: 2,
    VERDICT_UNAVAILABLE: 0,
}


def _facts(block: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(block, dict):
        return {}
    facts = block.get("facts")
    return facts if isinstance(facts, dict) else {}


def _found(block: dict[str, Any] | None) -> bool:
    if not isinstance(block, dict):
        return False
    return bool(block.get("found"))


def _excerpt(block: dict[str, Any] | None) -> str | None:
    if not isinstance(block, dict):
        return None
    return str(block.get("excerpt") or "").strip()[:500] or None


def _reasoning(block: dict[str, Any] | None, fallback: str) -> str:
    if isinstance(block, dict) and block.get("reasoning"):
        return str(block["reasoning"])
    return fallback


def resolve_license_kind(facts: dict[str, Any]) -> str | None:
    """Нормализовать вид лицензии из фактов ТЗ до внутреннего дескриптора.

    Сначала берётся ``license_code``, указанный LLM; если он не назван или
    не распознан дескриптором — лексический матч по названию/органу/обоснованию.
    Возвращает None, если вид не распознан.
    """
    code = str(facts.get("license_code") or "").strip()
    if code in LICENSE_KINDS:
        return code
    haystack = " ".join(
        str(facts.get(k) or "") for k in ("license_name", "authority", "reasoning")
    ).lower()
    haystack = re.sub(r"[^а-яёa-z0-9 ]", " ", haystack)
    for pattern, mapped in LICENSE_ALIASES:
        if pattern.search(haystack):
            return mapped
    return None


def kind_in_profile(kind: str, license_names: list[str]) -> bool:
    """Есть ли в профиле лицензия, относящаяся к дескриптору ``kind``.

    Сопоставление по маркерам вида, встречающимся в названиях лицензий профиля
    (``license_types.name``). Возвращается истина, если любой маркер вида найден
    в любом названии — recall-over-precision.
    """
    if not license_names:
        return False
    haystack = " ".join(license_names).lower()
    return any(marker in haystack for marker in LICENSE_KIND_MARKERS.get(kind, []))


def license_kinds_in_text(text: str) -> list[str]:
    """Все виды лицензий, лексически упомянутые в тексте (детерминированный fallback).

    Используется, когда LLM-разбор требования недоступен (аккаунт без платной
    опции): вид определяется по тем же синонимам (``LICENSE_ALIASES``), что и
    ``resolve_license_kind``. «Гостайна» — частный случай ФСБ, поэтому при её
    наличии общий «фсб» не дублируется.
    """
    haystack = re.sub(r"[^а-яёa-z0-9 ]", " ", (text or "").lower())
    found: list[str] = []
    for pattern, mapped in LICENSE_ALIASES:
        if pattern.search(haystack) and mapped not in found:
            found.append(mapped)
    if "fsb_gostayna" in found and "fsb" in found:
        found.remove("fsb")
    return found


def _name_in_profile(name: str, license_names: list[str]) -> bool:
    """Лексическое совпадение названия требования с названиями лицензий профиля.

    Fallback для видов, которые не удалось нормализовать (``resolve_license_kind``
    вернул None): проверяем вхождение строки в любую сторону.
    """
    needle = re.sub(r"\s+", " ", (name or "").strip().lower())
    if len(needle) < 4:
        return False
    return any(needle in p.lower() or p.lower() in needle for p in license_names if p)


def build_license_summary(
    requirements: dict[str, Any] | None, license_names: list[str] | None
) -> dict[str, Any]:
    """Компактная сводка по лицензиям для отчёта: что требуется и есть ли в профиле.

    Источник требуемых видов — LLM-поле ``data`` элемента требований
    (``kinds``/``code``), если оно заполнено (аккаунт с платной LLM-опцией); иначе —
    детерминированный лексический разбор текста (``license_kinds_in_text``).
    Признаки «не требуется» (``negated`` от маркеров «не установлено»/«не
    требуется», либо ``data.required=false``) исключают требование из сводки.

    Элемент сводки: ``{label, kind, type, name, authority, available}``, где
    ``available`` — ``True``/``False`` (сопоставление с ``license_names`` профиля,
    ``kind_in_profile``) либо ``None`` (вид не распознан — нужна проверка).
    """
    names = [str(n) for n in (license_names or []) if str(n).strip()]
    raw = (requirements or {}).get("licenses")
    if isinstance(raw, dict):
        items_in: list[Any] = [raw]
    elif isinstance(raw, list):
        items_in = raw
    else:
        items_in = []

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    any_required = False
    any_item = False

    def add(
        label: str,
        kind: str | None,
        ktype: str,
        name: str,
        authority: str | None,
        available: bool | None,
    ) -> None:
        key = kind or label.lower()
        if not label or key in seen:
            return
        seen.add(key)
        entries.append(
            {
                "label": label,
                "kind": kind,
                "type": ktype,
                "name": name,
                "authority": authority,
                "available": available,
            }
        )

    for item in items_in:
        if not isinstance(item, dict):
            continue
        any_item = True
        negated = bool(item.get("negated"))
        raw_data = item.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        required = data.get("required") if isinstance(data.get("required"), bool) else not negated
        if not required:
            continue
        any_required = True

        raw_kinds = data.get("kinds")
        kinds: list[Any] = raw_kinds if isinstance(raw_kinds, list) else []
        for kind in kinds:
            if not isinstance(kind, dict):
                continue
            name = str(kind.get("name") or "").strip()
            ktype = str(kind.get("type") or "license").strip() or "license"
            authority = str(kind.get("authority") or "").strip() or None
            desc = resolve_license_kind(
                {
                    "license_code": kind.get("code"),
                    "license_name": name,
                    "authority": authority,
                }
            )
            if desc:
                add(
                    LICENSE_KIND_LABELS[desc],
                    desc,
                    ktype,
                    name,
                    authority,
                    kind_in_profile(desc, names),
                )
            else:
                available = _name_in_profile(name, names) if name else None
                add(name or "Вид не распознан", None, ktype, name, authority, available)

        if not kinds:
            # Детерминированный fallback: LLM-разбора нет — виды из текста требования.
            for desc in license_kinds_in_text(item.get("text") or ""):
                add(
                    LICENSE_KIND_LABELS[desc],
                    desc,
                    "license",
                    "",
                    None,
                    kind_in_profile(desc, names),
                )

    if any_required and not entries:
        # Требование есть, но вид не распознан ни LLM, ни лексически.
        add("Допуск/лицензия (вид не распознан)", None, "license", "", None, None)

    return {
        "found": any_item,
        "required": any_required,
        "negated": any_item and not any_required,
        "items": entries,
    }


def requirement_category_status(requirements: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """Статус категории требований: найдено / требуется / «не требуется».

    ``required`` — есть хотя бы один НЕ-отрицаемый пункт (LLM ``data.required``,
    если заполнено, иначе отсутствие ``negated``). Нужен отчёту, чтобы явно
    показать «требований не найдено» / «не требуется» (лицензии/опыт/Минпромторг).
    """
    raw = (requirements or {}).get(key)
    if isinstance(raw, dict):
        items: list[Any] = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    if not items:
        return {"found": False, "required": False, "negated": False}
    any_required = False
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_data = item.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        req = (
            data.get("required")
            if isinstance(data.get("required"), bool)
            else not bool(item.get("negated"))
        )
        if req:
            any_required = True
            break
    return {"found": True, "required": any_required, "negated": not any_required}


def _verdict(
    question_id: str,
    question_text: str,
    verdict: str,
    marker: str,
    reason: str,
    excerpt: str | None,
    facts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "question_text": question_text,
        "verdict": verdict,
        "severity": SEVERITY[verdict],
        "marker": marker,
        "excerpt": excerpt,
        "reasoning": reason,
        "source": "system",
        "question_version": SYSTEM_QUESTIONS_VERSION,
        "facts": facts,
    }


def _experience_verdict(
    block: dict[str, Any] | None, profile_facts: dict[str, Any]
) -> dict[str, Any]:
    question = next((q for q in SYSTEM_QUESTIONS if q["id"] == "sys:exp_2571"), {})
    qid = question.get("id", "sys:exp_2571")
    qtext = question.get("text", "")
    facts = _facts(block)
    if not _found(block) or not facts.get("required"):
        return _verdict(
            qid,
            qtext,
            VERDICT_NONE,
            MARKERS[VERDICT_NONE],
            "Требование к опыту в ТЗ не установлено",
            _excerpt(block),
            facts,
        )

    confirmation = str(facts.get("confirmation") or "").strip() or None
    experience_codes = profile_facts.get("experience_codes") or []
    if confirmation == "evaluation_only":
        return _verdict(
            qid,
            qtext,
            VERDICT_NONE,
            MARKERS[VERDICT_NONE],
            "Опыт упоминается только в критериях оценки — не барьер допуска",
            _excerpt(block),
            facts,
        )
    if confirmation == "platform":
        if "platform" in experience_codes:
            return _verdict(
                qid,
                qtext,
                VERDICT_NONE,
                MARKERS[VERDICT_NONE],
                "Требуется подтверждение опыта на площадке; подтверждённый опыт в профиле есть",
                _excerpt(block),
                facts,
            )
        return _verdict(
            qid,
            qtext,
            VERDICT_ABSOLUTE,
            MARKERS[VERDICT_ABSOLUTE],
            "Требуется подтверждение опыта на площадке (ПП 2571) — в профиле нет",
            _excerpt(block),
            facts,
        )
    if confirmation in ("documents", "registry"):
        return _verdict(
            qid,
            qtext,
            VERDICT_SOFT,
            MARKERS[VERDICT_SOFT],
            "Опыт допускает подтверждение сканами актов/выпиской из реестра — мягкое требование",
            _excerpt(block),
            facts,
        )
    return _verdict(
        qid,
        qtext,
        VERDICT_SOFT,
        MARKERS[VERDICT_SOFT],
        "Форма подтверждения опыта неоднозначна — требуется проверка",
        _excerpt(block),
        facts,
    )


def _minprom_verdict(block: dict[str, Any] | None, profile_facts: dict[str, Any]) -> dict[str, Any]:
    question = next((q for q in SYSTEM_QUESTIONS if q["id"] == "sys:minprom_registry"), {})
    qid = question.get("id", "sys:minprom_registry")
    qtext = question.get("text", "")
    facts = _facts(block)
    if not _found(block) or not facts.get("required"):
        return _verdict(
            qid,
            qtext,
            VERDICT_NONE,
            MARKERS[VERDICT_NONE],
            "Требование реестра Минпромторга не установлено (или есть пометка «не установлено»)",
            _excerpt(block),
            facts,
        )
    return _verdict(
        qid,
        qtext,
        VERDICT_ABSOLUTE,
        MARKERS[VERDICT_ABSOLUTE],
        "Требуется выписка из реестра Минпромторга (запрет иностранной продукции)",
        _excerpt(block),
        facts,
    )


def _license_verdict(block: dict[str, Any] | None, profile_facts: dict[str, Any]) -> dict[str, Any]:
    question = next((q for q in SYSTEM_QUESTIONS if q["id"] == "sys:license_sro"), {})
    qid = question.get("id", "sys:license_sro")
    qtext = question.get("text", "")
    facts = _facts(block)
    if not _found(block) or not facts.get("required"):
        return _verdict(
            qid,
            qtext,
            VERDICT_NONE,
            MARKERS[VERDICT_NONE],
            "Обязательная лицензия/СРО/допуск в ТЗ не требуются",
            _excerpt(block),
            facts,
        )

    license_names = profile_facts.get("license_names") or []
    kind = resolve_license_kind(facts)
    if kind is None:
        return _verdict(
            qid,
            qtext,
            VERDICT_SOFT,
            MARKERS[VERDICT_SOFT],
            "Требуется допуск, вид не распознан справочником — требует проверки",
            _excerpt(block),
            facts,
        )
    if kind_in_profile(kind, license_names):
        return _verdict(
            qid,
            qtext,
            VERDICT_NONE,
            MARKERS[VERDICT_NONE],
            f"Требуется {kind}; лицензия этого вида в профиле есть",
            _excerpt(block),
            facts,
        )
    return _verdict(
        qid,
        qtext,
        VERDICT_ABSOLUTE,
        MARKERS[VERDICT_ABSOLUTE],
        f"Требуется {kind}; лицензии этого вида в профиле нет",
        _excerpt(block),
        facts,
    )


def apply_profile_facts(
    extractions: dict[str, Any], profile_facts: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Сопоставить факты ТЗ (по системным проверкам) с фактами профиля.

    ``extractions`` — ответ Stage A (batch_system): ключи ``experience_2571``,
    ``minprom_registry``, ``license_sro``. ``profile_facts`` — ``{"license_names": [...],
    "experience_codes": [...]}``. Возвращает список вердиктов по системным вопросам.
    """
    profile_facts = profile_facts or {}
    return [
        _experience_verdict(extractions.get("experience_2571"), profile_facts),
        _minprom_verdict(extractions.get("minprom_registry"), profile_facts),
        _license_verdict(extractions.get("license_sro"), profile_facts),
    ]
