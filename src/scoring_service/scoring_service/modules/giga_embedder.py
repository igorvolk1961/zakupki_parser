"""Клиент Giga Embedder (Sber GigaChat): эмбеддинги + автообновление OAuth-токена.

- ``GigaTokenProvider``: получает ``access_token`` по OAuth 2.0 (client_credentials)
  и автоматически обновляет его при истечении ``expires_in``.
- ``GigaEmbedder``: POST /embeddings с Bearer-токеном; при 401 принудительно
  сбрасывает и получает свежий токен, затем повторяет запрос.
"""

from __future__ import annotations

import threading
import time
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
        """Векторные представления списка текстов."""
        payload = {"model": self._model, "input": texts}
        try:
            return self._embed_once(payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                # Токен всё же протух — принудительно обновляем и повторяем.
                self._tokens.reset()
                return self._embed_once(payload)
            raise

    def _embed_once(self, payload: Mapping[str, object]) -> list[list[float]]:
        with httpx.Client(timeout=30.0, verify=self._verify_ssl) as client:
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
