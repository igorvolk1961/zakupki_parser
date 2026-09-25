"""Помощь при настройке условий отчётных полей (редактор поля профиля).

- ``POST /api/conditions/stem`` — подсказка: слово -> шаблон со звёздочкой
  (``утилизация`` -> ``утилизац*``), пользователь может его поправить.
- ``POST /api/conditions/test`` — «Проверить»: условие на примере значений
  (как будто их нашли в ТЗ) — итог по каждому значению, для сайта — сколько
  раз значение найдено, его строка-окно и какие уточнения/метки совпали.
  Ничего не сохраняет.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from scoring_common.conditions import (
    ConditionError,
    evaluate_condition,
    normalize_condition,
    stem_pattern,
)
from scoring_common.sources.matching import source_contexts, value_windows
from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.source_links import with_source_meta


class StemIn(BaseModel):
    words: list[str] = Field(default_factory=list, max_length=50)


class StemOut(BaseModel):
    word: str
    pattern: str


class ConditionTestIn(BaseModel):
    field_type: str = "string"
    value_mode: str = "auto"
    condition: dict[str, Any]
    # Значения, как будто найденные в ТЗ (для списка — несколько).
    values: list[str] = Field(default_factory=list, max_length=200)
    # Уточнения «рядом» для условия с source=field (значения соседнего поля из ТЗ).
    qualifiers: list[str] = Field(default_factory=list, max_length=50)


class ConditionTestOut(BaseModel):
    match: bool | None
    check_status: str
    mismatched_values: list[str] = Field(default_factory=list)
    mismatch_reasons: dict[str, str] = Field(default_factory=dict)
    requirements: dict[str, list[str]] = Field(default_factory=dict)
    near_matches: dict[str, list[str]] = Field(default_factory=dict)
    near_labels: dict[str, str | None] = Field(default_factory=dict)
    # Для сайта: {значение: {occurrences, window}} и состояние текста сайта.
    on_site: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source: dict[str, Any] | None = None


def build_conditions_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    require_base = ctx.require_base

    @router.post(
        "/api/conditions/stem",
        response_model=list[StemOut],
        dependencies=[Depends(require_base)],
    )
    async def stem(body: StemIn) -> list[StemOut]:
        """Подсказка основы: окончание заменено звёздочкой."""
        return [StemOut(word=w, pattern=stem_pattern(w)) for w in body.words if w.strip()]

    @router.post(
        "/api/conditions/test",
        response_model=ConditionTestOut,
        dependencies=[Depends(require_base)],
    )
    async def test_condition(body: ConditionTestIn) -> ConditionTestOut:
        """Проверка условия на примере значений (как при анализе закупки)."""
        try:
            condition = normalize_condition(body.condition, body.field_type, body.value_mode)
        except ConditionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if condition is None:
            raise HTTPException(status_code=400, detail="Условие не задано")
        values = [v.strip() for v in body.values if v.strip()]
        if not values:
            raise HTTPException(status_code=400, detail="Введите хотя бы одно значение")
        near = condition.get("near") or {}
        qualifiers = (
            list(near.get("words") or [])
            if near.get("source") == "words"
            else [q.strip() for q in body.qualifiers if q.strip()]
        )
        source = None
        source_info: dict[str, Any] | None = None
        if condition.get("value_kind") == "url":
            [with_meta] = await with_source_meta(state, [{"condition": condition}])
            source_info = with_meta["condition"]["source"]
            contexts = await asyncio.to_thread(source_contexts, [with_meta])
            source = next(iter(contexts.values()), None)
        value: Any = values if body.field_type == "list" else values[0]
        outcome = evaluate_condition(
            condition,
            body.field_type,
            value,
            True,
            value_mode=body.value_mode,
            source=source,
            qualifiers=qualifiers,
        )
        on_site: dict[str, dict[str, Any]] = {}
        if source is not None and source.windows is not None:
            on_site = await asyncio.to_thread(
                value_windows,
                source.windows,
                values if body.field_type == "list" else values[:1],
                body.value_mode,
                window=int(near.get("window", 300)),
                max_window=int(near.get("max_window", 2000)),
            )
        return ConditionTestOut(
            match=outcome.match,
            check_status=outcome.check_status,
            mismatched_values=outcome.mismatched_values,
            mismatch_reasons=outcome.mismatch_reasons,
            requirements=outcome.requirements,
            near_matches=outcome.near_matches,
            near_labels=outcome.near_labels,
            on_site=on_site,
            source=source_info,
        )

    return router
