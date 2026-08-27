"""Модели сервисной конфигурации (config_service.yaml).

Здесь — бизнес/тематические параметры, которыми управляют аналитики: список
площадок, критерии поиска, отсечки по датам, условия прекращения обработки.
Инфраструктурные (devops) параметры вынесены в ``config/models/ops.py``
(config_ops.yaml).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _BaseConfig(BaseModel):
    """Базовый класс конфигурации: неизвестные ключи — ошибка (reject опечаток)."""

    model_config = ConfigDict(extra="forbid")


class SiteServiceEntry(_BaseConfig):
    """Одна запись в списке сайтов для периодического обхода."""

    platform_id: str = Field(description="площадка")
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
        description="не обрабатывать закупку, если срок приёма заявок истёк к текущей дате",
    )


class ScoringConfig(_BaseConfig):
    """Правила оценки закупки внешним скорингом (аналитик).

    Параметры влияют на результат скоринга (fit_score/score) и на то, выполняется ли
    LLM-пайплайн. Хранятся в ``config_service.yaml``; значения применяются воркером
    scoring_service (см. ``scoring_service.worker``), источник истины — этот конфиг.
    """

    embedding_filter_threshold: float = Field(
        default=0.66,
        ge=0,
        le=1,
        description=(
            "Порог векторной близости описания закупки и компетенций (0..1): если "
            "близость ниже порога, LLM-пайплайн fit/judge не выполняется, возвращается "
            "score=0 и score_method=sim. Значение <= 0 отключает фильтрацию"
        ),
    )
    giga_embedding_alpha: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description="Вес векторной близости в итоговом score (0 — только диагностика)",
    )
    giga_enabled: bool = Field(
        default=False,
        description="Выполнять ли ветку векторной близости (эмбеддинги компетенций и описания)",
    )
    num_refine_rounds: int = Field(
        default=1,
        ge=0,
        description="пересчётов Fit после критики судьи при вердикте «reject» (0 — без повторов)",
    )
    max_fit_score: float = Field(
        default=10.0, gt=0, description="Максимальное значение Fit (шкала 0..max_fit_score)"
    )
    min_fit_score: float = Field(default=0.0, ge=0, description="Минимальное значение Fit")
    score_round_digits: int = Field(
        default=2, ge=0, le=4, description="Округление итогового score до N знаков"
    )
    tz_download_timeout: float = Field(
        default=30.0, gt=0, description="лимит времени на скачивание файла ТЗ для уточнения (сек)"
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
            "поддерживает, иначе по дате публикации)"
        ),
    )
    search_criteria: SearchCriteria = Field(
        default_factory=SearchCriteria, description="критерии поиска (тематика фильтра)"
    )
    profiles_loop_order: Literal["platform_then_profile", "profile_then_platform"] = Field(
        default="platform_then_profile",
        description=(
            "порядок циклов по профилям и площадкам: 'platform_then_profile' — снаружи "
            "площадки, внутри профили (кэшируемость одинаковых запросов, дефолт); "
            "'profile_then_platform' — снаружи профиль (изоляция/параллелизм по профилю). "
            "Влияет только на порядок, не на состав обрабатываемых профилей."
        ),
    )
    deduplicate_requests: bool = Field(
        default=True,
        description=(
            "объединять идентичные поисковые обходы одна и та же площадка + одинаковые "
            "критерии (коды ОКПД2/НМЦК) разных профилей в один проход с веерной "
            "фильтрацией по каждому профилю"
        ),
    )
    scoring: ScoringConfig = Field(
        default_factory=ScoringConfig,
        description="правила оценки закупки (вкладка «Параметры мониторинга»)",
    )
