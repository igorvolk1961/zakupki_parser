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


class StopConditions(_BaseConfig):
    """Набор флагов-условий прекращения обработки очередной закупки.

    Каждый флаг — это условие, при котором закупка пропускается (не сохраняется
    и не уведомляется). Набор расширяется добавлением новых флагов.
    """

    deadline_not_expired: bool = Field(
        default=True,
        description=(
            "не обрабатывать закупку, если срок приёма заявок (переменная 'deadline' "
            "из configs/dom/<platform_id>.yaml) истёк к текущей дате"
        ),
    )
    min_deadline_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "если задано — не обрабатывать закупку, до срока подачи которой осталось "
            "меньше указанного числа календарных дней (нужно время на подготовку заявки); "
            "применяется ТОЛЬКО если deadline_not_expired=true"
        ),
    )


class SearchCriteria(_BaseConfig):
    """Бизнес-критерии поиска — задаются в config_service.yaml в ОБОБЩЁННЫХ терминах.

    Эти поля платформонезависимы (ОКПД2, НМЦК, ключевые слова, регионы — это
    понятия закупочной тематики). Конкретная привязка каждого критерия к параметрам
    URL-запроса или DOM-селекторам площадки выполняется в configs/dom/<platform_id>.yaml
    (``search.criteria_map``), поэтому здесь нет ни селекторов, ни имён query-параметров.
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
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "ключевые слова для фильтрации по наименованию/описанию закупки; "
            "закупка проходит, если любое из слов встречается (регистронезависимо); "
            "пустой список — фильтр не применяется"
        ),
    )
    active_only: bool = Field(
        default=False,
        description=(
            "выбор закупок по состоянию: false — все, true — только активные "
            "(применяется на площадках, где это поддерживается, через stateIdIn)"
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
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
