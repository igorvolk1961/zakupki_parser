"""Эндпоинты заказчиков: справочник и рейтинг (ADR-4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import CustomerListOut, CustomerOut, RatingUpdate


def build_customers_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _repo = ctx._repo
    require_base = ctx.require_base
    require_internal = ctx.require_internal

    @router.get(
        "/api/customers",
        response_model=CustomerListOut,
        dependencies=[Depends(require_base)],
    )
    async def list_customers(
        name: str | None = None,
        inn: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> CustomerListOut:
        rows, total = await _repo().list_customers(name=name, inn=inn, limit=limit, offset=offset)
        return CustomerListOut(total=total, items=[CustomerOut.model_validate(r) for r in rows])

    @router.get(
        "/api/customers/{customer_id}",
        response_model=CustomerOut,
        dependencies=[Depends(require_base)],
    )
    async def get_customer(customer_id: int) -> CustomerOut:
        row = await _repo().get_customer(customer_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Заказчик не найден")
        return CustomerOut.model_validate(row)

    @router.post(
        "/api/customers/{customer_id}/rating",
        response_model=CustomerOut,
        dependencies=[Depends(require_internal)],
    )
    async def set_customer_rating(customer_id: int, body: RatingUpdate) -> CustomerOut:
        """Установка рейтинга заказчика внешним сервисом (ADR-4)."""
        if not await _repo().set_customer_rating(customer_id, body.rating):
            raise HTTPException(status_code=404, detail="Заказчик не найден")
        row = await _repo().get_customer(customer_id)
        return CustomerOut.model_validate(row)

    return router
