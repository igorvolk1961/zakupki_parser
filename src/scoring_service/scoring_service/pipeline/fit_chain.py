"""Fit-цепочка: оценка Fit (0..10) по описанию закупки и компетенциям.

Langchain: сообщения из ``prompts.build_fit_messages`` (few-shot + negative-example)
→ ``with_structured_output(FitResult)`` — строгий JSON-выход по pydantic-схеме.

Устойчивость: некоторые OpenAI-совместимые модели (DeepSeek) поддерживают только
``json_mode``, при котором возможен дрейф от схемы (например, вложенный ``fit_score``).
На такой случай — fallback с явной схемой и исправлением выхода (``OutputFixingParser``).
"""

from __future__ import annotations

import logging
from typing import Any, cast

from langchain.output_parsers import OutputFixingParser
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config

from scoring_service.pipeline.prompts import build_fit_messages
from scoring_service.schemas import FitResult

logger = logging.getLogger(__name__)


class FitChain:
    """Обёртка над fit-цепочкой LLM."""

    def __init__(
        self,
        llm: BaseChatModel,
        callbacks: list[Any] | None = None,
        method: str = "json_mode",
    ) -> None:
        self._llm = llm
        self._structured = llm.with_structured_output(FitResult, method=method)
        self._parser = PydanticOutputParser(pydantic_object=FitResult)
        self._fixing = OutputFixingParser.from_llm(parser=self._parser, llm=llm)
        self._callbacks = callbacks

    def _config(
        self,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        run_name: str = "fit_scoring",
    ) -> RunnableConfig:
        config: dict[str, Any] = {
            "callbacks": self._callbacks or None,
            "run_name": run_name,
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
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        parent_config: RunnableConfig | None = None,
        run_name: str = "fit_scoring",
        truncated: bool = False,
    ) -> FitResult:
        """Выставить Fit-оценку (reasoning + fit_score).

        ``truncated`` — описание обрезано многоточием: добавляется явное указание
        на неполноту описания (см. ``prompts.build_fit_messages``).
        """
        messages: list[BaseMessage] = build_fit_messages(
            competencies, description, truncated=truncated
        )
        config = (
            self._config(session_id, metadata, run_name)
            if parent_config is None
            else self._child_config(parent_config, session_id, metadata, run_name)
        )
        try:
            result = self._structured.invoke(messages, config=config)
        except OutputParserException:
            logger.warning("fit: структурированный выход не распарсился — fallback с исправлением")
            result = self._invoke_with_fix(messages, config)
        return cast(FitResult, result)

    def _invoke_with_fix(
        self,
        messages: list[BaseMessage],
        config: RunnableConfig,
    ) -> FitResult:
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
        return cast(FitResult, self._fixing.parse(text))
