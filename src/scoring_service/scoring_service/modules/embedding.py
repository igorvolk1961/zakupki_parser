"""Параллельная ветка: косинусная близость эмбеддингов компетенций и описания закупки."""

from __future__ import annotations

import math

from scoring_service.modules.giga_embedder import GigaEmbedder


def _cosine(a: list[float], b: list[float]) -> float:
    """Косинусная близость двух векторов (0..1)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def embedding_similarity(embedder: GigaEmbedder, competencies: str, description: str) -> float:
    """Близость (0..1) текстов компетенций и описания закупки."""
    vecs = embedder.embed([competencies, description])
    return round(_cosine(vecs[0], vecs[1]), 4)
