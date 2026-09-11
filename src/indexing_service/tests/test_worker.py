"""Тесты воркера Index: извлечение текста документов и поведение при сбоях."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from fakeredis.aioredis import FakeRedis, FakeServer
from indexing_service.settings import Settings
from indexing_service.worker import IndexWorker


class _StubParser:
    """Парсер, возвращающий фиксированную карточку закупки."""

    def __init__(self, record: dict | None = None) -> None:
        self._record = record or {"id": 1, "subject": "Разработка ПО", "files_json": []}

    async def get_procurement(self, procurement_id: int) -> dict:
        return self._record


class _UnreachableParser:
    async def get_procurement(self, procurement_id: int) -> dict:
        raise httpx.ConnectError(
            "All connection attempts failed", request=httpx.Request("GET", "http://x")
        )


class _InternalErrorParser:
    async def get_procurement(self, procurement_id: int) -> dict:
        resp = httpx.Response(500, request=httpx.Request("GET", "http://x"))
        raise httpx.HTTPStatusError("Server error", request=resp.request, response=resp)


class _MissingParser:
    async def get_procurement(self, procurement_id: int) -> dict:
        resp = httpx.Response(404, request=httpx.Request("GET", "http://x"))
        raise httpx.HTTPStatusError("Not found", request=resp.request, response=resp)


@pytest.fixture
async def worker_queue():
    settings = Settings(parser_retry_backoff_seconds=0.0, download_delay_seconds=0.0)
    worker = IndexWorker(settings)
    worker._queue._client = FakeRedis(server=FakeServer(), decode_responses=True)  # noqa: SLF001
    yield worker
    await worker._queue.close()


async def _published_payload(worker) -> dict:
    results = worker._queue._settings.results_key
    payload = await worker._queue._client.lindex(results, 0)
    return json.loads(payload)


async def test_no_files_publishes_empty_indexed(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _StubParser({"id": 1, "files_json": []})
    await worker._queue.enqueue(1, 0.5, profile_id=0)
    await worker._process_once()

    data = await _published_payload(worker)
    assert data["procurement_id"] == 1
    assert data["stage"] == "index"
    assert data["status"] == "indexed"
    assert data["document_text"] == ""


async def test_extracts_and_publishes_document_text(worker_queue, monkeypatch) -> None:
    worker = worker_queue
    worker._parser = _StubParser(
        {"id": 2, "files_json": [{"name": "tz.docx", "url": "http://x/tz.docx"}]}
    )

    def _fake_extract(ref, timeout, verify_ssl=True):
        assert ref.name == "tz.docx"
        return "текст технического задания"

    monkeypatch.setattr("indexing_service.worker.extract_text_cached", _fake_extract)

    await worker._queue.enqueue(2, 0.5, profile_id=0)
    await worker._process_once()

    data = await _published_payload(worker)
    assert data["status"] == "indexed"
    assert data["document_text"] == "текст технического задания"
    assert data["content_hash"] == hashlib.sha256("текст технического задания".encode()).hexdigest()


async def test_partial_failure_still_indexed(worker_queue, monkeypatch) -> None:
    worker = worker_queue
    worker._parser = _StubParser(
        {
            "id": 3,
            "files_json": [
                {"name": "ok.txt", "url": "http://x/ok.txt"},
                {"name": "broken.txt", "url": "http://x/broken.txt"},
            ],
        }
    )

    def _fake_extract(ref, timeout, verify_ssl=True):
        if ref.name == "broken.txt":
            raise RuntimeError("boom")
        return "ok text"

    monkeypatch.setattr("indexing_service.worker.extract_text_cached", _fake_extract)

    await worker._queue.enqueue(3, 0.5, profile_id=0)
    await worker._process_once()

    data = await _published_payload(worker)
    assert data["status"] == "indexed"
    assert data["document_text"] == "ok text"


async def test_all_downloads_fail_marks_error(worker_queue, monkeypatch) -> None:
    worker = worker_queue
    worker._parser = _StubParser(
        {"id": 4, "files_json": [{"name": "broken.txt", "url": "http://x/broken.txt"}]}
    )

    def _fake_extract(ref, timeout, verify_ssl=True):
        raise RuntimeError("boom")

    monkeypatch.setattr("indexing_service.worker.extract_text_cached", _fake_extract)

    await worker._queue.enqueue(4, 0.5, profile_id=0)
    await worker._process_once()

    data = await _published_payload(worker)
    assert data["status"] == "error"
    assert "boom" in data["error_message"]


async def test_transient_parser_error_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _UnreachableParser()
    await worker._queue.enqueue(133, 0.5, profile_id=0)
    await worker._process_once()
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:133:pf:0")
    assert score == 0.5


async def test_http_500_requeues_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _InternalErrorParser()
    await worker._queue.enqueue(5, 0.4, profile_id=0)
    await worker._process_once()
    score = await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:5:pf:0")
    assert score == 0.4


async def test_http_404_drops_job(worker_queue) -> None:
    worker = worker_queue
    worker._parser = _MissingParser()
    await worker._queue.enqueue(7, 0.3, profile_id=0)
    await worker._process_once()
    assert (
        await worker._queue._client.zscore(worker._queue._settings.jobs_key, "proc:7:pf:0") is None
    )
