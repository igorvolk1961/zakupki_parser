"""Клиент Giga Embedder (Sber GigaChat): эмбеддинги + автообновление OAuth-токена.

- ``GigaTokenProvider``: получает ``access_token`` по OAuth 2.0 (client_credentials)
  и автоматически обновляет его при истечении ``expires_in``.
- ``GigaEmbedder``: POST /embeddings с Bearer-токеном; при 401 принудительно
  сбрасывает и получает свежий токен, затем повторяет запрос.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Mapping

import httpx


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
    ) -> None:
        self._auth_url = auth_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._min_ttl = min_ttl_seconds
        self._verify_ssl = verify_ssl
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> tuple[str, float]:
        with httpx.Client(timeout=30.0, verify=self._verify_ssl) as client:
            resp = client.post(
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
        """Возвращает действующий токен (обновляет при необходимости)."""
        with self._lock:
            now = time.time()
            if self._token is None or (self._expires_at - now) < self._min_ttl:
                self._token, self._expires_at = self._fetch()
            return self._token

    def reset(self) -> None:
        """Принудительно сбросить кэш токена (для повторного получения)."""
        with self._lock:
            self._token = None
            self._expires_at = 0.0


class GigaEmbedder:
    """Эмбеддинги текста через GigaChat (best-effort: не роняет скоринг)."""

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
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._tokens = token_provider
        self._verify_ssl = verify_ssl

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Векторные представления списка текстов (длинные — усреднённые по чанкам)."""
        return [self._embed_one(text) for text in texts]

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
        with httpx.Client(timeout=60.0, verify=self._verify_ssl) as client:
            resp = client.post(
                f"{self._base}/embeddings",
                headers={
                    "Authorization": f"Bearer {self._tokens.get_token()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return [item["embedding"] for item in data["data"]]
