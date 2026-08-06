"""Judge-цепочка: судья оценивает адекватность fit-оценки.

Отдельный контекст (новый запрос) с исходными данными и результатом fit-цепочки.
Возвращает ``JudgeResult`` (critics / verdict / final_fit_score).
"""

from __future__ import annotations

import json
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

from scoring_service.pipeline.prompts import build_judge_messages
from scoring_service.schemas import FitResult, JudgeResult


class JudgeChain:
    """Обёртка над judge-цепочкой LLM."""

    def __init__(self, llm: BaseChatModel, callbacks: list[Any] | None = None) -> None:
        self._structured = llm.with_structured_output(JudgeResult)
        self._callbacks = callbacks

    def _config(self, procurement_id: str | None = None) -> RunnableConfig:
        config: RunnableConfig = {
            "callbacks": self._callbacks or None,
            "run_name": "judge_scoring",
        }
        if procurement_id is not None:
            config["metadata"] = {"procurement_id": procurement_id}
        return config

    def invoke(
        self,
        competencies: str,
        description: str,
        fit_result: FitResult,
        procurement_id: str | None = None,
    ) -> JudgeResult:
        """Оценить адекватность fit-оценки."""
        fit_json = json.dumps(fit_result.model_dump(), ensure_ascii=False)
        messages: list[BaseMessage] = build_judge_messages(competencies, description, fit_json)
        result = self._structured.invoke(messages, config=self._config(procurement_id))
        return cast(JudgeResult, result)
