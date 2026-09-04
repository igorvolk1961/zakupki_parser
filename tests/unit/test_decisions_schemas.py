"""Unit-тесты схем Эпика 5 (отбраковка и «в работе»): дефолты и сериализация."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from zakupki_parser.api.app.schemas import (
    AcceptWorkByUrlIn,
    ClearDbIn,
    ProcurementOut,
    RejectIn,
    WorkItemOut,
)
from zakupki_parser.storage.db import ProcurementWorkItem


def test_reject_in_defaults() -> None:
    body = RejectIn()
    assert body.rejection_reason is None
    assert body.remove_matched_keywords is False
    assert body.exclusion_word is None

    body = RejectIn(rejection_reason="не наш профиль", remove_matched_keywords=True)
    assert body.rejection_reason == "не наш профиль"
    assert body.remove_matched_keywords is True


def test_clear_db_keeps_work_items_by_default() -> None:
    # По умолчанию очистка БД НЕ удаляет закупки «в работе» — только по явному флагу.
    assert ClearDbIn().include_work_items is False
    assert ClearDbIn(include_work_items=True).include_work_items is True


def test_accept_by_url_requires_url() -> None:
    assert AcceptWorkByUrlIn(url="https://etp.example.com/need/1").notes is None
    with pytest.raises(ValidationError):
        AcceptWorkByUrlIn(url="")


def test_work_item_out_from_snapshot() -> None:
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    item = ProcurementWorkItem(
        id=1,
        profile_id=10,
        procurement_id=None,
        source="url",
        status="in_work",
        notes="проверить",
        accepted_at=now,
        url="https://etp.example.com/purchase/999",
        created_at=now,
        updated_at=now,
    )
    out = WorkItemOut.model_validate(item)
    assert out.id == 1
    assert out.procurement_id is None
    assert out.source == "url"
    assert out.url == "https://etp.example.com/purchase/999"
    assert out.notes == "проверить"


def test_procurement_out_in_work_default() -> None:
    # in_work — динамический атрибут выдачи (по умолчанию False), как score.
    assert ProcurementOut.model_fields["in_work"].default is False
