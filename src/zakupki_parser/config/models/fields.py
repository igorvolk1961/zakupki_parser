"""Канонический контракт полей закупки и оценка покрытия конфигурации.

``Procurement`` (storage/db/procurement.py) — уже каноническая запись: поля потоком
идут из ``list_config.variables``/``detail.variables`` и дозаполняются на этапе
деталей (BR-08). Этот модуль добавляет «контракт» — какие поля нужно собрать
тендерологу (с тирами важности) и как оценить, покрывает ли их конфиг площадки
(статическое покрытие по задекларированным источникам).

Статическое покрытие не требует БД: вычисляется из ``PlatformDom`` и пригодно для
оценки «черновика» конфига (что нужно ИИ-агенту). Динамическое (реальное
заполнение по сохранённым записям) живёт в репозитории (см.
``ProcurementRepository.field_coverage_runtime``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class FieldTier(StrEnum):
    """Тир важности поля для тендеролога."""

    MANDATORY = "mandatory"
    IMPORTANT = "important"
    OPTIONAL = "optional"


class FieldSpec(BaseModel):
    """Описание одного поля контракта.

    ``column`` — атрибут ``Procurement`` для runtime-метрики (``None`` для
    синтетических полей вроде url, заполняемого из ``detail_link``).
    ``config_keys`` — имена переменных конфига/ключи данных, которыми поле
    заполняется (для статики: «задекларировано», если хоть один есть в списке
    переменных площадки).
    """

    key: str
    label: str
    tier: FieldTier
    column: str | None = Field(default=None, description="атрибут Procurement для runtime")
    config_keys: list[str] = Field(default_factory=list, description="имена переменных/ключи")

    def declared(self, keys: set[str]) -> bool:
        return any(k in keys for k in self.config_keys)


class FieldStatus(BaseModel):
    """Результат статического покрытия одного поля конфигурации площадки."""

    key: str
    label: str
    tier: FieldTier
    status: Literal["declared", "missing"]
    sources: list[str] = Field(default_factory=list, description="источники (найденные ключи)")


# Контракт полей: ключ, человекочитаемое имя, тир, колонка runtime, ключи конфига.
# Тира можно скорректировать при ревью; alarm строится только по MANDATORY.
_REQUIRED_FIELDS: tuple[FieldSpec, ...] = (
    # --- обязательные (без них запись бесполезна) ------------------------
    FieldSpec(
        key="number",
        label="Номер закупки",
        tier=FieldTier.MANDATORY,
        column="number",
        config_keys=["number"],
    ),
    FieldSpec(
        key="subject",
        label="Предмет",
        tier=FieldTier.MANDATORY,
        column="subject",
        config_keys=["subject"],
    ),
    FieldSpec(
        key="customer",
        label="Заказчик",
        tier=FieldTier.MANDATORY,
        column="customer_id",
        config_keys=["customer"],
    ),
    # url — синтетический: заполняется из detail_link или переменной url.
    FieldSpec(
        key="url",
        label="Страница закупки",
        tier=FieldTier.MANDATORY,
        column="url",
        config_keys=["url"],
    ),
    # --- важные (сильно желательны; могут законно отсутствовать) ----------
    FieldSpec(
        key="inn",
        label="ИНН заказчика",
        tier=FieldTier.IMPORTANT,
        column="customer_id",
        config_keys=["inn"],
    ),
    FieldSpec(
        key="nmck", label="НМЦК", tier=FieldTier.IMPORTANT, column="nmck", config_keys=["nmck"]
    ),
    FieldSpec(
        key="deadline",
        label="Срок подачи",
        tier=FieldTier.IMPORTANT,
        column="deadline",
        config_keys=["deadline"],
    ),
    FieldSpec(
        key="publication_date",
        label="Дата публикации",
        tier=FieldTier.IMPORTANT,
        column="publication_date",
        config_keys=["publication_date"],
    ),
    FieldSpec(
        key="law", label="Закон", tier=FieldTier.IMPORTANT, column="law", config_keys=["law"]
    ),
    FieldSpec(
        key="purchase_type",
        label="Тип процедуры",
        tier=FieldTier.IMPORTANT,
        column="procedure_type_id",
        config_keys=["purchase_type"],
    ),
    FieldSpec(
        key="okpd2",
        label="Коды ОКПД2",
        tier=FieldTier.IMPORTANT,
        column="okpd2_codes",
        config_keys=["okpd2", "okpd2_code", "okpd2_codes", "okpd2_name"],
    ),
    FieldSpec(
        key="files",
        label="Приложенные файлы",
        tier=FieldTier.IMPORTANT,
        column="files_json",
        config_keys=["files"],
    ),
    # --- опциональные ----------------------------------------------------
    FieldSpec(
        key="kpgz",
        label="Коды КПГЗ",
        tier=FieldTier.OPTIONAL,
        column="kpgz_codes",
        config_keys=["kpgz", "kpgz_code", "kpgz_codes"],
    ),
    FieldSpec(
        key="security_amount",
        label="Обеспечение",
        tier=FieldTier.OPTIONAL,
        column="security_amount",
        config_keys=["security_amount"],
    ),
    FieldSpec(
        key="advance",
        label="Аванс",
        tier=FieldTier.OPTIONAL,
        column="advance",
        config_keys=["advance"],
    ),
    FieldSpec(
        key="execution_term",
        label="Срок исполнения",
        tier=FieldTier.OPTIONAL,
        column="execution_term",
        config_keys=["execution_term"],
    ),
    FieldSpec(
        key="region",
        label="Регион",
        tier=FieldTier.OPTIONAL,
        column="region",
        config_keys=["region"],
    ),
    FieldSpec(
        key="status",
        label="Статус/активность",
        tier=FieldTier.OPTIONAL,
        column=None,
        config_keys=["status"],
    ),
)


def required_fields() -> tuple[FieldSpec, ...]:
    """Копия контракта (защита от случайной мутации внешним кодом)."""
    return _REQUIRED_FIELDS


def field_by_key(key: str) -> FieldSpec | None:
    return next((f for f in _REQUIRED_FIELDS if f.key == key), None)


def _declared_variable_keys(platform: Any) -> set[str]:
    """Имена переменных списка и деталей — «словарь» полей, которые площадка заполняет."""
    keys: set[str] = set()
    for var in platform.list_config.variables:
        keys.add(var.name)
    for var in platform.detail.variables:
        keys.add(var.name)
    return keys


def _inn_declared(platform: Any) -> tuple[bool, list[str]]:
    """ИНН считается покрытым, если есть переменная inn или способ извлечения из
    организации (ADR-4): customer_link_selector/inn_page_selector/inn_from_link_regex
    /inn_from_org_page указывают на возможность получить ИНН."""
    keys = _declared_variable_keys(platform)
    if "inn" in keys:
        return True, ["inn"]
    org = platform.organization
    if org is not None and (
        org.customer_link_selector
        or org.inn_page_selector
        or org.inn_from_link_regex
        or org.inn_from_org_page
    ):
        return True, ["organization"]
    return False, []


def static_field_coverage(platform: Any) -> list[FieldStatus]:
    """Статическое покрытие полей конфигурацией площадки (без обращения к БД).

    Для каждого поля контракта вычисляется, есть ли в конфиге источник:
    переменная списка/деталей (по ``config_keys``), либо специальные случаи
    (``url`` — из ``detail_link``; ``files`` — из ``detail.files``; ``inn`` —
    из способа организации).
    """
    keys = _declared_variable_keys(platform)
    results: list[FieldStatus] = []

    inn_ok, inn_sources = _inn_declared(platform)

    for spec in _REQUIRED_FIELDS:
        if spec.key == "url":
            ok = bool(platform.list_config.detail_link) or "url" in keys
            sources = (
                ["detail_link"]
                if platform.list_config.detail_link
                else (["url"] if "url" in keys else [])
            )
        elif spec.key == "files":
            ok = bool(platform.detail.files) or spec.declared(keys)
            sources = (
                ["detail.files"]
                if platform.detail.files
                else ([k for k in spec.config_keys if k in keys] if spec.declared(keys) else [])
            )
        elif spec.key == "inn":
            ok = inn_ok
            sources = inn_sources
        else:
            ok = spec.declared(keys)
            sources = [k for k in spec.config_keys if k in keys] if ok else []
        results.append(
            FieldStatus(
                key=spec.key,
                label=spec.label,
                tier=spec.tier,
                status="declared" if ok else "missing",
                sources=sources,
            )
        )
    return results


def coverage_score(results: list[FieldStatus]) -> float:
    """Доля задекларированных полей среди MANDATORY+IMPORTANT (0..1)."""
    relevant = [r for r in results if r.tier != FieldTier.OPTIONAL]
    if not relevant:
        return 1.0
    declared = sum(1 for r in relevant if r.status == "declared")
    return declared / len(relevant)


def missing_mandatory(results: list[FieldStatus]) -> list[str]:
    """Ключи незакрытых обязательных (MANDATORY) полей — для предупреждений в CLI."""
    return [r.key for r in results if r.tier == FieldTier.MANDATORY and r.status == "missing"]
