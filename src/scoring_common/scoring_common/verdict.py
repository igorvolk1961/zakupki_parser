"""Вердикт приемлемости закупки (единый отчёт).

Стоп-условия задаёт профиль, у каждого — уровень:

- ``block`` — жёсткий барьер: закупка не принята и авто-отклоняется;
- ``soft`` — мягкий барьер: закупка принята, но её P(win) снижается
  (``soft_pwin_factor`` за каждое мягкое нарушение, применяет API — см.
  ``EvaluationMixin._apply_pwin_penalty``);
- ``off`` — не учитывается (только показывается в отчёте).

Источники:

- требования к участнику (``scoring_common.requirements``, детерминированно):
  лицензии, Минпромторг, соисполнители — уровень задаёт профиль
  (``profiles.requirement_severity``); **опыт — по правилу BR-03**, если
  категория включена: подтверждение через электронную площадку (ПП 2571) без
  опыта «через площадку» в профиле — жёсткий барьер; сканы договоров/актов или
  выписка из реестра контрактов — мягкий; опыт только в критериях оценки — не
  барьер; способ подтверждения не распознан — мягкий (решение за тендерологом).
  Требование с отметкой «не установлено»/«не требуется» (``negated``) не барьер;
- отчётные поля с условием (``scoring_common.conditions``): невыполненное
  условие (``match=False``) — барьер уровня поля (``severity``); непроверенное
  (``match=None``) — не барьер.

Чистый код без I/O: вызывается воркером анализа и API (пересчёт без LLM).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

Severity = Literal["block", "soft"]
SEVERITIES = ("block", "soft", "off")
# Опыт: правило BR-03 или «не учитывать».
EXPERIENCE_SEVERITIES = ("br03", "off")
REQUIREMENT_KEYS = ("licenses", "experience", "minprom", "subcontractors")
_REQUIREMENT_LABELS: dict[str, str] = {
    "licenses": "Лицензии",
    "experience": "Опыт исполнения",
    "minprom": "Требования Минпромторга",
    "subcontractors": "Допустимость привлечения соисполнителей",
}
CONFIRMATIONS = ("platform", "documents", "registry", "evaluation_only")
_CONFIRMATION_LABELS = {
    "platform": "подтверждение через электронную площадку (ПП 2571)",
    "documents": "подтверждение копиями договоров/актов",
    "registry": "подтверждение выпиской из реестра контрактов",
    "evaluation_only": "опыт только в критериях оценки",
    None: "способ подтверждения не распознан",
}

# Способ подтверждения опыта по тексту требования (когда LLM-поле data не
# заполнено): площадка важнее документов, документы — важнее критериев оценки.
_PLATFORM_RE = re.compile(r"2571|электронн\w*\s+площадк|на\s+площадк\w*\s+оператор")
_REGISTRY_RE = re.compile(r"реестр\w*\s+(?:\w+\s+){0,2}контракт")
_DOCUMENTS_RE = re.compile(
    r"копи\w*\s+(?:\w+\s+){0,3}(?:контракт|договор|акт)|акт\w*\s+(?:выполнен|приемк|приёмк|сдачи)"
)
_EVALUATION_RE = re.compile(r"критери\w*\s+оценк|показател\w*\s+(?:критери|оценк)|оценк\w*\s+заяв")


def normalize_requirement_severity(raw: Any) -> dict[str, str]:
    """Уровни категорий требований профиля; неизвестная категория/уровень — ошибка.

    Raises:
        ValueError: с понятным текстом.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("requirement_severity должен быть объектом")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key not in REQUIREMENT_KEYS:
            raise ValueError(f"Неизвестная категория требований: {key}")
        allowed = EXPERIENCE_SEVERITIES if key == "experience" else SEVERITIES
        if value not in allowed:
            raise ValueError(f"Категория «{key}»: уровень {value!r} — допустимо {allowed}")
        out[str(key)] = str(value)
    return out


