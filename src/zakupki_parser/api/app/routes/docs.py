"""Эндпоинты UI-документации: руководства по ролям («Документация»).

Руководства ролей ``user``/``admin``/``analyst``/``devops`` лежат в
``docs/guides/<role>.md`` (поиск вверх по дереву репозитория — dev-каталог).

Доступ:
- ``user`` — открыто и гостю до входа (публичный ``/api/docs/user-guide`` —
  алиас роли user);
- ``admin``/``analyst``/``devops`` — только пользователю с соответствующей
  ролью; пункт руководства показывается в меню «Документация» по ролям
  вошедшего пользователя.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from zakupki_parser.api.app.deps import ApiContext

_GUIDES_DIR = Path("docs") / "guides"


def _guide_path(role: str) -> Path | None:
    for parent in Path(__file__).resolve().parents:
        cand = parent / _GUIDES_DIR / f"{role}.md"
        if cand.is_file():
            return cand
    return None


def _serve(role: str) -> PlainTextResponse:
    path = _guide_path(role)
    if path is None:
        raise HTTPException(status_code=404, detail="Руководство не найдено")
    return PlainTextResponse(
        path.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
    )


def build_docs_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/docs/user-guide", include_in_schema=False)
    @router.get("/api/docs/guide/user", include_in_schema=False)
    async def user_guide() -> PlainTextResponse:
        """Руководство роли «Пользователь» (доступно и гостю до входа)."""
        return _serve("user")

    @router.get(
        "/api/docs/guide/admin",
        include_in_schema=False,
        dependencies=[Depends(ctx.require_admin)],
    )
    async def admin_guide() -> PlainTextResponse:
        """Руководство роли «Администратор»."""
        return _serve("admin")

    @router.get(
        "/api/docs/guide/analyst",
        include_in_schema=False,
        dependencies=[Depends(ctx.require_analyst)],
    )
    async def analyst_guide() -> PlainTextResponse:
        """Руководство роли «Аналитик»."""
        return _serve("analyst")

    @router.get(
        "/api/docs/guide/devops",
        include_in_schema=False,
        dependencies=[Depends(ctx.require_devops)],
    )
    async def devops_guide() -> PlainTextResponse:
        """Руководство роли «DevOps»."""
        return _serve("devops")

    return router
