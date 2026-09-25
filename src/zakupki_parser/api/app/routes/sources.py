"""Сайты-источники: текст сайта по всем страницам пагинации (``zakupki_parser.sources``).

Источник общий для всех пользователей (текст публичного сайта, один URL —
одна запись): его может запросить любой аутентифицированный пользователь,
а внутренний вызов конвейера (analysis_service) — прочитать.

Сбор — фоновая задача: ``POST /api/sources`` возвращает сразу, ход сбора —
в ``GET /api/sources/{id}`` (``progress``: страниц, символов, текущий URL,
способ перехода, сколько секунд идёт).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from scoring_common.conditions import find_value, normalize_text
from scoring_common.sources.store import get_text, text_key
from scoring_common.sources.urls import normalize_source_url
from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.net_safety import UnsafeUrlError
from zakupki_parser.storage.db import SiteSource

_SNIPPET = 150


class SourceIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class SourceOut(BaseModel):
    id: int
    url: str
    status: str
    stop_reason: str | None = None
    # Итог последнего сбора; текст в хранилище — от последнего сбора, который
    # его записал (``fetched_at``/``text_complete``).
    pages: int = 0
    text_chars: int = 0
    text_complete: bool = False
    fetched_at: datetime | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # Сбор идёт прямо сейчас (или ждёт очереди) — фронт опрашивает статус.
    active: bool = False


class SourceTextOut(BaseModel):
    source_id: int
    total_chars: int
    query: str | None = None
    matches: int = 0
    fragments: list[str] = Field(default_factory=list)


def build_sources_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    _repo = ctx._repo
    require_base = ctx.require_base
    require_user_or_internal = ctx.require_user_or_internal

    def _manager() -> Any:
        manager = state.source_crawls
        if manager is None:
            raise HTTPException(status_code=503, detail="Сбор сайтов недоступен (нет БД)")
        return manager

    def _out(source: SiteSource) -> SourceOut:
        return SourceOut(
            id=source.id,
            url=source.url,
            status=source.status,
            stop_reason=source.stop_reason,
            pages=source.pages,
            text_chars=source.text_chars,
            text_complete=source.text_complete,
            fetched_at=source.fetched_at,
            progress=dict(source.progress or {}),
            error=source.error,
            started_at=source.started_at,
            finished_at=source.finished_at,
            active=_manager().is_active(source.id),
        )

    async def _source(source_id: int) -> SiteSource:
        source: SiteSource | None = await _repo().get_site_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Источник не найден")
        return source

    @router.post("/api/sources", response_model=SourceOut, dependencies=[Depends(require_base)])
    async def ensure_source(body: SourceIn) -> SourceOut:
        """Источник по URL: создаётся, и сбор ставится, если текста нет или он устарел."""
        try:
            source = await _manager().ensure(body.url)
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _out(source)

    @router.get(
        "/api/sources/lookup",
        response_model=SourceOut,
        dependencies=[Depends(require_base)],
    )
    async def lookup_source(url: str = Query(min_length=1, max_length=2048)) -> SourceOut:
        """Статус сайта по URL без запуска сбора (404 — ещё не собирался)."""
        source = await _repo().get_site_source_by_url(normalize_source_url(url))
        if source is None:
            raise HTTPException(status_code=404, detail="Сайт ещё не собирался")
        return _out(source)

    @router.get(
        "/api/sources/{source_id}",
        response_model=SourceOut,
        dependencies=[Depends(require_user_or_internal)],
    )
    async def get_source(source_id: int) -> SourceOut:
        return _out(await _source(source_id))

    @router.post(
        "/api/sources/{source_id}/refresh",
        response_model=SourceOut,
        dependencies=[Depends(require_base)],
    )
    async def refresh_source(source_id: int) -> SourceOut:
        """Пересобрать сейчас (если уже не собирается)."""
        await _source(source_id)
        source = await _manager().refresh(source_id)
        return _out(source or await _source(source_id))

    @router.post(
        "/api/sources/{source_id}/cancel",
        response_model=SourceOut,
        dependencies=[Depends(require_base)],
    )
    async def cancel_source(source_id: int) -> SourceOut:
        """Остановить сбор — собранное сохранится как неполное."""
        await _source(source_id)
        await _manager().cancel(source_id)
        return _out(await _source(source_id))

    @router.get(
        "/api/sources/{source_id}/text",
        response_model=SourceTextOut,
        dependencies=[Depends(require_user_or_internal)],
    )
    async def source_text(
        source_id: int,
        q: str | None = Query(default=None, max_length=500),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> SourceTextOut:
        """Фрагменты текста сайта с найденным значением ``q`` (поиск как в
        условиях полей: код — по цифрам, текст — по основам слов); без ``q`` —
        начало текста. Весь текст не отдаётся (до десятков МБ)."""
        source = await _source(source_id)
        text = await asyncio.to_thread(get_text, text_key(source.url_norm))
        if text is None:
            raise HTTPException(status_code=404, detail="Текст сайта ещё не собран")
        if not q:
            return SourceTextOut(
                source_id=source_id, total_chars=len(text), fragments=[text[:2000]]
            )
        spans = find_value(normalize_text(text), q)
        fragments = [
            text[max(0, s - _SNIPPET) : min(len(text), e + _SNIPPET)] for s, e in spans[:limit]
        ]
        return SourceTextOut(
            source_id=source_id,
            total_chars=len(text),
            query=q,
            matches=len(spans),
            fragments=fragments,
        )

    return router
