"""Модели сервисной конфигурации (config_service.yaml).

Здесь — бизнес/тематические параметры, которыми управляют аналитики: список
площадок, критерии поиска, отсечки по датам, условия прекращения обработки.
Инфраструктурные (devops) параметры вынесены в ``config/models/ops.py``
(config_ops.yaml).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _BaseConfig(BaseModel):
    """Базовый класс конфигурации: неизвестные ключи — ошибка (reject опечаток)."""

    model_config = ConfigDict(extra="forbid")


class SiteServiceEntry(_BaseConfig):
    """Одна запись в списке сайтов для периодического обхода."""

    platform_id: str = Field(description="ключ площадки в configs/dom/<platform_id>.yaml")
    enabled: bool = Field(default=True)


class SearchCriteria(_BaseConfig):
    """Бизнес-критерии поиска — задаются в config_service.yaml в ОБОБЩЁННЫХ терминах.

    Эти поля платформонезависимы (ОКПД2, НМЦК — это понятия закупочной тематики).
    Конкретная привязка каждого критерия к параметрам URL-запроса или
    DOM-селекторам площадки выполняется в configs/dom/<platform_id>.yaml
    (``search.criteria_map``), поэтому здесь нет ни селекторов, ни имён query-параметров.
    Ключевые слова в серверном запросе не участвуют (R9): они хранятся в таблице
    ``keywords`` профиля и применяются клиентской пост-фильтрацией до записи в БД.
    """

    okpd_codes: list[str] = Field(
        default_factory=list,
        description=(
            "коды ОКПД2 (тематика). Резолвятся в пути узлов дерева площадки "
            "через маппинг (search.okpd_tree_file); выбор предка включает потомков"
        ),
    )
    nmck_min: float | None = Field(
        default=None,
        ge=0,
        description="минимальная НМЦК (отсекает мелкие лоты, не окупающие консалтинг)",
    )
    nmck_max: float | None = Field(
        default=None,
        ge=0,
        description="максимальная НМЦК",
    )
    active_only: bool = Field(
        default=False,
        description=(
            "выбор закупок по состоянию: false — все, true — только активные "
            "(применяется на площадках, где это поддерживается, через stateIdIn)"
        ),
    )
    no_code_search: bool = Field(
        default=False,
        description=(
            "обход «без кода» (R9): отдельный проход по реестру площадки без фильтра "
            "ОКПД2, чтобы не терять закупки, подходящие по словам профиля, но вне "
            "заданных кодов; полученный список фильтруется словами клиентски до записи. "
            "ВЫКЛЮЧЕН по умолчанию; включается только этим флагом в config_service.yaml"
        ),
    )
    deadline_not_expired: bool = Field(
        default=True,
        description=(
            "не обрабатывать закупку, если срок приёма заявок (переменная 'deadline' "
            "из configs/dom/<platform_id>.yaml) истёк к текущей дате"
        ),
    )


class ServiceConfig(_BaseConfig):
    """Сервисная конфигурация (аналитика): список сайтов, критерии, пороги, флаги."""

    sites: list[SiteServiceEntry] = Field(default_factory=list)
    default_cutoff_days: int = Field(
        default=7, ge=0, description="порог 'дата последней обработанной записи' в днях"
    )
    sort_by_date_only: bool = Field(
        default=False,
        description=(
            "сортировать все площадки по дате (по дате обновления, если площадка её "
            "поддерживает, иначе по дате публикации); стоп-порог по дате применяется "
            "всегда. false — сортировка площадок, допускающих релевантность, "
            "управляется их индивидуальным параметром sort.by_relevance"
        ),
    )
    search_criteria: SearchCriteria = Field(
        default_factory=SearchCriteria, description="критерии поиска (тематика фильтра)"
    )
