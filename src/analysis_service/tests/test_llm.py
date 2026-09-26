"""Тесты app-side стоимости LLM-клиента (usage/cost для Langfuse) и устойчивости
``chat_json`` к временным сбоям (найдено вживую — аудит анализа профиля
«Экопаттерн»: DeepSeek изредка отдаёт пустой/невалидный JSON без ретрая раньше
ронял результат по одному полю целиком)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from analysis_service.llm import LlmClient


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://x/chat/completions")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"{status_code}", request=request, response=response)


async def _no_delay(_seconds: float) -> None:
    """Замена ``asyncio.sleep`` в тестах ретрая — без реальной задержки.

    Нельзя использовать ``lambda _s: asyncio.sleep(0)``: patch-таргет
    ``analysis_service.llm.asyncio`` — тот же объект модуля, что и ``asyncio``
    в этом файле, так что вызов ``asyncio.sleep`` внутри лямбды снова попадает
    на подмену (бесконечная рекурсия).
    """
    return None


def test_chat_json_retries_on_invalid_json_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LlmClient("http://x", "deepseek-v4-flash")
    calls = 0

    async def fake_chat_once(system: str, user: str) -> dict:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise json.JSONDecodeError("Expecting value", "", 0)
        return {"found": True}

    monkeypatch.setattr(client, "_chat_once", fake_chat_once)
    monkeypatch.setattr("analysis_service.llm.asyncio.sleep", _no_delay)
    assert asyncio.run(client.chat_json("sys", "usr")) == {"found": True}
    assert calls == 2


def test_chat_json_retries_on_retryable_status(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LlmClient("http://x", "deepseek-v4-flash")
    calls = 0

    async def fake_chat_once(system: str, user: str) -> dict:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise _status_error(429)
        return {"found": True}

    monkeypatch.setattr(client, "_chat_once", fake_chat_once)
    monkeypatch.setattr("analysis_service.llm.asyncio.sleep", _no_delay)
    assert asyncio.run(client.chat_json("sys", "usr")) == {"found": True}
    assert calls == 2


def test_chat_json_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LlmClient("http://x", "deepseek-v4-flash")
    calls = 0

    async def always_fails(system: str, user: str) -> dict:
        nonlocal calls
        calls += 1
        raise _status_error(503)

    monkeypatch.setattr(client, "_chat_once", always_fails)
    monkeypatch.setattr("analysis_service.llm.asyncio.sleep", _no_delay)
    assert asyncio.run(client.chat_json("sys", "usr")) is None
    assert calls == client._MAX_RETRY_ATTEMPTS


def test_chat_json_non_retryable_status_fails_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LlmClient("http://x", "deepseek-v4-flash")
    calls = 0

    async def bad_request(system: str, user: str) -> dict:
        nonlocal calls
        calls += 1
        raise _status_error(400)

    monkeypatch.setattr(client, "_chat_once", bad_request)
    assert asyncio.run(client.chat_json("sys", "usr")) is None
    assert calls == 1  # без повторов — 4xx (кроме 429) не временный сбой


def test_usage_and_cost_deepseek() -> None:
    """DeepSeek: usage разбивается на кэш-хит/мисс, cost_details — по типам."""
    client = LlmClient("http://x", "deepseek-v4-flash")
    data = {
        "usage": {
            "prompt_tokens": 110,
            "prompt_cache_hit_tokens": 10,
            "prompt_cache_miss_tokens": 100,
            "completion_tokens": 50,
        }
    }
    usage, cost = client._usage_and_cost(data, [{"role": "system", "content": "s"}])
    assert usage == {"input": 100, "input_cached_tokens": 10, "output": 50}
    assert set(cost) == {"input", "input_cached_tokens", "output"}
    assert cost["input"] > 0 and cost["input_cached_tokens"] > 0 and cost["output"] > 0


def test_usage_and_cost_fallback_without_usage() -> None:
    """Без usage в ответе — оценка токенов по символам (все входы = cache-miss)."""
    client = LlmClient("http://x", "deepseek-v4-flash")
    usage, _cost = client._usage_and_cost({}, [{"role": "system", "content": "0123456789"}])
    assert usage["input"] == 3  # 10 симв. / 3
    assert usage["output"] == 0
