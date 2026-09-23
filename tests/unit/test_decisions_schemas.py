"""Unit-тесты схем Эпика 5 (отбраковка и «в работе»): дефолты и сериализация."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zakupki_parser.api.app.schemas import (
    ClearDbIn,
    ProcurementByUrlIn,
    ProcurementOut,
    RejectIn,
)


def test_reject_in_defaults() -> None:
    body = RejectIn()
    assert body.rejection_reason is None
    assert body.remove_matched_keywords is False
    assert body.exclusion_word is None

    body = RejectIn(rejection_reason="не наш профиль", remove_matched_keywords=True)
    assert body.rejection_reason == "не наш профиль"
    assert body.remove_matched_keywords is True


def test_clear_db_keeps_in_work_by_default() -> None:
    # По умолчанию очистка БД НЕ удаляет закупки «в работе» — только по явному флагу.
    assert ClearDbIn().include_in_work is False
    assert ClearDbIn(include_in_work=True).include_in_work is True


def test_procurement_by_url_requires_url() -> None:
    assert ProcurementByUrlIn(url="https://etp.example.com/need/1").url
    with pytest.raises(ValidationError):
        ProcurementByUrlIn(url="")


def test_procurement_out_in_work_default() -> None:
    assert ProcurementOut.model_fields["in_work"].default is False
