"""Фабрика LLM и LangFuse-обработчика.

- ``build_llm``: OpenAI-совместимый ``ChatOpenAI`` (base_url/api_key/model из настроек).
- ``langfuse_handler``: ``langfuse.CallbackHandler`` либо ``None``, если LangFuse
  не настроен (dev fallback — вызовы идут без трассировки).
"""

from __future__ import annotations

from typing import Any, cast

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from scoring_service.settings import Settings

try:  # langfuse — опциональная зависимость
    from langfuse.callback import CallbackHandler as _CallbackHandler

    _LANGFUSE_AVAILABLE = True
except Exception:  # pragma: no cover - импорт недоступен
    _CallbackHandler = None
    _LANGFUSE_AVAILABLE = False


def build_llm(settings: Settings) -> ChatOpenAI:
    """Создать OpenAI-совместимую LLM."""
    return ChatOpenAI(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        base_url=settings.llm_base_url,
        api_key=SecretStr(settings.llm_api_key),
    )


def langfuse_handler(settings: Settings) -> object | None:
    """Вернуть Langfuse CallbackHandler или None (если LangFuse не настроен)."""
    if not _LANGFUSE_AVAILABLE or _CallbackHandler is None:
        return None
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    return cast(
        object,
        _CallbackHandler(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host or "https://cloud.langfuse.com",
        ),
    )


def callbacks_for(handler: object | None) -> list[Any] | None:
    """Привести LangFuse-handler к списку callbacks для LangChain (None при пустом)."""
    if handler is None:
        return None
    return [handler]
