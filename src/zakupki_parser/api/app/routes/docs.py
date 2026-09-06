"""Публичные эндпоинты UI-страниц: руководство пользователя («Документация»).

Документация открыта без авторизации: пункт меню «Документация» доступен и
гостю (до входа), и авторизованному пользователю.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

# docs/user-guide.md ищем вверх по дереву репозитория (dev-каталог), как это
# делают другие модули для docs/references/* (см. law_requirements).
_USER_GUIDE = Path("docs") / "user-guide.md"


def _user_guide_path() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        cand = parent / _USER_GUIDE
        if cand.is_file():
            return cand
    return None


def build_docs_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/docs/user-guide", include_in_schema=False)
    async def user_guide() -> PlainTextResponse:
        """Руководство пользователя (тендеролога) в Markdown для страницы UI."""
        path = _user_guide_path()
        if path is None:
            raise HTTPException(status_code=404, detail="Руководство пользователя не найдено")
        return PlainTextResponse(
            path.read_text(encoding="utf-8"),
            media_type="text/markdown; charset=utf-8",
        )

    return router
