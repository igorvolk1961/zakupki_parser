"""Сбор стоимости LLM-вызовов скоринга (fit/judge/refine) в USD.

LangChain-колбэк ``CostCallback`` суммирует стоимость всех LLM-вызовов одного
задания в ``total_usd`` по app-side тарифам из ``scoring_common.costing``.
Вход трактуется как cache-miss: LangChain не отдаёт разбивку кэш-хит/мисс.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from scoring_common.costing import llm_cost_usd


class CostCallback(BaseCallbackHandler):
    """Суммирует стоимость LLM-вызовов скоринга (fit/judge/refine) в USD."""

    def __init__(self, model: str) -> None:
        self._model = model
        self._total_usd = 0.0

    @property
    def total_usd(self) -> float:
        """Итоговая стоимость (USD), округлённая до 8 знаков."""
        return round(self._total_usd, 8)

    def reset(self) -> None:
        """Обнулить накопленную стоимость (перед следующим заданием)."""
        self._total_usd = 0.0

    def on_llm_end(self, response: LLMResult, *args: Any, **kwargs: Any) -> None:
        """Добавить стоимость завершившегося LLM-вызова по его token usage."""
        usage = (response.llm_output or {}).get("token_usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        if prompt or completion:
            self._total_usd += llm_cost_usd(self._model, prompt, completion, datetime.now(UTC))


__all__ = ["CostCallback"]
