"""Тесты app-side стоимости LLM-клиента (usage/cost для Langfuse)."""

from __future__ import annotations

from analysis_service.llm import LlmClient


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
