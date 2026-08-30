"""Оркестратор пайплайна скоринга.

Поток:
1. извлечь описание закупки из карточки (``pipeline.description``);
2. (опц.) ветка векторной близости ДО LLM: если близость ниже порога
   ``embedding_filter_threshold`` — предварительная фильтрация (LLM не
   запускается, возвращается fit_score=0 и score_method=sim);
3. fit-цепочка: reasoning + fit_score (0..10);
4. judge-цепочка: critics / verdict / final_fit_score;
5. если verdict == reject — до ``num_refine_rounds`` повторный fit с учётом critics,
   затем повторный judge;
6. Score = final_fit_score × P(win) × Margin.

Реализация разбита на подпакеты: ``embedding`` (ветка векторной близости),
``pipeline`` (fit/judge/refine и сборка результата), ``types`` (внутренние типы).
Здесь — класс ``Scorer`` и ``build_scorer`` (реэкспорт для совместимости с
прежним модулем ``scoring_service/scoring.py``).
"""

from __future__ import annotations

import logging
import time
from typing import Any, cast

from langchain_core.runnables import RunnableConfig, RunnableLambda

from scoring_common.costing import merge_usage, stage_metrics
from scoring_service.llm_factory import build_llm, callbacks_for, langfuse_handler
from scoring_service.pipeline.cost import CostCallback
from scoring_service.pipeline.description import extract_description
from scoring_service.pipeline.fit_chain import FitChain
from scoring_service.pipeline.judge_chain import JudgeChain
from scoring_service.pipeline.tz_reviewer import TzReviewer
from scoring_service.profile import ProfileTexts
from scoring_service.schemas import ScoringOutput
from scoring_service.scoring.embedding import EmbeddingMixin
from scoring_service.scoring.pipeline import PipelineMixin
from scoring_service.settings import Settings

logger = logging.getLogger(__name__)


