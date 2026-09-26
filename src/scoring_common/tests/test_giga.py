"""Unit-тесты устойчивости GigaEmbedder к временным сбоям Giga API.

Живой инцидент (аудит анализа профиля «Экопаттерн», 2026-09-26): при повторном
запуске анализа реальные ответы Giga Embeddings — `413 Request Entity Too
Large` (закупка целиком не оценена) и серия `429 Too Many Requests` (закупка
довыполнилась только благодаря случайному успеху между повторами воркера).
`GigaEmbedder.embed()` раньше падал на первой же такой ошибке, роняя эмбеддинг
ВСЕХ чанков закупки, а не только проблемного.
"""

from __future__ import annotations

import httpx
import pytest

from scoring_common.giga import GigaEmbedder, GigaTokenProvider


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://x/embeddings")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"{status_code}", request=request, response=response)


def _embedder() -> GigaEmbedder:
    token_provider = GigaTokenProvider(
        auth_url="http://x/oauth", client_id="cid", client_secret="secret"
    )
    return GigaEmbedder(base_url="http://x", model="EmbeddingsGigaR", token_provider=token_provider)


def test_embed_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    embedder = _embedder()
    calls: list[str] = []

    def fake_embed_raw(text: str) -> list[float]:
        calls.append(text)
        if len(calls) < 3:
            raise _status_error(429)
        return [1.0, 2.0]

    monkeypatch.setattr(embedder, "_embed_raw", fake_embed_raw)
    monkeypatch.setattr("scoring_common.giga.time.sleep", lambda _seconds: None)
    assert embedder.embed(["текст"]) == [[1.0, 2.0]]
    # Один и тот же текст, без деления — только повтор.
    assert calls == ["текст"] * 3


def test_embed_gives_up_after_max_retries_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    embedder = _embedder()
    calls = 0

    def always_429(text: str) -> list[float]:
        nonlocal calls
        calls += 1
        raise _status_error(429)

    monkeypatch.setattr(embedder, "_embed_raw", always_429)
    monkeypatch.setattr("scoring_common.giga.time.sleep", lambda _seconds: None)
    with pytest.raises(httpx.HTTPStatusError):
        embedder.embed(["текст"])
    assert calls == embedder._MAX_RETRY_ATTEMPTS


def test_embed_splits_text_in_half_on_413(monkeypatch: pytest.MonkeyPatch) -> None:
    embedder = _embedder()
    # _chunks() делает text.strip() перед возвратом единственного чанка —
    # сравниваем уже с ним, а не с исходной (непрочищенной) строкой теста.
    text = ("слово " * 200).strip()  # длиннее _MIN_SPLITTABLE_CHARS
    seen: list[str] = []

    def fake_embed_raw(chunk: str) -> list[float]:
        seen.append(chunk)
        if chunk == text:
            raise _status_error(413)
        # Половины проходят — вернём вектор, зависящий от длины куска.
        return [float(len(chunk)), 0.0]

    monkeypatch.setattr(embedder, "_embed_raw", fake_embed_raw)
    vector = embedder.embed([text])[0]
    # Текст был поделён (в seen — исходный + минимум 2 половины).
    assert len(seen) >= 3
    assert seen[0] == text
    halves = seen[1:]
    # Итог — среднее векторов половин (не равно вектору целого текста).
    assert vector[0] == pytest.approx(sum(len(h) for h in halves) / len(halves))


def test_embed_413_on_tiny_text_raises_without_infinite_recursion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedder = _embedder()

    def always_413(text: str) -> list[float]:
        raise _status_error(413)

    monkeypatch.setattr(embedder, "_embed_raw", always_413)
    with pytest.raises(httpx.HTTPStatusError):
        embedder.embed(["короткий текст"])


def test_embed_non_retryable_status_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    embedder = _embedder()
    calls = 0

    def always_500(text: str) -> list[float]:
        nonlocal calls
        calls += 1
        raise _status_error(500)

    monkeypatch.setattr(embedder, "_embed_raw", always_500)
    with pytest.raises(httpx.HTTPStatusError):
        embedder.embed(["текст"])
    assert calls == 1  # без повторов и без деления
