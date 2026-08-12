"""Judge-цепочка: судья оценивает адекватность fit-оценки.

Отдельный контекст (новый запрос) с исходными данными и результатом fit-цепочки.
Возвращает ``JudgeResult`` (critics / verdict / final_fit_score).

Устойчивость к дрейфу схемы (как и в fit): fallback с явной схемой и исправлением выхода.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from langchain.output_parsers import OutputFixingParser
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config

from scoring_service.pipeline.prompts import build_judge_messages
from scoring_service.schemas import FitResult, JudgeResult

logger = logging.getLogger(__name__)


class JudgeChain:
    """Обёртка над judge-цепочкой LLM."""

    def __init__(
        self,
        llm: BaseChatModel,
        callbacks: list[Any] | None = None,
        method: str = "json_mode",
    ) -> None:
        self._llm = llm
        self._structured = llm.with_structured_output(JudgeResult, method=method)
        self._parser = PydanticOutputParser(pydantic_object=JudgeResult)
        self._fixing = OutputFixingParser.from_llm(parser=self._parser, llm=llm)
        self._callbacks = callbacks

    def _config(
        self,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunnableConfig:
        config: dict[str, Any] = {
            "callbacks": self._callbacks or None,
            "run_name": "judge_scoring",
        }
        trace_meta = dict(metadata or {})
        if session_id is not None:
            # langfuse 4.x LangChain-callback читает session_id именно из этого
            # зарезервированного ключа metadata (config["session_id"] игнорируется).
            trace_meta["langfuse_session_id"] = session_id
        if trace_meta:
            config["metadata"] = trace_meta
        return cast(RunnableConfig, config)

    def _child_config(
        self,
        parent_config: RunnableConfig,
        session_id: str | None,
        metadata: dict[str, Any] | None,
        run_name: str,
    ) -> RunnableConfig:
        """Конфиг вложенного run: наследует callbacks родителя (для одного трейса)."""
        child = patch_config(parent_config, run_name=run_name)
        trace_meta = dict(metadata or {})
        if session_id is not None:
            trace_meta["langfuse_session_id"] = session_id
        child["metadata"] = {**(parent_config.get("metadata") or {}), **trace_meta}
        return child

    def invoke(
        self,
        competencies: str,
        description: str,
        fit_result: FitResult,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        parent_config: RunnableConfig | None = None,
    ) -> JudgeResult:
        """Оценить адекватность fit-оценки."""
        fit_json = json.dumps(fit_result.model_dump(), ensure_ascii=False)
        messages: list[BaseMessage] = build_judge_messages(competencies, description, fit_json)
        config = (
            self._config(session_id, metadata)
            if parent_config is None
            else self._child_config(parent_config, session_id, metadata, "judge_scoring")
        )
        try:
            result = self._structured.invoke(messages, config=config)
        except OutputParserException:
            logger.warning(
                "judge: структурированный выход не распарсился — fallback с исправлением"
            )
            result = self._invoke_with_fix(messages, config)
        return cast(JudgeResult, result)

    def _invoke_with_fix(
        self,
        messages: list[BaseMessage],
        config: RunnableConfig,
    ) -> JudgeResult:
        """Fallback: явная схема в промпте + исправление выхода при дрейфе."""
        format_instructions = self._parser.get_format_instructions()
        fix_messages: list[BaseMessage] = [
            *messages,
            SystemMessage(
                content=(
                    "Выведи ТОЛЬКО один валидный JSON-объект, строго соответствующий "
                    "приведённой ниже схеме (без пояснений, без ```json-обёртки):\n"
                    f"{format_instructions}"
                )
            ),
        ]
        text = cast(str, self._llm.invoke(fix_messages, config=config).content)
        return cast(JudgeResult, self._fixing.parse(text))
