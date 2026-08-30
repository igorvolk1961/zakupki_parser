"""Опциональная LangFuse-трассировка LLM-вызовов и эмбеддингов каскада.

LangFuse включается автоматически, если заданы стандартные переменные окружения
``LANGFUSE_PUBLIC_KEY`` и ``LANGFUSE_SECRET_KEY`` (``LANGFUSE_HOST`` по умолчанию —
https://cloud.langfuse.com). Без ключей трассировка отключена: все вызовы — no-op,
ничего не ломают и не шлют.

Используется:
- ``analysis_service.llm.LlmClient`` — LLM-вердикты RAG-анализа;
- ``scoring_common.embeddings.EmbeddingClient``/``GigaEmbeddingClient`` и
  ``scoring_common.giga.GigaEmbedder`` — эмбеддинги;
- как ``scoring_service.llm_factory`` для LangChain-callback'ов (там свой путь).

``parent_span`` позволяет вложить наблюдения единицы работы в общий родительский
span: эмбеддинги и вердикты одного задания образуют один трейс (общий родитель)
вместо отдельных корневых наблюдений.

В ``scoring_service`` корневой трейс ведёт LangChain-callback, а не ``parent_span``:
поэтому ``start_observation`` дополнительно вкладывает наблюдения в текущий
активный OTel-спан (см. ``_otel_parent_trace_context``) — этап эмбеддингов
попадает в общий трейс скоринга.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

try:  # langfuse — опциональная зависимость
    from langfuse import Langfuse

    _LANGFUSE_AVAILABLE = True
except Exception:  # pragma: no cover - langfuse не установлен
    _LANGFUSE_AVAILABLE = False

try:  # opentelemetry — транзитивная зависимость langfuse (родитель по активному span'у)
    from opentelemetry import trace as _otel_trace

    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover - opentelemetry не установлен
    _OTEL_AVAILABLE = False

_client: Any = None


@dataclass(frozen=True)
class _TraceParent:
    """Родительский контекст наблюдения: идентификаторы трейса и спана."""

    trace_id: str
    span_id: str


# Текущий родитель наблюдений. Контекст-зависимая переменная: ``asyncio.to_thread``
# копирует её в рабочий поток, поэтому родительский контекст доезжает и до
# эмбеддингов, выполняемых в потоке (``GigaEmbeddingClient``).
_current_parent: contextvars.ContextVar[_TraceParent | None] = contextvars.ContextVar(
    "langfuse_current_parent", default=None
)


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


def _otel_parent_trace_context() -> dict[str, str] | None:
    """``trace_context`` активного OTel-спана (родителя) или None.

    Используется как фолбэк к ``_current_parent``: если родительский контекст не
    задан явно (scoring_service ведёт корневой трейс через LangChain-callback),
    наблюдения вкладываются в текущий активный span — так этап эмбеддингов
    попадает в общий трейс скоринга.
    """
    if not _OTEL_AVAILABLE:
        return None
    try:
        span = _otel_trace.get_current_span()
        ctx = span.get_span_context()
    except Exception:  # noqa: BLE001 - best-effort, не роняет работу
        return None
    if not ctx.is_valid:
        return None
    return {
        "trace_id": _otel_trace.format_trace_id(ctx.trace_id),
        "parent_span_id": _otel_trace.format_span_id(ctx.span_id),
    }


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
    parent = _current_parent.get()
    trace_context = None
    if parent is not None:
        trace_context = {"trace_id": parent.trace_id, "parent_span_id": parent.span_id}
    else:
        trace_context = _otel_parent_trace_context()
    return _get_client().start_observation(
        name=name,
        as_type=as_type,
        input=input,
        output=output,
        metadata=metadata,
        model=model,
        model_parameters=model_parameters,
        trace_context=trace_context,
    )


@contextmanager
def parent_span(
    name: str,
    *,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Открыть родительский span: все наблюдения внутри станут его потомками.

    Единица работы (например, RAG-анализ одной закупки) оборачивается в один
    родительский span, поэтому эмбеддинги и LLM-вердикты вкладываются в общий
    трейс как дочерние спаны вместо отдельных корневых наблюдений.

    Объект наблюдения доступен через ``yield`` (можно вызвать ``update()``, но не
    ``end()`` — родитель закрывается самим контекст-менеджером). При выключенной
    трассировке — no-op (yield ``None``, контекст не трогаем).
    """
    if not langfuse_enabled():
        yield None
        return
    try:
        obs = _get_client().start_observation(
            name=name, as_type="span", input=input, metadata=metadata
        )
    except Exception:  # noqa: BLE001 - best-effort, не роняет работу
        yield None
        return
    token = _current_parent.set(_TraceParent(trace_id=obs.trace_id, span_id=obs.id))
    try:
        yield obs
    finally:
        _current_parent.reset(token)
        obs.end()


def flush() -> None:
    """Принудительно отправить буферизованные наблюдения (для CLI-команд)."""
    if not langfuse_enabled():
        return
    with contextlib.suppress(Exception):  # pragma: no cover - best-effort
        _get_client().flush()


def trace_url_from_trace_id(trace_id: str | None) -> str | None:
    """Ссылка на трейс LangFuse по ``trace_id``; None при выключенной трассировке.

    Возвращает ``None``, если трейс не задан или LangFuse недоступен — вызывающая
    сторона просто не показывает кнопку «Трейс» (best-effort, не роняет работу).
    """
    if not trace_id or not langfuse_enabled():
        return None
    try:
        return _get_client().get_trace_url(trace_id=trace_id)  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001 - best-effort, не роняет работу
        return None
