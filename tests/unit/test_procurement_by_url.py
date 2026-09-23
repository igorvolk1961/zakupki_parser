"""Unit-тесты распознавания площадки по URL (US-5.5/FR-5.5, добавление закупки
«в работу» по URL — живая подгрузка карточки)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from zakupki_parser.api.app.routes.procurements import (
    _match_platform_ids_by_url,
    fetch_procurement_by_url,
)


@dataclass
class _FakePlatform:
    url: str
    name: str = "Площадка"


PLATFORMS = {
    "zakupki_mos": _FakePlatform(url="https://zakupki.mos.ru", name="Портал поставщиков Москвы"),
    "roseltorg_44fz": _FakePlatform(url="https://www.roseltorg.ru", name="РТС-тендер 44-ФЗ"),
    "roseltorg_223fz": _FakePlatform(url="https://www.roseltorg.ru", name="РТС-тендер 223-ФЗ"),
}


def test_match_platform_ids_by_url_exact_host() -> None:
    assert _match_platform_ids_by_url(PLATFORMS, "https://zakupki.mos.ru/need/123") == [
        "zakupki_mos"
    ]


def test_match_platform_ids_by_url_case_insensitive() -> None:
    assert _match_platform_ids_by_url(PLATFORMS, "HTTPS://ZAKUPKI.MOS.RU/need/123") == [
        "zakupki_mos"
    ]


def test_match_platform_ids_by_url_multiple_platforms_share_host() -> None:
    # roseltorg_44fz и roseltorg_223fz — один портал, два раздела (общий хост).
    matched = _match_platform_ids_by_url(PLATFORMS, "https://www.roseltorg.ru/tender/1")
    assert set(matched) == {"roseltorg_44fz", "roseltorg_223fz"}


def test_match_platform_ids_by_url_no_match() -> None:
    assert _match_platform_ids_by_url(PLATFORMS, "https://unknown-etp.example.com/lot/1") == []


def test_match_platform_ids_by_url_empty_or_garbage() -> None:
    assert _match_platform_ids_by_url(PLATFORMS, "") == []
    assert _match_platform_ids_by_url(PLATFORMS, "не url вовсе") == []


@pytest.mark.asyncio
async def test_fetch_procurement_by_url_not_implemented_stub() -> None:
    # Фаза 1 (заглушка): площадка распознана, но живая подгрузка карточки
    # для неё ещё не реализована — честная ошибка, а не тихий частичный результат.
    class _FakeCfg:
        dom = type("D", (), {"platforms": PLATFORMS})()

    class _FakeState:
        cfg = _FakeCfg()

    with pytest.raises(NotImplementedError, match="zakupki_mos|Портал поставщиков Москвы"):
        await fetch_procurement_by_url(
            _FakeState(), ["zakupki_mos"], "https://zakupki.mos.ru/need/1"
        )
