"""Unit-тесты условий прекращения обработки закупки.

Ключевые слова и слова-исключения здесь НЕ тестируются: по R9 они применяются
обязательной клиентской пост-фильтрацией (см. ``test_filtering.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from zakupki_parser.config.models import AppConfig
from zakupki_parser.parser.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_deadline_expired_skips(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, deadline_not_expired=True)
    record = {"number": "1", "deadline": datetime(2026, 8, 1, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is True  # noqa: SLF001


@pytest.mark.asyncio
async def test_deadline_future_not_skipped(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, deadline_not_expired=True)
    record = {"number": "2", "deadline": datetime(2026, 8, 10, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is False  # noqa: SLF001


def _make_orch(
    app_config: AppConfig,
    now: datetime,
    deadline_not_expired: bool = True,
) -> Orchestrator:
    cfg = app_config.model_copy(deep=True)
    cfg.service.search_criteria.deadline_not_expired = deadline_not_expired
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
def test_is_known_skips_existing(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now)
    # Без загруженного набора (репозиторий None) — ничего не пропускаем.
    assert orch._is_known("1") is False  # noqa: SLF001
    orch._known_numbers = {"1", "2"}
    assert orch._is_known("1") is True  # noqa: SLF001
    assert orch._is_known("3") is False  # noqa: SLF001
    assert orch._is_known(None) is False  # noqa: SLF001


def test_is_active_matches_active_status(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now)
    assert orch._is_active({"status": "Прием предложений"}) is True  # noqa: SLF001


def test_is_active_normalizes_case_and_ellipsis(app_config: AppConfig) -> None:
    """Верхний регистр и хвостовое CSS-обрезание '...' не ломают сопоставление."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now)
    assert orch._is_active({"status": "ПРИЕМ ПРЕДЛОЖЕНИЙ ..."}) is True  # noqa: SLF001
    assert orch._is_active({"status": "Прием предложений ..."}) is True  # noqa: SLF001


def test_is_active_inactive_status(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now)
    assert orch._is_active({"status": "Прием предложений завершен"}) is False  # noqa: SLF001
    assert orch._is_active({"status": ""}) is False  # noqa: SLF001
    assert orch._is_active({}) is False  # noqa: SLF001


def test_is_active_ignores_deadline_at_write(app_config: AppConfig) -> None:
    """Срок актуальности не влияет на is_active при записи в БД.

    Проверка текущей даты — обязанность клиента (репозиторий/API),
    см. ``effective_is_active``.
    """
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now)
    expired = {"status": "Прием предложений", "deadline": datetime(2026, 8, 1, tzinfo=UTC)}
    assert orch._is_active(expired) is True  # noqa: SLF001


def test_is_active_default_true_when_no_statuses(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now)
    orch._platform.list_config.active_statuses = None
    assert orch._is_active({"status": "anything"}) is True  # noqa: SLF001
    assert orch._is_active({}) is True  # noqa: SLF001
