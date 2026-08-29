"""Unit-тесты опциональной LangFuse-трассировки (scoring_common.langfuse)."""

from __future__ import annotations

import pytest

from scoring_common.langfuse import (
    _NoopObservation,
    langfuse_enabled,
    parent_span,
    start_observation,
)


def test_langfuse_disabled_by_default(monkeypatch) -> None:
    """Без ключей LANGFUSE_* трассировка выключена, вызовы — безопасный no-op."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert langfuse_enabled() is False
    obs = start_observation(
        "embeddings",
        as_type="embedding",
        input=["text"],
        metadata={"model": "m"},
    )
    assert isinstance(obs, _NoopObservation)
    obs.update(output=[1.0])
    obs.end()


def test_langfuse_enabled_with_keys(monkeypatch) -> None:
    """При заданных ключах возвращается реальное наблюдение с update/end."""
    pytest.importorskip("langfuse")  # langfuse ставится только в сервисах-потребителях
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    assert langfuse_enabled() is True
    obs = start_observation(
        "verdict",
        as_type="generation",
        input=[{"role": "user", "content": "hi"}],
        model="m",
    )
    assert not isinstance(obs, _NoopObservation)
    assert callable(getattr(obs, "update", None))
    assert callable(getattr(obs, "end", None))
    obs.update(output={"ok": True})
    obs.end()


class _FakeObservation:
    trace_id: str = ""
    id: str = ""

    def update(self, *args: object, **kwargs: object) -> None:
        return None

    def end(self, *args: object, **kwargs: object) -> None:
        return None


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def start_observation(self, **kwargs: object) -> _FakeObservation:
        self.calls.append(dict(kwargs))
        obs = _FakeObservation()
        obs.trace_id = f"trace-{len(self.calls)}"
        obs.id = f"span-{len(self.calls)}"
        return obs


def test_parent_span_disabled_noop(monkeypatch) -> None:
    """Без ключей LANGFUSE_* parent_span — безопасный no-op (yield None)."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with parent_span("rag_analysis") as root:
        assert root is None
    # После no-op-контекста родитель не установлен: наблюдения по-прежнему корневые.
    assert isinstance(start_observation("x", as_type="embedding"), _NoopObservation)


def test_parent_span_nests_children(monkeypatch) -> None:
    """Наблюдения внутри parent_span вкладываются в общий родительский span."""
    from scoring_common import langfuse as lf

    monkeypatch.setattr(lf, "_LANGFUSE_AVAILABLE", True)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    fake = _FakeLangfuseClient()
    monkeypatch.setattr(lf, "_get_client", lambda: fake)

    with lf.parent_span("rag_analysis", metadata={"procurement_id": 7}) as parent:
        lf.start_observation("embeddings", as_type="embedding", input=["text"])
        lf.start_observation("verdict", as_type="generation", model="m")

    root_call = fake.calls[0]
    assert root_call["name"] == "rag_analysis"
    assert root_call["as_type"] == "span"
    assert root_call["metadata"] == {"procurement_id": 7}

    embedding_call = fake.calls[1]
    assert embedding_call["name"] == "embeddings"
    assert embedding_call["as_type"] == "embedding"
    assert embedding_call["trace_context"] == {
        "trace_id": parent.trace_id,
        "parent_span_id": parent.id,
    }

    verdict_call = fake.calls[2]
    assert verdict_call["name"] == "verdict"
    assert verdict_call["trace_context"] == {
        "trace_id": parent.trace_id,
        "parent_span_id": parent.id,
    }
