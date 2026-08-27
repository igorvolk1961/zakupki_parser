"""Unit-тесты клиента transport-конвейера скоринга.

Дефолтный (внутренний) скоринг удалён: закупка сохраняется без оценки, результат
внешнего каскада пишется в ``procurement_evaluations`` через POST /score.
"""

from __future__ import annotations

import httpx
import pytest

from zakupki_parser.scoring import ScoringTransportClient


@pytest.mark.asyncio
async def test_transport_client_posts_job() -> None:
    captured: dict[str, bytes] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url).encode()
        captured["json"] = request.content
        return httpx.Response(202, json={"status": "enqueued"})

    transport = httpx.MockTransport(_handler)
    client = ScoringTransportClient("http://localhost:8200")
    await client.enqueue(42, 900.0, transport=transport)

    assert captured["url"] == b"http://localhost:8200/api/scoring/jobs"
    assert b'"procurement_id":42' in captured["json"]
    assert b'"priority":900.0' in captured["json"]
    assert b'"stage":"fit"' in captured["json"]


@pytest.mark.asyncio
async def test_transport_client_posts_job_stage() -> None:
    captured: dict[str, bytes] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.content
        return httpx.Response(202, json={"status": "enqueued"})

    transport = httpx.MockTransport(_handler)
    client = ScoringTransportClient("http://localhost:8200")
    await client.enqueue(42, 0.7, transport=transport, stage="pwin")

    assert b'"stage":"pwin"' in captured["json"]


@pytest.mark.asyncio
async def test_transport_client_sends_bearer_token() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(202, json={"status": "enqueued"})

    transport = httpx.MockTransport(_handler)
    client = ScoringTransportClient("http://localhost:8200", auth_token="t0ken")
    await client.enqueue(42, 900.0, transport=transport)

    assert captured["auth"] == "Bearer t0ken"


@pytest.mark.asyncio
async def test_transport_client_omits_bearer_when_unset() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(202, json={"status": "enqueued"})

    transport = httpx.MockTransport(_handler)
    client = ScoringTransportClient("http://localhost:8200")
    await client.enqueue(42, 900.0, transport=transport)

    assert captured["auth"] == ""
