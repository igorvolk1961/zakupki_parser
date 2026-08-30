"""Клиент Giga Embedder (Sber GigaChat): эмбеддинги + автообновление OAuth-токена.

- ``GigaTokenProvider``: получает ``access_token`` по OAuth 2.0 (client_credentials)
  и автоматически обновляет его при истечении ``expires_in``.
- ``GigaEmbedder``: POST /embeddings с Bearer-токеном; при 401 принудительно
  сбрасывает и получает свежий токен, затем повторяет запрос.

Используется и scoring_service (ветка векторной близости), и analysis_service
(RAG-анализ стоп-условий, см. ``scoring_common.embeddings.GigaEmbeddingClient``).

Клиенты ``httpx`` переиспользуются между запросами (без пересоздания на каждый
чанк), а при сетевом сбое эмбеддер уходит в короткий «кулдаун», чтобы повторные
обработки не ждали полный таймаут подключения.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Mapping
from typing import Any

import httpx

from scoring_common.costing import embedding_cost_usd, embedding_input_tokens
from scoring_common.langfuse import start_observation

# Общие дефолты настроек Giga. Единый источник, чтобы у scoring_service и
# analysis_service не расходились модель/эндпоинты (риск дрейфа).
GIGA_BASE_URL = "https://gigachat.devices.sberbank.ru/api/v1"
GIGA_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGA_EMBEDDINGS_MODEL = "EmbeddingsGigaR"
GIGA_AUTH_SCOPE = "GIGACHAT_API_PERS"
GIGA_DEFAULT_TIMEOUT_SECONDS = 30.0
GIGA_DEFAULT_MIN_TOKEN_TTL_SECONDS = 60.0


class GigaTokenError(RuntimeError):
    """Ошибка получения/обновления токена Giga."""


class GigaTokenProvider:
    """Выдаёт Bearer-токен, обновляя его по истечении expires_in."""

    def __init__(
        self,
        auth_url: str,
        client_id: str,
        client_secret: str,
        scope: str = "GIGACHAT_API_PERS",
        min_ttl_seconds: float = 60.0,
        verify_ssl: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self._auth_url = auth_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._min_ttl = min_ttl_seconds
        # Переиспользуемый клиент: соединения/пул живут на время жизни воркера.
        self._client = httpx.Client(timeout=timeout, verify=verify_ssl)
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> tuple[str, float]:
        resp = self._client.post(
            self._auth_url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                # Sber требует заголовок RqUID (UUID запроса) — без него 400.
                "RqUID": str(uuid.uuid4()),
            },
            data={
                "scope": self._scope,
                "grant_type": "client_credentials",
            },
            auth=(self._client_id, self._client_secret),
        )
        if resp.status_code >= 400:
            raise GigaTokenError(f"OAuth failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        expires_in = float(data.get("expires_in") or 300.0)
        return str(data["access_token"]), time.time() + expires_in

    def get_token(self) -> str:
        """Возвращает действующий токен (обновляет при необходимости).

        Быстрый путь (действующий токен) не берёт блокировку; сетевое обновление
        выполняется ВНЕ блокировки, чтобы недоступный auth-эндпоинт не сериализовал
        все потоки. Допустим редкий повторный OAuth-запрос при истечении токена.
        """
        with self._lock:
            if self._token is not None and (self._expires_at - time.time()) >= self._min_ttl:
                return self._token
        token, expires_at = self._fetch()
        with self._lock:
            if self._token is None or (self._expires_at - time.time()) < self._min_ttl:
                self._token, self._expires_at = token, expires_at
        return self._token

    def reset(self) -> None:
        """Принудительно сбросить кэш токена (для повторного получения)."""
        with self._lock:
            self._token = None
            self._expires_at = 0.0


class GigaEmbedder:
    """Эмбеддинги текста через GigaChat (best-effort: не роняет скоринг).

    Синхронный клиент. Для асинхронного использования (analysis_service) оберните
    его в ``scoring_common.embeddings.GigaEmbeddingClient``.
    """

    # Консервативный лимит длины текста в одном запросе (в символах): для модели
    # EmbeddingsGigaR окно 4096 токенов (~15000 символов RU). Длинный текст
    # (например, расширенный профиль компетенций) разбивается на чанки,
    # эмбеддинги усредняются.
    MAX_CHARS_PER_CHUNK = 12000

    def __init__(
        self,
        base_url: str,
        model: str,
        token_provider: GigaTokenProvider,
        verify_ssl: bool = True,
        timeout: float = 60.0,
        failure_cooldown_seconds: float = 60.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._tokens = token_provider
        # Переиспользуемый клиент: один на время жизни эмбеддера.
        self._client = httpx.Client(timeout=timeout, verify=verify_ssl)
        # Краткий «кулдаун» после сетевого сбоя: не ждать полный таймаут
        # подключения на каждом следующем задании.
        self._cooldown_until = 0.0
        self._failure_cooldown_seconds = failure_cooldown_seconds
        self._cost_usd: float = 0.0
        self._usage: dict[str, int] = {}
        self._cost_details: dict[str, float] = {}
        self._calls = 0
        self._latency_ms = 0.0

    @property
    def total_cost(self) -> float:
        """Накопленная стоимость эмбеддингов (USD, best-effort)."""
        return round(self._cost_usd, 8)

    def reset_cost(self) -> None:
        """Обнулить накопленную стоимость (перед обработкой новой закупки)."""
        self._cost_usd = 0.0

    def metrics(self) -> dict[str, Any]:
        """Сырые агрегаты эмбеддингов: стоимость/токены/латенси/число вызовов.

        Общее ``duration_ms`` стадии вычисляет вызывающая сторона (Scorer/RagAnalyzer),
        поэтому здесь возвращаются только части без ``duration_ms``.
        """
        return {
            "usd": round(self._cost_usd, 8),
            "usage": dict(self._usage),
            "cost_details": dict(self._cost_details),
            "models": [self._model],
            "calls": self._calls,
            "latency_ms": round(self._latency_ms, 3),
        }

    def reset_metrics(self) -> None:
        """Обнулить метрики (для независимого сбора по одной закупке)."""
        self._cost_usd = 0.0
        self._usage = {}
        self._cost_details = {}
        self._calls = 0
        self._latency_ms = 0.0

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Векторные представления списка текстов (длинные — усреднённые по чанкам)."""
        self._raise_if_cooldown()
        try:
            return [self._embed_one(text) for text in texts]
        except (httpx.TransportError, GigaTokenError):
            # Сетевой/auth-сбой: входим в кулдаун, чтобы повторные вызовы падали
            # быстро (best-effort), а не ждали таймаут на каждое задание.
            self._cooldown_until = time.time() + self._failure_cooldown_seconds
            raise

    def _raise_if_cooldown(self) -> None:
        if time.time() < self._cooldown_until:
            raise httpx.ConnectError(
                "Giga embeddings временно недоступны (cooldown)",
                request=httpx.Request("POST", self._base),
            )

    def _embed_one(self, text: str) -> list[float]:
        chunks = self._chunks(text)
        if len(chunks) == 1:
            return self._embed_raw(chunks[0])
        vecs = [self._embed_raw(chunk) for chunk in chunks]
        return self._average(vecs)

    def _chunks(self, text: str) -> list[str]:
        """Разбить текст на чанки не длиннее MAX_CHARS_PER_CHUNK (по границам абзацев)."""
        text = text.strip()
        if not text:
            return [""]
        if len(text) <= self.MAX_CHARS_PER_CHUNK:
            return [text]
        paragraphs = [p for p in text.split("\n") if p.strip()]
        chunks: list[str] = []
        buf = ""
        for para in paragraphs:
            if len(buf) + len(para) + 1 <= self.MAX_CHARS_PER_CHUNK:
                buf = f"{buf}\n{para}" if buf else para
            else:
                if buf:
                    chunks.append(buf)
                # Очень длинный абзац — режем по словам.
                buf = para
                while len(buf) > self.MAX_CHARS_PER_CHUNK:
                    cut = buf.rfind(" ", 0, self.MAX_CHARS_PER_CHUNK)
                    if cut <= 0:
                        cut = self.MAX_CHARS_PER_CHUNK
                    chunks.append(buf[:cut])
                    buf = buf[cut:].lstrip()
        if buf:
            chunks.append(buf)
        return chunks

    @staticmethod
    def _average(vecs: list[list[float]]) -> list[float]:
        if not vecs:
            return []
        n = len(vecs)
        dim = len(vecs[0])
        avg = [0.0] * dim
        for v in vecs:
            for i, val in enumerate(v):
                avg[i] += val
        return [x / n for x in avg]

    def _embed_raw(self, text: str) -> list[float]:
        payload: Mapping[str, object] = {"model": self._model, "input": text}
        try:
            return self._embed_once(payload)[0]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                # Токен всё же протух — принудительно обновляем и повторяем.
                self._tokens.reset()
                return self._embed_once(payload)[0]
            raise

    def _embed_once(self, payload: Mapping[str, object]) -> list[list[float]]:
        obs = start_observation(
            name="embeddings",
            as_type="embedding",
            input=payload.get("input"),
            metadata={"model": payload.get("model")},
        )
        try:
            start = time.perf_counter()
            resp = self._client.post(
                f"{self._base}/embeddings",
                headers={
                    "Authorization": f"Bearer {self._tokens.get_token()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            self._latency_ms += (time.perf_counter() - start) * 1000.0
            vectors = [item["embedding"] for item in data["data"]]
            raw_input = payload.get("input")
            if isinstance(raw_input, list):
                texts = [t for t in raw_input if isinstance(t, str)]
            else:
                texts = [raw_input] if isinstance(raw_input, str) else []
            input_tokens = embedding_input_tokens(data, texts)
            embed_usd = embedding_cost_usd(input_tokens)
            self._cost_usd += embed_usd
            self._calls += 1
            self._usage["input"] = int(self._usage.get("input") or 0) + input_tokens
            self._cost_details["input"] = round(
                (self._cost_details.get("input") or 0.0) + embed_usd, 8
            )
            obs.update(
                output=vectors,
                usage_details={"input": input_tokens},
                cost_details={"input": embed_usd},
            )
            obs.end()
            return vectors
        except Exception as exc:  # noqa: BLE001
            obs.update(level="WARNING", status_message=f"сбой эмбеддингов Giga: {exc}")
            obs.end()
            raise
