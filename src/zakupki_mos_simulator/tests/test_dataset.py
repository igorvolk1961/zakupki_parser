"""Тесты датасета: модели, 5 категорий, балансировка, генерация."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from zakupki_mos_simulator.data.dataset import balance_report
from zakupki_mos_simulator.data.models import CATEGORIES, Procurement
from zakupki_mos_simulator.llm.generate import (
    build_customers,
    build_demo_dataset,
    read_competencies,
)


def test_categories_cover_five() -> None:
    assert set(CATEGORIES) == {
        "perfect",
        "synonym",
        "close",
        "far",
        "false_friend",
    }


def test_procurement_validates_category() -> None:
    with pytest.raises(ValidationError):
        Procurement(
            id=1,
            number="1",
            subject="x",
            customer="c",
            customer_id=1,
            nmck=100.0,
            publication_date="с 01.01.2026 до 10.01.2026 12:00 (МСК)",
            category="unknown",
        )


def test_demo_dataset_balanced() -> None:
    ds = build_demo_dataset(
        competencies="компетенции",
        okpd2_sections=["62", "63"],
        per_category=4,
    )
    report = balance_report(ds)
    assert report["total"] == 5 * 4
    assert not report["violations"]
    for cat in CATEGORIES:
        assert report["counts"][cat] == 4


def test_assign_metadata_sets_dates_and_ids() -> None:
    ds = build_demo_dataset(
        competencies="компетенции",
        okpd2_sections=["62"],
        per_category=2,
    )
    first = ds.procurements[0]
    assert first.id != 0
    assert first.number == str(first.id)
    assert "(МСК)" in first.publication_date
    assert first.customer_id != 0
    assert all(f.url.startswith("/api/FileStorage/Download") for f in first.files)


def test_build_customers_dedupes() -> None:
    ds = build_demo_dataset(
        competencies="компетенции",
        okpd2_sections=["62"],
        per_category=2,
    )
    customers = build_customers(ds.procurements)
    names = [c.name for c in customers]
    assert len(names) == len(set(names))


def test_read_competencies(tmp_path: Path) -> None:
    p = tmp_path / "comp.md"
    p.write_text("   компетенции поставщика  ", encoding="utf-8")
    assert read_competencies(p) == "компетенции поставщика"


def test_dataset_roundtrip(tmp_path: Path) -> None:
    from zakupki_mos_simulator.data.dataset import load_dataset, save_dataset

    ds = build_demo_dataset(
        competencies="компетенции",
        okpd2_sections=["62"],
        per_category=2,
    )
    out = tmp_path / "ds.json"
    save_dataset(ds, out)
    loaded = load_dataset(out)
    assert len(loaded.procurements) == len(ds.procurements)
    assert loaded.competencies == ds.competencies
