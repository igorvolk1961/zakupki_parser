"""Вердикт приемлемости закупки (единый отчёт).

Учитывает: требования к участнику (лицензии/опыт/минпромторг/соисполнители,
``scoring_common.requirements``, детерминированно, без LLM — профиль решает,
какие из НАЙДЕННЫХ (не отрицаемых) требований блокируют приемлемость,
``profiles.requirement_blocking``); отчётные LLM-поля с ``blocking=True`` и
``match=False`` (значение поля, извлечённое из документов, не удовлетворяет
условию поля — ``scoring_common.conditions``; это и есть стоп-условие,
задаваемое пользователем). Поле, которое проверить не удалось
(``match=None``: не найдено, сбой LLM, нужен повторный анализ), не блокирует.

Чистый код без I/O: вызывается воркером анализа и API (пересчёт условий без
LLM после правки профиля).
"""

from __future__ import annotations

from typing import Any

_REQUIREMENT_KEYS = ("licenses", "experience", "minprom", "subcontractors")
_REQUIREMENT_LABELS: dict[str, str] = {
    "licenses": "Лицензии",
    "experience": "Опыт исполнения",
    "minprom": "Требования Минпромторга",
    "subcontractors": "Допустимость привлечения соисполнителей",
}


def compute_requirements_verdict(
    requirements: dict[str, Any], requirement_blocking: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Интерпретация ``requirements_json`` под профиль.

    Возвращает ``{category: {blocking, negated, count}}`` только для категорий,
    где вообще что-то найдено. ``negated=True`` — ВСЕ найденные пункты
    категории явно не создают барьера (маркер «не требуется»/явное разрешение
    у соисполнителей) → ``blocking=False`` всегда. Иначе — есть хотя бы один
    реальный пункт → ``blocking`` берётся из настройки профиля.
    """
    result: dict[str, dict[str, Any]] = {}
    for key in _REQUIREMENT_KEYS:
        items = requirements.get(key)
        if not items or not isinstance(items, list):
            continue
        entries = [it for it in items if isinstance(it, dict)]
        if not entries:
            continue
        all_negated = all(bool(it.get("negated")) for it in entries)
        if all_negated:
            result[key] = {"blocking": False, "negated": True, "count": len(entries)}
        else:
            result[key] = {
                "blocking": bool(requirement_blocking.get(key)),
                "negated": False,
                "count": len(entries),
            }
    return result


def compute_verdict(
    requirements: dict[str, Any],
    requirement_blocking: dict[str, Any],
    fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Вердикт приемлемости закупки: ``{requirements_verdict, verdict}``.

    ``verdict.accepted=False``, если хотя бы одна категория требований
    заблокирована (профиль отметил её блокирующей, и реальное требование
    найдено) ИЛИ хотя бы одно отчётное поле с ``blocking=True`` получило
    ``match=False`` (значение не удовлетворяет условию поля)
    — закупку следует авто-отклонить (см. вызывающий код,
    ``analysis_service.worker``/``upsert_score.auto_rejected`` и пересчёт
    условий в API).
    """
    requirements_verdict = compute_requirements_verdict(requirements, requirement_blocking)
    blocking_reasons = [
        {"source": key, "label": _REQUIREMENT_LABELS.get(key, key)}
        for key, info in requirements_verdict.items()
        if info.get("blocking")
    ]
    for field in fields or []:
        if isinstance(field, dict) and field.get("blocking") and field.get("match") is False:
            name = str(field.get("field_name") or "").strip() or "Отчётное поле"
            source = f"field:{field.get('field_id')}"
            blocking_reasons.append({"source": source, "label": name})
    return {
        "requirements_verdict": requirements_verdict,
        "verdict": {"accepted": not blocking_reasons, "blocking_reasons": blocking_reasons},
    }


__all__ = ["compute_verdict", "compute_requirements_verdict"]
