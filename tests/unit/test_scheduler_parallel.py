"""Unit-тесты параллельной обработки площадок (Scheduler.run_once, R5/4B)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from zakupki_parser.scheduler import Scheduler


class _FakeRepo:
    """Минимальный репозиторий: возвращает список включённых площадок."""

    async def enabled_platform_ids(self) -> set[str]:
        return {"p1", "p2", "p3"}


def _make_scheduler(app_config: Any, max_concurrent: int) -> Scheduler:
    cfg = app_config.model_copy(deep=True)
    cfg.score.scoring_transport_url = ""  # recovery не выполняется
    cfg.parser.max_concurrent_platforms = max_concurrent
    scheduler = Scheduler(cfg)
    scheduler._repository = _FakeRepo()  # type: ignore[assignment]  # noqa: SLF001
    return scheduler


def _patch_platforms(
    scheduler: Scheduler,
    monkeypatch: pytest.MonkeyPatch,
    platform_ids: list[str],
) -> None:
    """Подставляем детерминированный набор площадок и один «профиль»."""

    async def fake_ctxs() -> list[object]:
        return [object()]

    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", fake_ctxs)
    monkeypatch.setattr(scheduler, "_ordered_enabled_platforms", lambda enabled: list(platform_ids))
    monkeypatch.setattr(scheduler, "_profile_on_platform", lambda ctx, platform_id: True)


def _install_tracked_process(
    scheduler: Scheduler,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_on: str | None = None,
    sleep: float = 0.05,
) -> tuple[list[str], list[str], Any]:
    """Устанавливает записывающий ``_process_platform``.

    Возвращает ``(started, finished, max_active_fn)`` — порядок стартов, порядок
    успешных завершений и функцию, возвращающую максимальную наблюдаемую
    параллельность (число одновременно выполняемых площадок).
    """
    started: list[str] = []
    finished: list[str] = []
    active = 0
    max_active = 0

    async def fake_process(platform_id: str, profiles: object, iteration: int = 0) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.append(platform_id)
        try:
            await asyncio.sleep(sleep)
        finally:
            active -= 1
        if fail_on == platform_id:
            raise RuntimeError(f"boom: {platform_id}")
        finished.append(platform_id)

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)
    return started, finished, lambda: max_active


@pytest.mark.asyncio
async def test_run_once_respects_concurrency_limit(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """max_concurrent_platforms=2: три площадки, в полёте одновременно не более двух."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    _patch_platforms(scheduler, monkeypatch, ["p1", "p2", "p3"])
    started, finished, max_active_fn = _install_tracked_process(scheduler, monkeypatch)

    await scheduler.run_once()

    assert max_active_fn() <= 2
    assert set(started) == {"p1", "p2", "p3"}
    assert set(finished) == {"p1", "p2", "p3"}


@pytest.mark.asyncio
async def test_run_once_sequential_when_limit_one(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """max_concurrent_platforms=1: старты не пересекаются (прежнее поведение)."""
    scheduler = _make_scheduler(app_config, max_concurrent=1)
    _patch_platforms(scheduler, monkeypatch, ["p1", "p2"])
    started, finished, max_active_fn = _install_tracked_process(scheduler, monkeypatch)

    await scheduler.run_once()

    assert max_active_fn() == 1
    assert started == finished  # строго последовательно
    assert set(finished) == {"p1", "p2"}


@pytest.mark.asyncio
async def test_run_once_isolates_platform_failure(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой одной площадки не отменяет остальные (gather return_exceptions)."""
    scheduler = _make_scheduler(app_config, max_concurrent=3)
    _patch_platforms(scheduler, monkeypatch, ["p1", "p2", "p3"])
    _started, finished, _max_active_fn = _install_tracked_process(
        scheduler, monkeypatch, fail_on="p2"
    )

    await scheduler.run_once()  # не поднимает исключение

    assert set(finished) == {"p1", "p3"}


@pytest.mark.asyncio
async def test_run_once_respects_per_domain_limit(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Одинаковый domain_group: площадки одного домена не пересекаются (R5).

    Даже при max_concurrent_platforms=2 две площадки общего бэкенда (44-ФЗ/223-ФЗ
    одного сайта) выполняются строго последовательно — общий IP/антибот/rate-limit.
    """
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    # Оба platform_id из тестового конфига относим к одному домену.
    scheduler._cfg.dom.platforms["zakupki_mos"].domain_group = "shared.ru"
    scheduler._cfg.dom.platforms["zakupki_gov"].domain_group = "shared.ru"
    _patch_platforms(scheduler, monkeypatch, ["zakupki_mos", "zakupki_gov"])
    started, finished, max_active_fn = _install_tracked_process(scheduler, monkeypatch)

    await scheduler.run_once()

    assert max_active_fn() == 1
    assert set(started) == {"zakupki_mos", "zakupki_gov"}
    assert set(finished) == {"zakupki_mos", "zakupki_gov"}


@pytest.mark.asyncio
async def test_run_once_parallelizes_different_domains(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Разные домены параллелятся в пределах max_concurrent_platforms (R5)."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    scheduler._cfg.dom.platforms["zakupki_mos"].domain_group = "mos.ru"
    scheduler._cfg.dom.platforms["zakupki_gov"].domain_group = "gov.ru"
    _patch_platforms(scheduler, monkeypatch, ["zakupki_mos", "zakupki_gov"])
    _started, _finished, max_active_fn = _install_tracked_process(scheduler, monkeypatch)

    await scheduler.run_once()

    assert max_active_fn() == 2


@pytest.mark.asyncio
async def test_run_once_noop_without_profiles(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой список профилей: площадки не обрабатываются."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    _patch_platforms(scheduler, monkeypatch, ["p1", "p2"])
    called: list[str] = []

    async def fake_process(platform_id: str, profiles: object, iteration: int = 0) -> None:
        called.append(platform_id)

    async def no_ctxs() -> list[object]:
        return []

    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", no_ctxs)
    monkeypatch.setattr(scheduler, "_process_platform", fake_process)

    await scheduler.run_once()

    assert called == []


@pytest.mark.asyncio
async def test_run_once_noop_without_platforms(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нет включённых площадок: обход не выполняется."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    _patch_platforms(scheduler, monkeypatch, [])
    called: list[str] = []

    async def fake_process(platform_id: str, profiles: object, iteration: int = 0) -> None:
        called.append(platform_id)

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)

    await scheduler.run_once()

    assert called == []
