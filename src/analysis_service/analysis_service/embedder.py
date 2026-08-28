"""Фабрика клиента эмбеддингов для RAG-анализа.

Приоритет: прямой Giga Embedder (``ANALYSIS_GIGA_CLIENT_ID``/``SECRET`` заданы) —
те же модель и ключи, что и у scoring_service. Иначе — фолбэк на
OpenAI-совместимый endpoint ``/embeddings`` (gpt2giga-прокси).
"""

from __future__ import annotations

import logging

from analysis_service.settings import Settings
from scoring_common.embeddings import Embeddable, EmbeddingClient, GigaEmbeddingClient

logger = logging.getLogger(__name__)


def build_embedder(settings: Settings) -> Embeddable:
    """Клиент эмбеддингов: Giga напрямую (если задан ключ) или gpt2giga-прокси."""
    if settings.giga_enabled and settings.giga_configured:
        logger.info("Эмбеддинги: прямой Giga Embedder (%s)", settings.giga_embeddings_model)
        return GigaEmbeddingClient(
            base_url=settings.giga_base_url,
            model=settings.giga_embeddings_model,
            auth_url=settings.giga_auth_url,
            client_id=settings.giga_client_id,
            client_secret=settings.giga_client_secret,
            scope=settings.giga_auth_scope,
            timeout=settings.giga_timeout_seconds,
            min_token_ttl_seconds=settings.giga_min_token_ttl_seconds,
            verify_ssl=settings.giga_verify_ssl,
        )
    if settings.giga_enabled:
        logger.warning(
            "Ключ доступа Giga не задан (ANALYSIS_GIGA_CLIENT_ID/SECRET) — "
            "фолбэк на OpenAI-совместимый endpoint /embeddings"
        )
    return EmbeddingClient(
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        timeout=settings.embedding_timeout,
    )
