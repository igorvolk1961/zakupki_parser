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


def embedding_similarity(
    embedder: GigaEmbedder,
    competencies: str,
    description: str,
    competencies_cache: dict[str, list[float]] | None = None,
) -> float:
    """Близость (0..1) текстов компетенций и описания закупки.

    Компетенции одинаковы для всех закупок одного прогона, поэтому их эмбеддинг
    вычисляется один раз и переиспользуется: ``competencies_cache`` (ключ — текст
    компетенций) заполняется на первом вызове, при повторных вызовах с тем же
    текстом считается только вектор описания. Без кэша поведение прежнее — оба
    вектора вычисляются каждый вызов.
    """
    if competencies_cache is not None:
        comp_emb = competencies_cache.get(competencies)
        if comp_emb is not None:
            desc_emb = embedder.embed([description])[0]
            return round(_cosine(comp_emb, desc_emb), 4)
    vecs = embedder.embed([competencies, description])
    comp_emb, desc_emb = vecs[0], vecs[1]
    if competencies_cache is not None:
        competencies_cache[competencies] = comp_emb
    return round(_cosine(comp_emb, desc_emb), 4)
