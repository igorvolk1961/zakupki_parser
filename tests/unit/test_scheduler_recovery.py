"""Unit-тесты recovery-постановки закупок в очередь скоринга (Scheduler._recover_scoring_queue)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from zakupki_parser.scheduler import Scheduler


class _FakeTransport:
    """Записывает enqueue-вызовы; при ``fail_on`` бросает исключение."""

    def __init__(self, fail_on: int | None = None) -> None:
        self.enqueued: list[tuple[int, float, str]] = []
        self._fail_on = fail_on

    async def enqueue(self, procurement_id: int, priority: float, stage: str = "fit") -> None:
        if self._fail_on is not None and len(self.enqueued) >= self._fail_on:
            raise RuntimeError("transport down")
        self.enqueued.append((procurement_id, priority, stage))


class _FakeRepo:
    """Фейковый репозиторий: find_unscored исключает отмеченные как поставленные."""

    def __init__(
        self,
        items: list[dict[str, Any]],
        queued_at: dict[int, datetime] | None = None,
    ) -> None:
        self._items = items
        self._queued_at = queued_at or {}
        self.marked: list[int] = []

    async def find_unscored(
        self, limit: int | None = None, queued_before: datetime | None = None
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in self._items:
            if item["id"] in self.marked:
                continue
            queued = self._queued_at.get(item["id"])
            if queued is not None:
                if queued_before is None:
                    # Без порога старения — как в репозитории: только если запись
                    # обновлялась после постановки (иначе «уже поставлена»).
                    upd = item.get("update_date")
                    if not (upd is not None and upd > queued):
                        continue
                elif queued >= queued_before:
                    continue
            items.append(item)
        return items[: limit or len(items)]

    async def mark_scoring_queued(self, procurement_id: int, queued_at: datetime) -> bool:
        self.marked.append(procurement_id)
        return True


def _item(
    pid: int,
    *,
    update_date: datetime | None = None,
    publication_date: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": pid,
        "number": f"N-{pid}",
        "platform_id": "zakupki_mos",
        "update_date": update_date,
        "publication_date": publication_date,
    }


@pytest.mark.asyncio
async def test_recover_enqueues_unscored_with_time_priority(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Приоритет — по времени обновления/публикации; после enqueue проставляется метка."""
    scheduler = Scheduler(app_config)
    fake_transport = _FakeTransport()
    repo = _FakeRepo(
        [
            _item(1, publication_date=datetime(2026, 8, 10, 12, 0, tzinfo=UTC)),
            _item(
                2,
                update_date=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
                publication_date=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            ),
        ]
    )
    scheduler._repository = repo  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(
        "zakupki_parser.scheduler.ScoringTransportClient", lambda url: fake_transport
    )

    await scheduler._recover_scoring_queue()  # noqa: SLF001

    assert [item[0] for item in fake_transport.enqueued] == [1, 2]
    # Приоритет = epoch времени: у записи 2 — update_date (новее, выше приоритет).
    assert fake_transport.enqueued[0][1] == datetime(2026, 8, 10, 12, 0, tzinfo=UTC).timestamp()
    assert fake_transport.enqueued[1][1] == datetime(2026, 8, 15, 12, 0, tzinfo=UTC).timestamp()
    assert all(stage == "fit" for _, _, stage in fake_transport.enqueued)
    assert repo.marked == [1, 2]


@pytest.mark.asyncio
async def test_recover_stops_on_transport_failure(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой enqueue (транспорт снова недоступен) прерывает recovery до следующего цикла."""
    scheduler = Scheduler(app_config)
    fake_transport = _FakeTransport(fail_on=0)
    repo = _FakeRepo([_item(1), _item(2)])
    scheduler._repository = repo  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(
        "zakupki_parser.scheduler.ScoringTransportClient", lambda url: fake_transport
    )

    await scheduler._recover_scoring_queue()  # noqa: SLF001

    assert fake_transport.enqueued == []
    assert repo.marked == []


@pytest.mark.asyncio
async def test_recover_noop_without_transport(app_config: Any) -> None:
    """Без scoring_transport_url recovery не выполняется."""
    cfg = app_config.model_copy(deep=True)
    cfg.score.scoring_transport_url = ""
    scheduler = Scheduler(cfg)
    repo = _FakeRepo([_item(1)])
    scheduler._repository = repo  # type: ignore[assignment]  # noqa: SLF001

    await scheduler._recover_scoring_queue()  # noqa: SLF001

    assert repo.marked == []


@pytest.mark.asyncio
async def test_recover_reenqueues_stale_queued(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Задание потеряно (метка постановки старше TTL): закупка снова в очереди."""
    cfg = app_config.model_copy(deep=True)
    cfg.score.recovery_ttl_seconds = 3600.0
    scheduler = Scheduler(cfg)
    # Поставлена в очередь «давно», заново не обновлялась, но так и не отскорингована.
    stale = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    fake_transport = _FakeTransport()
    repo = _FakeRepo(
        [_item(1, publication_date=datetime(2026, 8, 10, 12, 0, tzinfo=UTC))],
        queued_at={1: stale},
    )
    scheduler._repository = repo  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(
        "zakupki_parser.scheduler.ScoringTransportClient", lambda url: fake_transport
    )

    await scheduler._recover_scoring_queue()  # noqa: SLF001

    assert [item[0] for item in fake_transport.enqueued] == [1]
    assert repo.marked == [1]


@pytest.mark.asyncio
async def test_recover_skips_fresh_queued_when_ttl_disabled(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTL=0 (самовосстановление выключено): «давно поставленная» закупка не дублируется."""
    cfg = app_config.model_copy(deep=True)
    cfg.score.recovery_ttl_seconds = 0.0
    scheduler = Scheduler(cfg)
    stale = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    fake_transport = _FakeTransport()
    repo = _FakeRepo(
        [_item(1, publication_date=datetime(2026, 8, 10, 12, 0, tzinfo=UTC))],
        queued_at={1: stale},
    )
    scheduler._repository = repo  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(
        "zakupki_parser.scheduler.ScoringTransportClient", lambda url: fake_transport
    )

    await scheduler._recover_scoring_queue()  # noqa: SLF001

    assert fake_transport.enqueued == []
    assert repo.marked == []
