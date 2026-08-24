"""Unit-тесты опциональной LangFuse-трассировки (scoring_common.langfuse)."""

from __future__ import annotations

import pytest

from scoring_common.langfuse import (
    _NoopObservation,
    langfuse_enabled,
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
