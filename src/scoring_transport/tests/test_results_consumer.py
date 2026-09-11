"""Тесты роутинга ResultsConsumer: score-результаты vs результаты индексации."""

from __future__ import annotations

from typing import Any

import pytest

import scoring_transport.consumers.results as results_module
from scoring_transport.settings import Settings


class _SpyParser:
    def __init__(
        self, base_url: str, timeout: float = 30.0, internal_token: str | None = None
    ) -> None:
        self.post_score_calls: list[dict[str, Any]] = []
        self.post_index_result_calls: list[dict[str, Any]] = []

    async def post_score(
        self, procurement_id: int, score: float, score_method: str = "fit", **kwargs: Any
    ) -> dict[str, Any]:
        self.post_score_calls.append(
            {
                "procurement_id": procurement_id,
                "score": score,
                "score_method": score_method,
                **kwargs,
            }
        )
        return {}

    async def post_index_result(
        self, procurement_id: int, status: str, **kwargs: Any
    ) -> dict[str, Any]:
        self.post_index_result_calls.append(
            {"procurement_id": procurement_id, "status": status, **kwargs}
        )
        return {}


class _QueueStub:
    def __init__(self, payloads: list[dict[str, Any] | None]) -> None:
        self._payloads = payloads

    async def pop_result(self, timeout: float | None = None) -> dict[str, Any] | None:
        return self._payloads.pop(0) if self._payloads else None


def _make_consumer(payloads: list[dict[str, Any] | None]) -> tuple[Any, _SpyParser]:
    settings = Settings(parser_api_url="http://parser", redis_url="redis://fake")
    results_module.TransportQueue = lambda s: _QueueStub(payloads)  # type: ignore[assignment]
    results_module.ParserApiClient = _SpyParser  # type: ignore[assignment]
    consumer = results_module.ResultsConsumer(settings)
    return consumer, consumer._parser  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_process_once_routes_index_stage_to_post_index_result() -> None:
    consumer, parser = _make_consumer(
        [{"procurement_id": 5, "stage": "index", "status": "indexed", "document_text": "текст"}]
    )
    await consumer._process_once()  # noqa: SLF001
    assert len(parser.post_index_result_calls) == 1
    call = parser.post_index_result_calls[0]
    assert call["procurement_id"] == 5
    assert call["status"] == "indexed"
    assert call["document_text"] == "текст"
    assert parser.post_score_calls == []


@pytest.mark.asyncio
async def test_process_once_score_payload_unaffected_by_index_routing() -> None:
    consumer, parser = _make_consumer([{"procurement_id": 6, "score": 7.5, "score_method": "fit"}])
    await consumer._process_once()  # noqa: SLF001
    assert parser.post_score_calls and parser.post_score_calls[0]["procurement_id"] == 6
    assert parser.post_index_result_calls == []


@pytest.mark.asyncio
async def test_process_once_index_payload_without_status_is_skipped() -> None:
    consumer, parser = _make_consumer([{"procurement_id": 7, "stage": "index"}])
    await consumer._process_once()  # noqa: SLF001
    assert parser.post_index_result_calls == []
