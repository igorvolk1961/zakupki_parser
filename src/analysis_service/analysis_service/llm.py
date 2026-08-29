"""Лёгкий OpenAI-совместимый клиент для вердиктов (httpx, без LangChain).

Верификация стоп-условий выполняется дешёвой LLM (сейчас DeepSeek; позже —
локальная лёгкая модель). Ответ — строгий JSON (``response_format``).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from scoring_common.costing import llm_cost_details
from scoring_common.langfuse import start_observation

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Грубая оценка токенов по числу символов (рус. текст ~3 симв./токен)."""
    return max(1, round(len(text) / 3))


class LlmClient:
    """Вызов ``/chat/completions`` (OpenAI-совместимый) со строгим JSON-ответом."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.0,
        timeout: float = 45.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._timeout = timeout

    async def chat_json(self, system: str, user: str) -> dict[str, Any] | None:
        """Запрос с JSON-ответом; None — сбой (best-effort, не роняет задание)."""
        url = f"{self._base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        obs = start_observation(
            name="verdict",
            as_type="generation",
            input=payload["messages"],
            metadata={"model": self._model, "temperature": self._temperature},
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            content = data["choices"][0]["message"]["content"]
            result: dict[str, Any] = json.loads(content)
            usage_details, cost_details = self._usage_and_cost(data, payload["messages"])
            obs.update(
                output=result,
                model=self._model,
                usage_details=usage_details,
                cost_details=cost_details,
            )
            obs.end()
            return result
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            obs.update(level="WARNING", status_message=f"LLM-вердикт не получен: {exc}")
            obs.end()
            logger.warning("LLM-вердикт не получен (%s): %s", self._model, exc)
            return None

    def _usage_and_cost(
        self, data: dict[str, Any], messages: list[dict[str, str]]
    ) -> tuple[dict[str, int], dict[str, float]]:
        """Usage и стоимость для Langfuse (DeepSeek: кэш-хит/мисс + пик/непик).

        Возвращает ``usage_details`` (взаимоисключающие бакеты input/output/
        input_cached_tokens) и ``cost_details`` (USD). Если LLM не вернула usage —
        оценка по символам (все токены входа трактуются как cache-miss).
        """
        usage = data.get("usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        cache_miss = int(usage.get("prompt_cache_miss_tokens", max(0, prompt - cache_hit)))
        if prompt == 0:
            cache_miss = sum(_estimate_tokens(str(m.get("content") or "")) for m in messages)
        usage_details: dict[str, int] = {"input": cache_miss, "output": completion}
        if cache_hit:
            usage_details["input_cached_tokens"] = cache_hit
        cost_details = llm_cost_details(
            self._model,
            cache_miss,
            completion,
            datetime.now(UTC),
            input_cache_hit=cache_hit,
        )
        return usage_details, cost_details
