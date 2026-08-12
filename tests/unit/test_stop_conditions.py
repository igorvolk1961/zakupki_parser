"""Unit-тесты условий прекращения обработки заявки."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from zakupki_parser.config.models import AppConfig
from zakupki_parser.parser.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_deadline_expired_skips(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None, deadline_not_expired=True)
    record = {"number": "1", "deadline": datetime(2026, 8, 1, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is True  # noqa: SLF001


@pytest.mark.asyncio
async def test_deadline_future_not_skipped(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None, deadline_not_expired=True)
    record = {"number": "2", "deadline": datetime(2026, 8, 10, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is False  # noqa: SLF001


def _make_orch(
    app_config: AppConfig,
    now: datetime,
    min_deadline_days: int | None,
    deadline_not_expired: bool = True,
) -> Orchestrator:
    cfg = app_config.model_copy(deep=True)
    cfg.service.stop_conditions.min_deadline_days = min_deadline_days
    cfg.service.stop_conditions.deadline_not_expired = deadline_not_expired
    return Orchestrator(
        cfg=cfg,
        platform_id="zakupki_mos",
        platform=cfg.dom.platforms["zakupki_mos"],
        delayer=object(),  # type: ignore[arg-type]
        repository=None,
        notifier=None,  # type: ignore[arg-type]
        site_cb=None,  # type: ignore[arg-type]
        db_cb=None,  # type: ignore[arg-type]
        now=now,
    )


@pytest.mark.asyncio
async def test_min_deadline_days_too_close_skips(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=5)
    # до дедлайна 2 дня < 5 -> пропустить
    record = {"number": "3", "deadline": datetime(2026, 8, 5, 12, 0, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is True  # noqa: SLF001


@pytest.mark.asyncio
async def test_min_deadline_days_enough_kept(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=5)
    # до дедлайна 10 дней >= 5 -> обрабатывать
    record = {"number": "4", "deadline": datetime(2026, 8, 13, 12, 0, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is False  # noqa: SLF001


@pytest.mark.asyncio
async def test_min_deadline_days_disabled(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None)
    record = {"number": "5", "deadline": datetime(2026, 8, 4, 12, 0, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is False  # noqa: SLF001


@pytest.mark.asyncio
async def test_min_deadline_days_ignored_when_deadline_check_off(
    app_config: AppConfig,
) -> None:
    """deadline_not_expired=false отключает и min_deadline_days (дедлайн не режется)."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(
        app_config,
        now,
        min_deadline_days=5,
        deadline_not_expired=False,
    )
    record = {"number": "6", "deadline": datetime(2026, 8, 1, 12, 0, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is False  # noqa: SLF001


def test_is_known_skips_existing(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None)
    # Без загруженного набора (репозиторий None) — ничего не пропускаем.
    assert orch._is_known("1") is False  # noqa: SLF001
    orch._known_numbers = {"1", "2"}
    assert orch._is_known("1") is True  # noqa: SLF001
    assert orch._is_known("3") is False  # noqa: SLF001
    assert orch._is_known(None) is False  # noqa: SLF001