class Scorer(EmbeddingMixin, PipelineMixin):
    """Оркестратор: карточка + компетенции → полный результат скоринга."""

    def __init__(
        self,
        fit_chain: FitChain,
        judge_chain: JudgeChain,
        settings: Settings,
        callbacks: list[Any] | None = None,
        tz_reviewer: TzReviewer | None = None,
        embedder: Any | None = None,
        embedding_skip_reason: str | None = None,
        langfuse_handler: Any | None = None,
        cost_callback: CostCallback | None = None,
    ) -> None:
        self._fit = fit_chain
        self._judge = judge_chain
        self._settings = settings
        # Callbacks для корневого run скоринга: один трейс на задание, в который
        # вложены fit/judge/refine как дочерние спаны (вместо отдельных трейсов).
        self._callbacks = callbacks
        # Сбор стоимости LLM-вызовов скоринга (fit/judge/refine) в USD. None, если
        # сбор не подключён — тогда result.cost_usd остаётся None.
        self._cost_callback = cost_callback
        # LangFuse LangChain-callback: после каждого score() читаем last_trace_id,
        # чтобы построить глубокую ссылку на трейс закупки. None, если LangFuse
        # не настроен — ссылки нет, кнопка «Трейс» не отображается.
        self._langfuse_handler = langfuse_handler
        # Параллельная ветка векторной близости (Giga Embedder). None, если ветка
        # выключена либо не настроен ключ доступа.
        self._embedder = embedder
        # Причина пропуска ветки (например, отсутствие ключа доступа) — пишется в
        # метаданные LangFuse-трейса, чтобы не было тихого «пропуска без следа».
        self._embedding_skip_reason = embedding_skip_reason
        # Кэш эмбеддинга компетенций: компетенции одинаковы для всех закупок
        # прогона, поэтому вектор считаем один раз и переиспользуем (ключ — текст
        # компетенций). Заполняется веткой векторной близости.
        self._competencies_embedding_cache: dict[str, list[float]] = {}
        # Уточнение по ТЗ работает только при tz_review_enabled=true (флаг имеет
        # приоритет над явно переданным reviewer — для тестов).
        self._tz_reviewer = (
            tz_reviewer
            if (settings.tz_review_enabled and tz_reviewer)
            else (TzReviewer(settings, callbacks) if settings.tz_review_enabled else None)
        )
        # Метаданные гиперпараметров/промптов, общие для всех вызовов скоринга.
        # Пишутся в каждый трейс, чтобы группировать/сравнивать запуски по конфигурации.
        self._base_metadata: dict[str, Any] = {
            "llm_model": settings.llm_model,
            "llm_temperature": settings.llm_temperature,
            "llm_structured_method": settings.llm_structured_method,
            "num_refine_rounds": settings.num_refine_rounds,
            "normalize_fit_for_score": settings.normalize_fit_for_score,
            "giga_enabled": settings.giga_enabled,
            "giga_configured": settings.giga_configured,
            "giga_model": settings.giga_embeddings_model,
            "giga_embedding_alpha": settings.giga_embedding_alpha,
            "embedding_filter_threshold": settings.embedding_filter_threshold,
        }
        if settings.giga_enabled and not settings.giga_configured:
            self._base_metadata["embedding_skipped"] = "missing giga credentials"

    def _trace_metadata(
        self,
        procurement_id: int | None,
        run_id: str | None,
        extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Метаданные трассы: гиперпараметры + идентификаторы задания/запуска."""
        meta = dict(self._base_metadata)
        if procurement_id is not None:
            meta["procurement_id"] = procurement_id
        if run_id is not None:
            meta["run_id"] = run_id
        if extra:
            meta.update(extra)
        return meta

    def _langfuse_trace_url(self, trace_id: str | None) -> str | None:
        """Глубокая ссылка на трейс LangFuse по trace_id (или None).

        Ссылку строит глобальный клиент LangFuse (``get_trace_url``): возвращает
        ``None``, если проект не резолвится (LangFuse недоступен/не настроен) —
        тогда кнопка «Трейс» на карточке скрывается. Ошибки не роняют скоринг.
        """
        if not trace_id:
            return None
        try:
            from langfuse import get_client

            return get_client().get_trace_url(trace_id=trace_id)
        except Exception:  # noqa: BLE001 - best-effort, не роняет скоринг
            return None

    def score(
        self,
        record: dict[str, Any],
        competencies: str | ProfileTexts,
        procurement_id: int | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        run_name: str = "scoring_job",
    ) -> ScoringOutput:
        """Полный скоринг закупки по карточке и профилю поставщика.

        ``competencies`` — либо отрендеренный текст профиля, либо структурированная
        пара ``ProfileTexts`` (llm/embedding). Если передан строкой, для ветки
        векторной близости используется тот же текст (без разделения на исключения).

        ``run_id`` — идентификатор запуска (батча): все задания одного запуска
        объединяются в одну LangFuse-сессию (``session_id``). Если ``run_id`` не задан,
        сессией служит ``procurement_id`` (для разовых/синхронных вызовов).

        ``run_name`` — имя корневого run (трейса) в LangFuse; по умолчанию
        ``scoring_job``. Позволяет различать трейсы (например, номер повтора и
        фрагмент описания закупки).

        Весь скоринг одного задания выполняется внутри единого корневого run,
        поэтому fit/judge/refine попадают в ОДИН трейс как дочерние спаны.
        """
        texts = (
            competencies
            if isinstance(competencies, ProfileTexts)
            else ProfileTexts(llm=competencies, embedding=competencies)
        )
        session_id = run_id or (str(procurement_id) if procurement_id is not None else None)
        trace_meta = self._trace_metadata(procurement_id, run_id, metadata)
        root_config = cast(
            RunnableConfig,
            {
                "callbacks": self._callbacks or None,
                "run_name": run_name,
                "metadata": {
                    **trace_meta,
                    **({"langfuse_session_id": session_id} if session_id is not None else {}),
                },
            },
        )
        # Обнуляем trace_id перед прогоном: last_trace_id у LangFuse-обработчика
        # глобальный (перезаписывается каждым run), чтобы при сбое текущей закупки
        # не прицепить ссылку на трейс предыдущей.
        if self._langfuse_handler is not None:
            self._langfuse_handler.last_trace_id = None
        # Обнуляем стоимость перед заданием: один Scorer переиспользуется на многих
        # закупках, поэтому счётчик накапливается и требует сброса на каждый score().
        if self._cost_callback is not None:
            self._cost_callback.reset()
        # Обнуляем метрики ветки векторной близости: эмбеддер переиспользуется, а
        # метрики должны отражать только обработку текущей закупки.
        getattr(self._embedder, "reset_metrics", lambda: None)()
        runner = RunnableLambda(self._score_impl, name=run_name)
        stage_start = time.perf_counter()
        result = runner.invoke(
            (record, texts, procurement_id, session_id, trace_meta, root_config),
            config=root_config,
        )
        duration_ms = (time.perf_counter() - stage_start) * 1000.0
        trace_url = self._langfuse_trace_url(getattr(self._langfuse_handler, "last_trace_id", None))
        if trace_url:
            result.langfuse_trace_url = trace_url
        if self._cost_callback is not None:
            result.cost_usd = self._cost_callback.total_usd
        result.cost_metrics = self._collect_stage_metrics(duration_ms)
        return result

    def _collect_stage_metrics(self, duration_ms: float) -> dict[str, Any] | None:
        """Собрать метрики стадии (LLM + эмбеддинги) для карточки закупки.

        LLM-агрегаты предоставляет ``CostCallback``, эмбеддинги — ``GigaEmbedder``
        (если ветка векторной близости запускалась). ``duration_ms`` — общее время
        всего пайплайна скоринга; ``latency_ms`` — суммарное время моделей/эмбеддингов.
        """
        parts: list[dict[str, Any]] = []
        if self._cost_callback is not None:
            parts.append(self._cost_callback.metrics())
        emb_metrics: dict[str, Any] = getattr(self._embedder, "metrics", lambda: {})()
        if emb_metrics:
            parts.append(emb_metrics)
        if not parts:
            return None
        total_usd = 0.0
        usage: dict[str, int] = {}
        cost_details: dict[str, float] = {}
        models: list[str] = []
        calls = 0
        latency_ms = 0.0
        for part in parts:
            total_usd += float(part.get("usd") or 0.0)
            merge_usage(usage, part.get("usage") or {})
            for key, value in (part.get("cost_details") or {}).items():
                cost_details[key] = round((cost_details.get(key) or 0.0) + float(value), 8)
            models.extend(part.get("models") or [])
            calls += int(part.get("calls") or 0)
            latency_ms += float(part.get("latency_ms") or 0.0)
        return stage_metrics(
            usd=total_usd,
            usage=usage,
            cost_details=cost_details,
            models=models,
            calls=calls,
            latency_ms=latency_ms,
            duration_ms=duration_ms,
        )

    def _score_impl(
        self,
        inputs: tuple[
            dict[str, Any],
            ProfileTexts,
            int | None,
            str | None,
            dict[str, Any],
            RunnableConfig,
        ],
        config: RunnableConfig | None = None,
    ) -> ScoringOutput:
        """Внутренняя реализация скоринга; выполняется внутри корневого run."""
        record, texts, procurement_id, session_id, trace_meta, root_config = inputs
        parent_config = config or root_config
        description = extract_description(record)

        embed_sim: float | None = None
        if self._embedder is not None:
            # Ветка векторной близости выполняется ДО LLM-пайплайна: результат
            # используется для предварительной фильтрации закупок (если близость
            # ниже порога embedding_filter_threshold — LLM не запускается).
            # Для вектора берётся ТОЛЬКО позитивный текст профиля (без исключений
            # и политики), чтобы «чего компания НЕ делает» не создавало шум.
            embed_sim = self._run_embedding_branch(texts.embedding, description, parent_config)
            if (
                embed_sim is not None
                and self._settings.embedding_filter_threshold > 0
                and embed_sim < self._settings.embedding_filter_threshold
            ):
                logger.info(
                    "Procurement %s: embedding similarity %.4f ниже порога %.4f — "
                    "LLM-пайплайн пропущен",
                    procurement_id,
                    embed_sim,
                    self._settings.embedding_filter_threshold,
                )
                return self._filtered_output(description, embed_sim, procurement_id)

        result = self._run_pipeline(
            record, texts.llm, description, session_id, trace_meta, parent_config
        )
        return self._build_output(result, embed_sim, procurement_id)


def build_scorer(settings: Settings) -> Scorer:
    """Построить ``Scorer`` с полным LLM-пайплайном (fit/judge/refine)."""
    llm = build_llm(settings)
    lf_handler = langfuse_handler(settings)
    callbacks = callbacks_for(lf_handler)
    # Параллельная ветка векторной близости. Строится только при включённом флаге
    # и заданном ключе доступа; иначе — None с причиной пропуска (без падения).
    embedder: Any | None = None
    embedding_skip_reason: str | None = None
    if settings.giga_enabled:
        if settings.giga_configured:
            from scoring_service.modules.giga_embedder import GigaEmbedder, GigaTokenProvider

            token_provider = GigaTokenProvider(
                auth_url=settings.giga_auth_url,
                client_id=settings.giga_client_id,
                client_secret=settings.giga_client_secret,
                scope=settings.giga_auth_scope,
                min_ttl_seconds=settings.giga_min_token_ttl_seconds,
                verify_ssl=settings.giga_verify_ssl,
            )
            embedder = GigaEmbedder(
                base_url=settings.giga_base_url,
                model=settings.giga_embeddings_model,
                token_provider=token_provider,
                verify_ssl=settings.giga_verify_ssl,
            )
        else:
            embedding_skip_reason = "missing giga credentials"
    cost_cb = CostCallback(settings.llm_model)
    augmented = (callbacks or []) + [cost_cb]
    return Scorer(
        FitChain(llm, augmented, method=settings.llm_structured_method),
        JudgeChain(llm, augmented, method=settings.llm_structured_method),
        settings,
        callbacks=augmented,
        embedder=embedder,
        embedding_skip_reason=embedding_skip_reason,
        langfuse_handler=lf_handler,
        cost_callback=cost_cb,
    )


__all__ = ["Scorer", "build_scorer"]
