"""Fit-цепочка: оценка Fit (0..10) по описанию закупки и компетенциям.

Langchain: сообщения из ``prompts.build_fit_messages`` (few-shot + negative-example)
→ ``with_structured_output(FitResult)`` — строгий JSON-выход по pydantic-схеме.
"""

from __future__ import annotations

from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

from scoring_service.pipeline.prompts import build_fit_messages
from scoring_service.schemas import FitResult


class FitChain:
    """Обёртка над fit-цепочкой LLM."""

    def __init__(self, llm: BaseChatModel, callbacks: list[Any] | None = None) -> None:
        self._structured = llm.with_structured_output(FitResult)
        self._callbacks = callbacks

    def _config(self, procurement_id: str | None = None) -> RunnableConfig:
        config: RunnableConfig = {
            "callbacks": self._callbacks or None,
            "run_name": "fit_scoring",
        }
        if procurement_id is not None:
            config["metadata"] = {"procurement_id": procurement_id}
        return config

    def invoke(
        self,
        competencies: str,
        description: str,
        procurement_id: str | None = None,
    ) -> FitResult:
        """Выставить Fit-оценку (reasoning + fit_score)."""
        messages: list[BaseMessage] = build_fit_messages(competencies, description)
        result = self._structured.invoke(messages, config=self._config(procurement_id))
        return cast(FitResult, result)
