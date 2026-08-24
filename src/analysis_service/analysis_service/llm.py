"""Лёгкий OpenAI-совместимый клиент для вердиктов (httpx, без LangChain).

Верификация стоп-условий выполняется дешёвой LLM (сейчас DeepSeek; позже —
локальная лёгкая модель). Ответ — строгий JSON (``response_format``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from scoring_common.langfuse import start_observation

logger = logging.getLogger(__name__)


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
        payload = {
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
            obs.update(output=result, model=self._model)
            obs.end()
            return result
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            obs.update(level="WARNING", status_message=f"LLM-вердикт не получен: {exc}")
            obs.end()
            logger.warning("LLM-вердикт не получен (%s): %s", self._model, exc)
            return None
