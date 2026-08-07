"""FastAPI-приложение сервиса скоринга.

- ``GET /health`` — статус сервиса;
- ``POST /score`` — синхронный скоринг по карточке + компетенциям
  (для тестов, eval и ручных вызовов). Асинхронный путь — через Redis-очередь.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from scoring_service.schemas import ScoringOutput
from scoring_service.scoring import Scorer, build_scorer
from scoring_service.settings import Settings, get_settings


def _auth_dependency(settings: Settings) -> Callable[[str | None], None]:
    """FastAPI-зависимость опциональной авторизации по Bearer-токену.

    Если ``auth_token`` не задан — доступ открыт (dev). Иначе требует
    ``Authorization: Bearer <token>``.
    """

    def _require_authorization(
        authorization: str | None = Header(default=None),
    ) -> None:
        if not settings.auth_token:
            return
        expected = f"Bearer {settings.auth_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")

    return _require_authorization


class ScoreRequest(BaseModel):
    """Синхронный запрос скоринга."""

    model_config = ConfigDict(extra="allow")

    record: dict[str, Any]
    competencies: str | None = None
    procurement_id: int | None = None


class ScoreResponse(BaseModel):
    result: ScoringOutput


class HealthOut(BaseModel):
    status: str


def _build_scorer(settings: Settings) -> Scorer:
    return build_scorer(settings)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    scorer = _build_scorer(settings)
    auth = _auth_dependency(settings)

    app = FastAPI(title="Zakupki Scoring Service", version="0.1.0")

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        return HealthOut(status="ok")

    @app.post("/score", response_model=ScoreResponse, dependencies=[Depends(auth)])
    async def score(body: ScoreRequest) -> ScoreResponse:
        competencies = body.competencies or settings.competencies()
        try:
            result = scorer.score(body.record, competencies, body.procurement_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ScoreResponse(result=result)

    return app
