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
    from langfuse.langchain import CallbackHandler

    _CallbackHandler: Any = CallbackHandler
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
        request_timeout=settings.llm_request_timeout,
        max_retries=settings.llm_max_retries,
    )  # type: ignore[call-arg] # поддерживается runtime (уходит в openai-клиент)


def langfuse_handler(settings: Settings) -> object | None:
    """Вернуть Langfuse CallbackHandler или None (если LangFuse не настроен).

    langfuse>=4: LangChain-callback — ``langfuse.langchain.CallbackHandler``, который
    использует глобальный клиент (без secret_key/host в конструкторе). Поэтому перед
    созданием handler конфигурируем глобальный клиент ``Langfuse(...)`` из настроек.
    """
    if not _LANGFUSE_AVAILABLE or _CallbackHandler is None:
        return None
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    from langfuse import Langfuse

    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host or "https://cloud.langfuse.com",
        debug=False,
    )
    return cast(object, _CallbackHandler())


def callbacks_for(handler: object | None) -> list[Any] | None:
    """Привести LangFuse-handler к списку callbacks для LangChain (None при пустом)."""
    if handler is None:
        return None
    return [handler]


def flush_langfuse() -> None:
    """Принудительно отправить буферизованные LangFuse-трейсы.

    Нужен для одноразовых CLI-команд (score / score-csv / evaluate): процесс
    завершается сразу после вызова LLM, а глобальный клиент LangFuse отправляет
    трейсы асинхронно. Без явного ``flush()`` они теряются при выходе.
    В long-running процессах (worker/serve) клиент флашит сам, поэтому вызов
    безопасен и там.
    """
    if not _LANGFUSE_AVAILABLE:
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:  # pragma: no cover - best-effort, не должно ронять команду
        pass
