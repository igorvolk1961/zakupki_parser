"""Модель конфигурации скоринга (config_score.yaml)."""

from __future__ import annotations

from pydantic import BaseModel, Field

SCORE_METHOD_FIT = "fit"
SCORE_METHOD_PWIN = "pwin"
SCORE_METHOD_MARGIN = "margin"
SCORE_METHOD_SIM = "sim"

# Стадии внешнего каскада скоринга (Fit -> P(win) -> Margin) плюс терминальная
# предварительная фильтрация по векторной близости (sim): значение
# score_method per-profile оценки (procurement_evaluations), обработанной внешним
# сервисом скоринга, — одна из этих констант. sim — не стадия каскада (переходы
# Fit -> P(win) -> Margin для неё не запускаются), но результат внешней обработки:
# учитывается в фильтре релевантности и при удалении нерелевантных записей (ADR-8).
SCORE_METHOD_STAGES = (
    SCORE_METHOD_FIT,
    SCORE_METHOD_PWIN,
    SCORE_METHOD_MARGIN,
    SCORE_METHOD_SIM,
)


class ScoreConfig(BaseModel):
    """Конфигурация внешнего скоринга закупок (ADR-7).

    Дефолтный (внутренний) скоринг УДАЛЁН: закупка сохраняется без оценки, результат
    внешнего каскада (Fit/P(win)/Margin) приходит асинхронно через конвейер
    transport + scoring_service + Redis и пишется в ``procurement_evaluations``
    (per-profile) через ``POST /score``.
    """

    pwin_enabled: bool = Field(
        default=False,
        description=(
            "включена ли стадия P(win): парсер ставит задачи в очередь pwin:jobs "
            "только при True. Держать False, пока сервис pwin_service не развёрнут "
            "(защита от зависания задач при ролл-ауте)"
        ),
    )
    margin_enabled: bool = Field(
        default=False,
        description=(
            "включена ли стадия Margin: парсер ставит задачи в очередь margin:jobs "
            "только при True. Держать False, пока сервис margin_service не развёрнут"
        ),
    )
    scoring_transport_url: str | None = Field(
        default=None,
        description="адрес scoring_transport для автопуша задания на внешний скоринг (ADR-7)",
    )
    # Bearer-токен авторизации при автопуше в scoring_transport. Секрет: хранится
    # только в env (ZAKUPKI_SCORING_TRANSPORT_TOKEN), в форму не выводится.
    scoring_transport_token: str | None = Field(
        default=None,
        description="Bearer-токен авторизации для автопуша задания в scoring_transport",
    )
    # TTL «постановки в очередь»: неотскорингованная закупка, чья метка
    # scoring_queued_at старше этого порога, снова ставится в очередь recovery
    # (задание могло быть потеряно: воркер упал/снял задачу, очередь очищена).
    # 0 — самовосстановление выключено.
    recovery_ttl_seconds: float = Field(
        default=3600.0,
        ge=0,
        description=(
            "TTL (сек) для повторной постановки в очередь неотскорингованных "
            "закупок: scoring_queued_at старше порога -> закупка снова ставится "
            "в очередь (задание потеряно). 0 — выключено"
        ),
    )
