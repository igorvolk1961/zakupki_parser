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

# Коды справочника license_types (сид BR-03, repository.py).
LICENSE_CODES = {
    "fstek",
    "fsb",
    "fsb_gostayna",
    "mincifry",
    "roscomnadzor",
    "minpromtorg",
    "mchs",
    "rosgvardia",
    "education",
    "other",
}

# Лексические синонимы «вид лицензии» → код справочника (дёшево и без LLM).
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

VERDICT_NONE = "no_stop_condition"
VERDICT_SOFT = "soft"
VERDICT_ABSOLUTE = "absolute"

MARKERS: dict[str, str] = {
    VERDICT_ABSOLUTE: "🔴",
    VERDICT_SOFT: "🟡",
    VERDICT_NONE: "🟢",
}

SEVERITY: dict[str, int] = {VERDICT_NONE: 0, VERDICT_SOFT: 1, VERDICT_ABSOLUTE: 2}


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


def resolve_license_code(facts: dict[str, Any]) -> str | None:
    """Нормализовать вид лицензии из фактов ТЗ до кода справочника.

    Сначала берётся ``license_code``, указанный LLM; если он не назван или
    равен ``other`` — лексический матч по названию/органу/обоснованию.
    Возвращает None, если вид не распознан.
    """
    code = str(facts.get("license_code") or "").strip()
    if code in LICENSE_CODES and code != "other":
        return code
    haystack = " ".join(
        str(facts.get(k) or "") for k in ("license_name", "authority", "reasoning")
    ).lower()
    haystack = re.sub(r"[^а-яёa-z0-9 ]", " ", haystack)
    for pattern, mapped in LICENSE_ALIASES:
        if pattern.search(haystack):
            return mapped
    return None


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
            "Требуется подтверждение опыта на площадке (ПП РФ 2571) — в профиле нет",
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

    license_codes = profile_facts.get("license_codes") or []
    code = resolve_license_code(facts)
    if code is None:
        return _verdict(
            qid,
            qtext,
            VERDICT_SOFT,
            MARKERS[VERDICT_SOFT],
            "Требуется допуск, вид не распознан справочником — требует проверки",
            _excerpt(block),
            facts,
        )
    if code in license_codes:
        return _verdict(
            qid,
            qtext,
            VERDICT_NONE,
            MARKERS[VERDICT_NONE],
            f"Требуется {code}; лицензия этого типа в профиле есть",
            _excerpt(block),
            facts,
        )
    return _verdict(
        qid,
        qtext,
        VERDICT_ABSOLUTE,
        MARKERS[VERDICT_ABSOLUTE],
        f"Требуется {code}; лицензия этого типа в профиле отсутствует",
        _excerpt(block),
        facts,
    )


def apply_profile_facts(
    extractions: dict[str, Any], profile_facts: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Сопоставить факты ТЗ (по системным проверкам) с фактами профиля.

    ``extractions`` — ответ Stage A (batch_system): ключи ``experience_2571``,
    ``minprom_registry``, ``license_sro``. ``profile_facts`` — ``{"license_codes": [...],
    "experience_codes": [...]}``. Возвращает список вердиктов по системным вопросам.
    """
    profile_facts = profile_facts or {}
    return [
        _experience_verdict(extractions.get("experience_2571"), profile_facts),
        _minprom_verdict(extractions.get("minprom_registry"), profile_facts),
        _license_verdict(extractions.get("license_sro"), profile_facts),
    ]