def experience_confirmation(item: Mapping[str, Any]) -> str | None:
    """Способ подтверждения опыта: из LLM-поля ``data``, иначе по тексту."""
    data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
    confirmation = (data or {}).get("confirmation")
    if confirmation in CONFIRMATIONS:
        return str(confirmation)
    if (data or {}).get("ref_2571") is True:
        return "platform"
    text = f"{item.get('text') or ''} {item.get('additional') or ''}".lower().replace("ё", "е")
    if _PLATFORM_RE.search(text):
        return "platform"
    if _REGISTRY_RE.search(text):
        return "registry"
    if _DOCUMENTS_RE.search(text):
        return "documents"
    if _EVALUATION_RE.search(text):
        return "evaluation_only"
    return None


def _experience_severity(
    items: list[Mapping[str, Any]], experience_codes: Iterable[str]
) -> tuple[Severity | None, str | None]:
    """BR-03: самый строгий барьер среди НЕ-отрицаемых требований опыта."""
    codes = set(experience_codes)
    worst: Severity | None = None
    detail: str | None = None
    for item in items:
        if item.get("negated"):
            continue
        confirmation = experience_confirmation(item)
        if confirmation == "evaluation_only":
            continue
        if confirmation == "platform":
            if "platform" in codes:
                continue  # опыт через площадку в профиле есть — барьера нет
            return "block", _CONFIRMATION_LABELS["platform"] + ", в профиле нет такого опыта"
        if worst is None:
            worst, detail = "soft", _CONFIRMATION_LABELS[confirmation]
    return worst, detail


def compute_requirements_verdict(
    requirements: Mapping[str, Any],
    requirement_severity: Mapping[str, Any],
    experience_codes: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """Интерпретация ``requirements_json`` под профиль.

    Возвращает ``{category: {severity, negated, count, detail}}`` для категорий,
    где что-то найдено: ``severity`` — ``block``/``soft``/``None`` (не барьер).
    """
    result: dict[str, dict[str, Any]] = {}
    for key in REQUIREMENT_KEYS:
        items = requirements.get(key)
        if not items or not isinstance(items, list):
            continue
        entries = [it for it in items if isinstance(it, Mapping)]
        if not entries:
            continue
        negated = all(bool(it.get("negated")) for it in entries)
        setting = requirement_severity.get(key) or "off"
        severity: Severity | None = None
        detail: str | None = None
        if not negated and setting != "off":
            if key == "experience":
                severity, detail = _experience_severity(entries, experience_codes)
            elif setting in ("block", "soft"):
                severity = setting  # type: ignore[assignment]
        result[key] = {
            "severity": severity,
            "negated": negated,
            "count": len(entries),
            "detail": detail,
        }
    return result


def compute_verdict(
    requirements: Mapping[str, Any],
    requirement_severity: Mapping[str, Any],
    fields: list[dict[str, Any]] | None = None,
    experience_codes: Iterable[str] = (),
) -> dict[str, Any]:
    """Вердикт приемлемости закупки: ``{requirements_verdict, verdict}``.

    ``verdict.accepted=False`` — есть жёсткий барьер (закупку следует
    авто-отклонить); ``soft_reasons`` — мягкие барьеры (снижают P(win)).
    """
    requirements_verdict = compute_requirements_verdict(
        requirements, requirement_severity, experience_codes
    )
    blocking: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    for key, info in requirements_verdict.items():
        if info.get("severity"):
            label = _REQUIREMENT_LABELS.get(key, key)
            if info.get("detail"):
                label = f"{label}: {info['detail']}"
            (blocking if info["severity"] == "block" else soft).append(
                {"source": key, "label": label}
            )
    for field in fields or []:
        if not isinstance(field, dict) or field.get("match") is not False:
            continue
        severity = field.get("severity")
        if severity not in ("block", "soft"):
            continue
        name = str(field.get("field_name") or "").strip() or "Отчётное поле"
        reason = {"source": f"field:{field.get('field_id')}", "label": name}
        (blocking if severity == "block" else soft).append(reason)
    return {
        "requirements_verdict": requirements_verdict,
        "verdict": {
            "accepted": not blocking,
            "blocking_reasons": blocking,
            "soft_reasons": soft,
        },
    }


__all__ = [
    "EXPERIENCE_SEVERITIES",
    "REQUIREMENT_KEYS",
    "SEVERITIES",
    "compute_requirements_verdict",
    "compute_verdict",
    "experience_confirmation",
    "normalize_requirement_severity",
]
