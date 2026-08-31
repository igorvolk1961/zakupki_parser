"""Эндпоинты справочных таблиц (вкладка «Справочники», роль analyst): CRUD.

Реестр ``REFERENCE_TABLES`` описывает таблицу (модель, схема валидации, колонки
для редактора). Новая справочная таблица подключается добавлением одной записи
в реестр — роутер и репозиторий не меняются.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError

from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import (
    ConfirmationTypeIn,
    LicenseTypeIn,
    ReferenceColumnOut,
    ReferenceRowIn,
    ReferenceRowsOut,
    ReferenceTableOut,
)
from zakupki_parser.storage.db import ExperienceConfirmationType, LicenseType
from zakupki_parser.storage.repository import ProcurementRepository
from zakupki_parser.storage.repository.profiles import license_type_names

logger = logging.getLogger(__name__)

# Предустановленные записи справочников (сид BR-03 / docs/references/licenze_kind.md):
# на них завязан analysis_service (матчер сверяет названия лицензий и коды опыта с
# хардкод-набором), поэтому их ключевое поле (name|code) переименовывать нельзя
# (иначе после ре-сида появится дубликат).
SEED_CONFIRMATION_CODES = {code for code, _ in ProcurementRepository.CONFIRMATION_TYPES_SEED}
SEED_LICENSE_NAMES = set(license_type_names())


class _ReferenceTable:
    """Описание справочной таблицы для редактора."""

    __slots__ = ("key", "title", "model", "schema", "columns", "seed_attr", "seed_keys")

    def __init__(
        self,
        key: str,
        title: str,
        model: type[Any],
        schema: type[BaseModel],
        columns: list[ReferenceColumnOut],
        seed_attr: str = "",
        seed_keys: set[str] | None = None,
    ) -> None:
        self.key = key
        self.title = title
        self.model = model
        self.schema = schema
        self.columns = columns
        self.seed_attr = seed_attr
        self.seed_keys = seed_keys or set()


REFERENCE_TABLES: dict[str, _ReferenceTable] = {
    table.key: table
    for table in [
        _ReferenceTable(
            key="license_types",
            title="Виды лицензий",
            model=LicenseType,
            schema=LicenseTypeIn,
            columns=[
                ReferenceColumnOut(key="name", label="Наименование", type="text"),
                ReferenceColumnOut(key="sort_order", label="Порядок", type="integer"),
            ],
            seed_attr="name",
            seed_keys=SEED_LICENSE_NAMES,
        ),
        _ReferenceTable(
            key="experience_confirmation_types",
            title="Типы подтверждения опыта",
            model=ExperienceConfirmationType,
            schema=ConfirmationTypeIn,
            columns=[
                ReferenceColumnOut(key="code", label="Код", type="text"),
                ReferenceColumnOut(key="name", label="Наименование", type="text"),
                ReferenceColumnOut(key="sort_order", label="Порядок", type="integer"),
            ],
            seed_attr="code",
            seed_keys=SEED_CONFIRMATION_CODES,
        ),
    ]
}


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Все колонки строки справочника (id, поля редактора, created_at/updated_at)."""
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _resolve_table(table: str) -> _ReferenceTable:
    cfg = REFERENCE_TABLES.get(table)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Неизвестная справочная таблица")
    return cfg


def _validate_body(cfg: _ReferenceTable, body: ReferenceRowIn) -> dict[str, Any]:
    """Валидирует строку по схеме таблицы (лишние ключи игнорируются)."""
    try:
        return cfg.schema.model_validate(body.model_dump()).model_dump()
    except ValidationError as exc:
        msgs = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors())
        raise HTTPException(status_code=422, detail=msgs or "Некорректные данные") from exc


def build_reference_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _repo = ctx._repo
    require_analyst = ctx.require_analyst

    @router.get(
        "/api/reference",
        response_model=list[ReferenceTableOut],
        dependencies=[Depends(require_analyst)],
    )
    async def list_tables() -> list[ReferenceTableOut]:
        """Список справочных таблиц (для переключателя на странице)."""
        return [
            ReferenceTableOut(key=t.key, title=t.title, columns=t.columns)
            for t in REFERENCE_TABLES.values()
        ]

    @router.get(
        "/api/reference/{table}",
        response_model=ReferenceRowsOut,
        dependencies=[Depends(require_analyst)],
    )
    async def list_rows(table: str) -> ReferenceRowsOut:
        cfg = _resolve_table(table)
        rows = await _repo().list_reference_rows(cfg.model)
        return ReferenceRowsOut(total=len(rows), items=[_row_to_dict(r) for r in rows])

    @router.post(
        "/api/reference/{table}",
        response_model=dict[str, Any],
        status_code=201,
        dependencies=[Depends(require_analyst)],
    )
    async def create_row(table: str, body: ReferenceRowIn) -> dict[str, Any]:
        cfg = _resolve_table(table)
        data = _validate_body(cfg, body)
        try:
            row = await _repo().create_reference_row(cfg.model, data)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="Запись с таким ключевым полем уже существует"
            ) from exc
        return _row_to_dict(row)

    @router.put(
        "/api/reference/{table}/{row_id}",
        response_model=dict[str, Any],
        dependencies=[Depends(require_analyst)],
    )
    async def update_row(table: str, row_id: int, body: ReferenceRowIn) -> dict[str, Any]:
        cfg = _resolve_table(table)
        existing = await _repo().get_reference_row(cfg.model, row_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        data = _validate_body(cfg, body)
        # Предустановленные записи (сид BR-03 / каталожные виды) защищены от
        # переименования ключевого поля: его использует матчер analysis_service и ре-сид.
        if (
            cfg.seed_attr
            and getattr(existing, cfg.seed_attr) in cfg.seed_keys
            and data.get(cfg.seed_attr) != getattr(existing, cfg.seed_attr)
        ):
            raise HTTPException(
                status_code=409,
                detail="Изменять ключевое поле предустановленной записи нельзя",
            )
        try:
            row = await _repo().update_reference_row(cfg.model, row_id, data)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="Запись с таким ключевым полем уже существует"
            ) from exc
        return _row_to_dict(row)

    @router.delete(
        "/api/reference/{table}/{row_id}",
        status_code=204,
        dependencies=[Depends(require_analyst)],
    )
    async def delete_row(table: str, row_id: int) -> None:
        cfg = _resolve_table(table)
        try:
            deleted = await _repo().delete_reference_row(cfg.model, row_id)
        except IntegrityError as exc:
            # FK RESTRICT: тип используется лицензиями/опытом профилей.
            raise HTTPException(
                status_code=409,
                detail="Запись используется в профилях и не может быть удалена",
            ) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Запись не найдена")

    return router
