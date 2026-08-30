"""Сбор стоимости и метрик LLM-вызовов скоринга (fit/judge/refine) в USD.

LangChain-колбэк ``CostCallback`` суммирует стоимость всех LLM-вызовов одного
задания в ``total_usd`` по app-side тарифам из ``scoring_common.costing``.
Вход трактуется как cache-miss, если LLM не вернула разбивку кэш-хит/мисс
(LangChain не всегда отдаёт её).

Помимо стоимости, колбэк накапливает метрики стадии для карточки закупки
(вкладка «Метрики»): токены по бакетам, посекционную стоимость, число вызовов
и суммарную латенси моделей (``metrics``).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from scoring_common.costing import llm_cost_details, llm_cost_usd


class CostCallback(BaseCallbackHandler):
    """Суммирует стоимость и метрики LLM-вызовов скоринга (fit/judge/refine)."""

    def __init__(self, model: str) -> None:
        self._model = model
        self._total_usd = 0.0
        self._usage: dict[str, int] = {}
        self._cost_details: dict[str, float] = {}
        self._calls = 0
        self._latency_ms = 0.0
        self._start = 0.0

    @property
    def total_usd(self) -> float:
        """Итоговая стоимость (USD), округлённая до 8 знаков."""
        return round(self._total_usd, 8)

    def metrics(self) -> dict[str, Any]:
        """Сырые агрегаты LLM-стадии: стоимость/токены/латенси/число вызовов.

        Общее ``duration_ms`` стадии вычисляет ``Scorer`` (обёртка вокруг всего
        пайплайна), поэтому здесь возвращаются только части без ``duration_ms``.
        """
        return {
            "usd": round(self._total_usd, 8),
            "usage": dict(self._usage),
            "cost_details": dict(self._cost_details),
            "models": [self._model],
            "calls": self._calls,
            "latency_ms": round(self._latency_ms, 3),
        }

    def reset(self) -> None:
        """Обнулить накопленные стоимость и метрики (перед следующим заданием)."""
        self._total_usd = 0.0
        self._usage = {}
        self._cost_details = {}
        self._calls = 0
        self._latency_ms = 0.0
        self._start = 0.0

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        """Запомнить время начала вызова для расчёта латенси моделей."""
        self._start = time.perf_counter()

    def on_llm_end(self, response: LLMResult, *args: Any, **kwargs: Any) -> None:
        """Добавить стоимость и метрики завершившегося LLM-вызова по его usage."""
        if self._start:
            self._latency_ms += (time.perf_counter() - self._start) * 1000.0
            self._start = 0.0
        usage = (response.llm_output or {}).get("token_usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        if not (prompt or completion):
            return
        cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        cache_miss = int(usage.get("prompt_cache_miss_tokens", max(0, prompt - cache_hit)))
        now = datetime.now(UTC)
        self._total_usd += llm_cost_usd(
            self._model,
            cache_miss,
            completion,
            now,
            input_cache_hit=cache_hit,
        )
        details = llm_cost_details(
            self._model,
            cache_miss,
            completion,
            now,
            input_cache_hit=cache_hit,
        )
        self._calls += 1
        for key, value in details.items():
            self._cost_details[key] = round((self._cost_details.get(key) or 0.0) + value, 8)
        self._usage["input"] = int(self._usage.get("input") or 0) + cache_miss
        self._usage["output"] = int(self._usage.get("output") or 0) + completion
        if cache_hit:
            self._usage["input_cached_tokens"] = (
                int(self._usage.get("input_cached_tokens") or 0) + cache_hit
            )


__all__ = ["CostCallback"]
