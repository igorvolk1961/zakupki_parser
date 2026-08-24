"""Опциональная LangFuse-трассировка LLM-вызовов и эмбеддингов каскада.

LangFuse включается автоматически, если заданы стандартные переменные окружения
``LANGFUSE_PUBLIC_KEY`` и ``LANGFUSE_SECRET_KEY`` (``LANGFUSE_HOST`` по умолчанию —
https://cloud.langfuse.com). Без ключей трассировка отключена: все вызовы — no-op,
ничего не ломают и не шлют.

Используется:
- ``analysis_service.llm.LlmClient`` — LLM-вердикты RAG-анализа;
- ``scoring_common.embeddings.EmbeddingClient`` и ``scoring_service`` GigaEmbedder —
  эмбеддинги;
- как ``scoring_service.llm_factory`` для LangChain-callback'ов (там свой путь).
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

try:  # langfuse — опциональная зависимость
    from langfuse import Langfuse

    _LANGFUSE_AVAILABLE = True
except Exception:  # pragma: no cover - langfuse не установлен
    _LANGFUSE_AVAILABLE = False

_client: Any = None


class _NoopObservation:
    """Заглушка наблюдения LangFuse (трассировка выключена)."""

    def update(self, *args: Any, **kwargs: Any) -> None:
        return None

    def end(self, *args: Any, **kwargs: Any) -> None:
        return None


def langfuse_enabled() -> bool:
    """Настроены ли ключи LangFuse (публичный + секретный)."""
    return bool(
        _LANGFUSE_AVAILABLE
        and os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def _get_client() -> Any:
    """Глобальный клиент LangFuse (конфигурация из окружения)."""
    global _client
    if _client is None:
        _client = Langfuse()
    return _client


def start_observation(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    output: Any = None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
) -> Any:
    """Начать наблюдение LangFuse; при выключенной трассировке — no-op.

    Возвращаемый объект имеет ``update(...)`` и ``end()`` (``_NoopObservation``,
    если LangFuse не настроен) — вызывающий код вызывает их безусловно.
    """
    if not langfuse_enabled():
        return _NoopObservation()
    return _get_client().start_observation(
        name=name,
        as_type=as_type,
        input=input,
        output=output,
        metadata=metadata,
        model=model,
        model_parameters=model_parameters,
    )


def flush() -> None:
    """Принудительно отправить буферизованные наблюдения (для CLI-команд)."""
    if not langfuse_enabled():
        return
    with contextlib.suppress(Exception):  # pragma: no cover - best-effort
        _get_client().flush()
