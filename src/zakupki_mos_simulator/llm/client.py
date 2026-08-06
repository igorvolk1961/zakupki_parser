"""OpenAI-совместимый клиент для генерации тестовой выборки (httpx)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from zakupki_mos_simulator.settings import Settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Ошибка обращения к LLM."""


class LLMClient:
    """Минимальный клиент к ``POST {base_url}/chat/completions``.

    Совместим с OpenAI API и локальными серверами (Ollama и др.), реализующими
    ``/v1/chat/completions``.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = httpx.Timeout(settings.llm_timeout_seconds)

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self._settings.llm_api_key}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise LLMError(f"LLM вернул {resp.status_code}: {resp.text[:500]}")
            try:
                return dict(resp.json())
            except ValueError as exc:
                raise LLMError(f"Невалидный JSON от LLM: {resp.text[:500]}") from exc

    def _content(self, data: dict[str, Any]) -> str:
        try:
            message = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Нет choices[0].message.content в ответе LLM: {data!r}") from exc
        return str(message).strip()

    async def chat_json(
        self, system: str, user: str, *, json_schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Вызывает chat-completions и разбирает ответ как JSON.

        При невалидном JSON выполняет до ``llm_max_retries`` повторных попыток.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": messages,
            "temperature": self._settings.temperature,
        }
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "dataset",
                    "strict": True,
                    "schema": json_schema,
                },
            }

        last_err: Exception | None = None
        for attempt in range(1, self._settings.llm_max_retries + 1):
            try:
                data = await self._post(payload)
                content = self._content(data)
                return dict(json.loads(content))
            except (json.JSONDecodeError, LLMError) as exc:
                last_err = exc
                logger.warning(
                    "Попытка LLM %d/%d не удалась: %s",
                    attempt,
                    self._settings.llm_max_retries,
                    exc,
                )
                if attempt < self._settings.llm_max_retries:
                    await asyncio.sleep(2**attempt)
        raise LLMError(
            f"LLM не вернул валидный JSON после "
            f"{self._settings.llm_max_retries} попыток: {last_err}"
        )
