"""Unit-тесты параллельной обработки площадок (Scheduler.run_once, R5/4B)."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from zakupki_parser.options import paid_default_options
from zakupki_parser.parser.orchestrator.context import ProfileRunContext
from zakupki_parser.scheduler import Scheduler
from zakupki_parser.storage.db import ALL_PLATFORMS_SENTINEL, Profile, UserAccount


def _competencies_json() -> str:
    """Канонический JSON валидного непустого профиля компетенций (BR-07)."""
    return json.dumps(
        {
            "positioning": "Тестовые компетенции",
            "breadth": "broad",
            "competencies": [{"area": "Аудит", "description": "обследование"}],
            "exclusions": [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _free_account(uid: int) -> UserAccount:
    """Аккаунт только с бесплатными опциями (нет платного LLM-скоринга)."""
    return UserAccount(
        user_id=uid,
        name="free",
        options=paid_default_options(False),
        is_active=True,
    )


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
    # Тесты этого файла — про параллельность/домены обхода площадок, не про
    # Stage D (маршрутизацию через индекс по коду): profile-заглушки (object())
    # не несут is_system_index/profile.okpd_codes, поэтому отключаем
    # разбиение — все ctx идут живым обходом, как было до Stage D.
    monkeypatch.setattr(scheduler, "_split_ctxs_for_index_routing", lambda ctxs: ([], ctxs))


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

    async def fake_process(
        platform_id: str,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
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

    async def fake_process(
        platform_id: str,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        called.append(platform_id)

    async def no_ctxs() -> list[object]:
        return []

    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", no_ctxs)
    monkeypatch.setattr(scheduler, "_process_platform", fake_process)

    await scheduler.run_once()

    assert called == []


@pytest.mark.asyncio
async def test_run_once_records_cycle_stats(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_once агрегирует received/saved/сбои площадок цикла и пишет parser_cycle_stats
    (devops-мониторинг, вкладка «Мониторинг»): один сбой площадки (p2) не портит
    сводку остальных, только увеличивает platforms_failed."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    # Реальные platform_id из тестовых configs/dom — на этот раз _process_platform
    # НЕ подменяется целиком (в отличие от остальных тестов файла), нужна его настоящая
    # реализация (agrегация в cycle), а она резолвит platform_id через configs/dom.
    _patch_platforms(scheduler, monkeypatch, ["zakupki_mos", "zakupki_gov"])

    async def fake_parse(
        platform_id: str,
        platform: object,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
    ) -> dict[str, int]:
        if platform_id == "zakupki_gov":
            raise RuntimeError("boom")
        return {"received": 5, "saved": 2, "known": 1}

    recorded: list[dict[str, Any]] = []

    class _RepoWithCycleStats(_FakeRepo):
        async def record_cycle_stats(self, **kwargs: Any) -> None:
            recorded.append(kwargs)

    monkeypatch.setattr(scheduler, "_parse_platform", fake_parse)
    scheduler._repository = _RepoWithCycleStats()  # type: ignore[assignment]  # noqa: SLF001

    await scheduler.run_once(iteration=3)

    assert len(recorded) == 1
    stats = recorded[0]
    assert stats["iteration"] == 3
    assert stats["kind"] == "regular"
    assert stats["platforms_total"] == 2
    assert stats["platforms_failed"] == 1
    assert stats["received"] == 5  # только zakupki_mos успешна
    assert stats["saved"] == 2
    assert stats["finished_at"] >= stats["started_at"]


@pytest.mark.asyncio
async def test_run_once_records_platform_stats(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_process_platform`` пишет ПЕР-ПЛОЩАДОЧНУЮ статистику (``parser_platform_
    stats``, devops-мониторинг, вкладка «Мониторинг» — разбивка по площадкам,
    в дополнение к сводке цикла целиком) — отдельно для успеха и для сбоя
    площадки, с корректными platform_id/iteration/received/saved/error_message.
    """
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    _patch_platforms(scheduler, monkeypatch, ["zakupki_mos", "zakupki_gov"])

    async def fake_parse(
        platform_id: str,
        platform: object,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
    ) -> dict[str, int]:
        if platform_id == "zakupki_gov":
            raise RuntimeError("boom")
        return {"received": 5, "saved": 2, "known": 1}

    recorded: list[dict[str, Any]] = []

    class _RepoWithPlatformStats(_FakeRepo):
        async def upsert_platform_stats(self, **kwargs: Any) -> None:
            recorded.append(kwargs)

    monkeypatch.setattr(scheduler, "_parse_platform", fake_parse)
    scheduler._repository = _RepoWithPlatformStats()  # type: ignore[assignment]  # noqa: SLF001

    await scheduler.run_once(iteration=3)

    assert len(recorded) == 2
    by_platform = {r["platform_id"]: r for r in recorded}
    ok = by_platform["zakupki_mos"]
    assert ok["iteration"] == 3
    assert ok["success"] is True
    assert ok["received"] == 5
    assert ok["saved"] == 2
    assert ok["error_message"] is None
    assert ok["finished_at"] >= ok["started_at"]
    failed = by_platform["zakupki_gov"]
    assert failed["iteration"] == 3
    assert failed["success"] is False
    assert failed["received"] == 0
    assert failed["saved"] == 0
    assert failed["error_message"] == "boom"


class _FakeProfileCtx:
    """Профиль-контекст для внеочередного обхода (нужны ``id`` и ``profile.id``).

    ``is_system_index=False``/``profile.okpd_codes=[]`` — чтобы
    ``Scheduler._split_ctxs_for_index_routing`` (Stage D) естественно отправлял
    такой профиль в живой обход БЕЗ сужения (нет кодов — индекс не применим),
    не мешая тестам этого файла, которые не про маршрутизацию по коду.
    """

    def __init__(self, profile_id: int) -> None:
        self.id = profile_id
        self.profile = SimpleNamespace(id=profile_id, okpd_codes=[])
        self.scoring_allowed = True
        self.is_system_index = False
        self.crawl_okpd_codes = None


@pytest.mark.asyncio
async def test_run_once_regular_pass_uses_incremental_window(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Регулярный проход не включает полное окно (full_window=False)."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    _patch_platforms(scheduler, monkeypatch, ["p1"])
    flags: list[bool] = []

    async def fake_process(
        platform_id: str,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        flags.append(full_window)

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)

    await scheduler.run_once()

    assert flags == [False]


@pytest.mark.asyncio
async def test_request_profile_refresh_sets_event_and_ids(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """request_profile_refresh добавляет id и будит планировщик из сна.

    Throttle, не debounce: профиль без записи в ``_refresh_last_run_at`` (ещё
    ни разу не обходился) готов к обходу немедленно — ``_refresh_remaining``
    возвращает 0, задержки перед первым обходом нет.
    """
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    assert not scheduler._refresh_ids  # noqa: SLF001
    assert not scheduler._refresh_event.is_set()  # noqa: SLF001

    scheduler.request_profile_refresh(7)
    assert scheduler._refresh_ids == {7}  # noqa: SLF001
    assert scheduler._refresh_event.is_set()  # noqa: SLF001
    assert scheduler._refresh_remaining(7) == 0.0  # noqa: SLF001

    # Повторный сигнал того же профиля — без изменений (ещё не обходился).
    scheduler.request_profile_refresh(7)
    assert scheduler._refresh_ids == {7}  # noqa: SLF001
    assert scheduler._refresh_event.is_set()  # noqa: SLF001

    # Другой профиль в том же батче также накапливается.
    scheduler.request_profile_refresh(8)
    assert scheduler._refresh_ids == {7, 8}  # noqa: SLF001


@pytest.mark.asyncio
async def test_run_refresh_pass_processes_only_requested_profiles(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Внеочередной обход: только затронутые профили, по всем их площадкам, full_window."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    calls: list[tuple[str, list[int], int, bool]] = []

    async def fake_process(
        platform_id: str,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        calls.append(
            (platform_id, sorted(c.id for c in profiles), iteration, full_window)  # type: ignore[attr-defined]
        )

    async def fake_gather(only_ids: set[int] | None = None) -> list[_FakeProfileCtx]:
        assert only_ids == {7}
        return [_FakeProfileCtx(7)]

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)
    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", fake_gather)
    monkeypatch.setattr(scheduler, "_ordered_enabled_platforms", lambda enabled: ["p1", "p2"])
    monkeypatch.setattr(scheduler, "_profile_on_platform", lambda ctx, platform_id: True)

    scheduler.request_profile_refresh(7)
    await scheduler._run_refresh_pass(iteration=5)  # noqa: SLF001

    assert scheduler._refresh_ids == set()  # noqa: SLF001
    assert 7 in scheduler._refresh_last_run_at  # noqa: SLF001
    assert calls == [
        ("p1", [7], 5, True),
        ("p2", [7], 5, True),
    ]


@pytest.mark.asyncio
async def test_run_refresh_pass_throttles_repeated_edit(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Throttle (``_refresh_remaining``), не кап «раз за регулярный цикл»: правка
    того же профиля СРАЗУ после его обхода не запускает новый полный обход —
    остаётся накопленной и выполнится, как только истечёт ``profile_refresh_
    debounce_seconds`` с ЗАВЕРШЕНИЯ предыдущего обхода (не с границы цикла)."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    scheduler._cfg.ops.profile_refresh_debounce_seconds = 60.0  # noqa: SLF001
    calls: list[tuple[str, list[int], int, bool]] = []
    gathers: list[set[int] | None] = []

    async def fake_process(
        platform_id: str,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        calls.append(
            (platform_id, sorted(c.id for c in profiles), iteration, full_window)  # type: ignore[attr-defined]
        )

    async def fake_gather(only_ids: set[int] | None = None) -> list[_FakeProfileCtx]:
        gathers.append(only_ids)
        if only_ids:
            return [_FakeProfileCtx(p) for p in only_ids]
        return []

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)
    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", fake_gather)
    monkeypatch.setattr(scheduler, "_ordered_enabled_platforms", lambda enabled: ["p1"])
    monkeypatch.setattr(scheduler, "_profile_on_platform", lambda ctx, platform_id: True)

    # Первая правка: профиль ещё не обходился — обход стартует немедленно
    # (без задержки перед первым обходом).
    scheduler.request_profile_refresh(7)
    await scheduler._run_refresh_pass(iteration=1)  # noqa: SLF001
    assert len(calls) == 1
    assert scheduler._refresh_remaining(7) > 0  # noqa: SLF001  # throttle начался

    # Вторая правка того же профиля сразу после обхода — ещё внутри throttle-окна.
    scheduler.request_profile_refresh(7)
    await scheduler._run_refresh_pass(iteration=2)  # noqa: SLF001

    assert len(calls) == 1  # повторный полный обход пока не запускается
    assert gathers == [{7}]  # вторая правка не доходит даже до сбора контекста
    assert scheduler._refresh_ids == {7}  # noqa: SLF001  # остаётся накопленной
    assert scheduler._refresh_remaining(7) > 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_run_refresh_pass_processes_again_once_throttle_elapses(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Как только throttle-окно истекло (debounce с завершения предыдущего обхода
    прошёл), накопленная правка того же профиля обходится — не дожидаясь границы
    регулярного цикла."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    scheduler._cfg.ops.profile_refresh_debounce_seconds = 60.0  # noqa: SLF001
    calls: list[tuple[str, list[int], int, bool]] = []

    async def fake_process(
        platform_id: str,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        calls.append(
            (platform_id, sorted(c.id for c in profiles), iteration, full_window)  # type: ignore[attr-defined]
        )

    async def fake_gather(only_ids: set[int] | None = None) -> list[_FakeProfileCtx]:
        if only_ids:
            return [_FakeProfileCtx(p) for p in only_ids]
        return []

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)
    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", fake_gather)
    monkeypatch.setattr(scheduler, "_ordered_enabled_platforms", lambda enabled: ["p1"])
    monkeypatch.setattr(scheduler, "_profile_on_platform", lambda ctx, platform_id: True)

    scheduler.request_profile_refresh(7)
    await scheduler._run_refresh_pass(iteration=1)  # noqa: SLF001
    assert len(calls) == 1

    scheduler.request_profile_refresh(7)
    await scheduler._run_refresh_pass(iteration=2)  # noqa: SLF001
    assert len(calls) == 1  # ещё внутри throttle-окна

    # Throttle-окно истекло (имитируем истечение времени напрямую, не sleep).
    scheduler._refresh_last_run_at[7] = time.monotonic() - 61.0  # noqa: SLF001
    assert scheduler._refresh_remaining(7) == 0.0  # noqa: SLF001
    await scheduler._run_refresh_pass(iteration=3)  # noqa: SLF001

    assert len(calls) == 2  # накопленная правка наконец обошлась
    assert scheduler._refresh_ids == set()  # noqa: SLF001


def test_next_refresh_wait_reflects_soonest_eligible_profile(app_config: Any) -> None:
    """``_next_refresh_wait`` — минимум остатка throttle среди накопленных
    профилей; ``None``, если очередь пуста."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    scheduler._cfg.ops.profile_refresh_debounce_seconds = 60.0  # noqa: SLF001

    assert scheduler._next_refresh_wait() is None  # noqa: SLF001

    scheduler.request_profile_refresh(7)
    assert scheduler._next_refresh_wait() == 0.0  # noqa: SLF001  # первый обход — сразу

    now = time.monotonic()
    scheduler._refresh_last_run_at[7] = now - 30.0  # noqa: SLF001  # ~30с осталось
    scheduler.request_profile_refresh(8)  # 8 обходится сразу (0с)

    wait = scheduler._next_refresh_wait()  # noqa: SLF001
    assert wait == 0.0  # минимум по батчу — профиль 8, готов немедленно


@pytest.mark.asyncio
async def test_run_refresh_pass_noop_when_profile_not_eligible(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Профиль не пригоден (например, отключён/нет компетенций): обход не выполняется."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    called: list[str] = []

    async def fake_process(
        platform_id: str,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        called.append(platform_id)

    async def fake_gather(only_ids: set[int] | None = None) -> list[object]:
        return []

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)
    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", fake_gather)

    scheduler.request_profile_refresh(42)
    await scheduler._run_refresh_pass(iteration=1)  # noqa: SLF001

    assert scheduler._refresh_ids == set()  # noqa: SLF001
    assert called == []


@pytest.mark.asyncio
async def test_run_once_noop_without_platforms(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нет включённых площадок: обход не выполняется."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    _patch_platforms(scheduler, monkeypatch, [])
    called: list[str] = []

    async def fake_process(
        platform_id: str,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        called.append(platform_id)

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)

    await scheduler.run_once()

    assert called == []


@pytest.mark.asyncio
async def test_run_refresh_pass_rebuilds_results_on_flag(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правка профиля с rebuild/rescore запускает перестройку результатов сбора."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    rebuild_calls: list[tuple[int, bool]] = []

    async def fake_process(
        platform_id: str,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        return None

    async def fake_gather(only_ids: set[int] | None = None) -> list[_FakeProfileCtx]:
        assert only_ids == {7}
        return [_FakeProfileCtx(7)]

    async def fake_rebuild(ctx: object, *, rescore: bool = False) -> None:
        rebuild_calls.append((int(ctx.profile.id), rescore))  # type: ignore[attr-defined]

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)
    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", fake_gather)
    monkeypatch.setattr(scheduler, "_ordered_enabled_platforms", lambda enabled: [])
    monkeypatch.setattr(scheduler, "_rebuild_profile_results", fake_rebuild)
    monkeypatch.setattr(scheduler, "_profile_on_platform", lambda ctx, platform_id: True)

    scheduler.request_profile_refresh(7, rebuild=True, rescore=True)
    await scheduler._run_refresh_pass(iteration=5)  # noqa: SLF001

    assert rebuild_calls == [(7, True)]
    assert scheduler._refresh_ids == set()  # noqa: SLF001
    assert scheduler._refresh_rebuild == set()  # noqa: SLF001
    assert scheduler._refresh_rescore == set()  # noqa: SLF001


@pytest.mark.asyncio
async def test_run_refresh_pass_skips_live_crawl_for_fully_covered_profile(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правка профиля, полностью покрытого индексом: синхронизация из БД даёт более
    полную ретроспективу, чем ограниченный окном живой обход — поэтому он вообще
    не запускается (Stage D, было безусловно ВСЕГДА до этого фикса)."""
    cfg = app_config.model_copy(deep=True)
    cfg.service.indexing.enabled = True
    cfg.service.indexing.okpd2_prefixes = ["62"]
    scheduler = _make_scheduler(cfg, max_concurrent=2)
    ctx = _index_ctx(okpd_codes=["62.01"], profile_id=7)

    async def fake_gather(only_ids: set[int] | None = None) -> list[ProfileRunContext]:
        assert only_ids == {7}
        return [ctx]

    live_calls: list[int] = []

    async def fake_process(
        platform_id: str,
        profiles: list[ProfileRunContext],
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        live_calls.extend(c.profile.id for c in profiles)

    sync_calls: list[int] = []

    async def fake_sync(ctxs: list[ProfileRunContext]) -> None:
        sync_calls.extend(c.profile.id for c in ctxs)

    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", fake_gather)
    monkeypatch.setattr(scheduler, "_ordered_enabled_platforms", lambda enabled: ["p1"])
    monkeypatch.setattr(scheduler, "_profile_on_platform", lambda ctx, platform_id: True)
    monkeypatch.setattr(scheduler, "_process_platform", fake_process)
    monkeypatch.setattr(scheduler, "_sync_profiles_via_index", fake_sync)

    scheduler.request_profile_refresh(7)  # без rebuild — обычная запрошенная правка
    await scheduler._run_refresh_pass(iteration=1)  # noqa: SLF001

    assert sync_calls == [7]
    assert live_calls == []


@pytest.mark.asyncio
async def test_run_refresh_pass_narrows_live_crawl_for_partially_covered_profile(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Частичное покрытие при правке профиля: синхронизация из БД ПЛЮС живой обход,
    но только по непокрытому остатку кодов — не по всему профилю."""
    cfg = app_config.model_copy(deep=True)
    cfg.service.indexing.enabled = True
    cfg.service.indexing.okpd2_prefixes = ["62"]
    scheduler = _make_scheduler(cfg, max_concurrent=2)
    ctx = _index_ctx(okpd_codes=["62.01", "71.20"], profile_id=7)

    async def fake_gather(only_ids: set[int] | None = None) -> list[ProfileRunContext]:
        return [ctx]

    live_calls: list[tuple[int, list[str] | None]] = []

    async def fake_process(
        platform_id: str,
        profiles: list[ProfileRunContext],
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        live_calls.extend((c.profile.id, c.crawl_okpd_codes) for c in profiles)

    sync_calls: list[int] = []

    async def fake_sync(ctxs: list[ProfileRunContext]) -> None:
        sync_calls.extend(c.profile.id for c in ctxs)

    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", fake_gather)
    monkeypatch.setattr(scheduler, "_ordered_enabled_platforms", lambda enabled: ["p1"])
    monkeypatch.setattr(scheduler, "_profile_on_platform", lambda ctx, platform_id: True)
    monkeypatch.setattr(scheduler, "_process_platform", fake_process)
    monkeypatch.setattr(scheduler, "_sync_profiles_via_index", fake_sync)

    scheduler.request_profile_refresh(7)
    await scheduler._run_refresh_pass(iteration=1)  # noqa: SLF001

    assert sync_calls == [7]
    assert live_calls == [(7, ["71.20"])]


@pytest.mark.asyncio
async def test_run_service_wakes_on_refresh_keeps_regular_cadence(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сигнал во время сна будит планировщик на внеочередной обход; регулярный проход
    по расписанию не дублируется."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    scheduler._cfg.ops.timeout_seconds = 3600  # noqa: SLF001
    scheduler._cfg.ops.profile_refresh_debounce_seconds = 0.0  # noqa: SLF001

    async def noop() -> None:
        return None

    monkeypatch.setattr(scheduler, "start", noop)
    monkeypatch.setattr(scheduler, "stop", noop)

    full_passes: list[int] = []
    refresh_passes: list[list[int]] = []

    async def fake_run_once(iteration: int = 0) -> None:
        full_passes.append(iteration)

    async def fake_refresh_pass(iteration: int = 0) -> None:
        ids = sorted(scheduler._refresh_ids)  # noqa: SLF001
        scheduler._refresh_ids.clear()  # noqa: SLF001
        refresh_passes.append(ids)

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(scheduler, "_run_refresh_pass", fake_refresh_pass)

    task = asyncio.create_task(scheduler.run_service())
    # run_service теперь запускает регулярный и внеочередной циклы как отдельные
    # дочерние задачи (asyncio.create_task) — им нужно несколько оборотов event
    # loop'а, чтобы дойти до первого run_once (один sleep(0) уже не гарантирует).
    for _ in range(5):
        await asyncio.sleep(0.01)
    assert full_passes == [1]

    # Профиль создан во время «сна» планировщика: внеочередной обход сразу.
    scheduler.request_profile_refresh(7)
    for _ in range(5):
        await asyncio.sleep(0.01)
    assert refresh_passes == [[7]]
    assert full_passes == [1]  # регулярный проход не запускался повторно

    # Вторая волна изменений — ещё один внеочередной обход.
    scheduler.request_profile_refresh(8)
    for _ in range(5):
        await asyncio.sleep(0.01)
    assert refresh_passes == [[7], [8]]

    scheduler._stop.set()  # noqa: SLF001
    await asyncio.wait_for(task, timeout=2)
    assert scheduler._refresh_ids == set()  # noqa: SLF001


@pytest.mark.asyncio
async def test_refresh_pass_runs_while_regular_pass_still_in_flight(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Внеочередной обход больше не ждёт завершения уже идущего регулярного
    прохода (_regular_loop/_refresh_loop — независимые параллельные циклы):
    правка профиля, сделанная ПОКА регулярный проход ещё выполняется (не
    завершился), запускает внеочередной обход немедленно, а не после того, как
    регулярный проход закончит работу."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    scheduler._cfg.ops.timeout_seconds = 3600  # noqa: SLF001
    scheduler._cfg.ops.profile_refresh_debounce_seconds = 0.0  # noqa: SLF001

    async def noop() -> None:
        return None

    monkeypatch.setattr(scheduler, "start", noop)
    monkeypatch.setattr(scheduler, "stop", noop)

    regular_started = asyncio.Event()
    regular_release = asyncio.Event()
    refresh_passes: list[list[int]] = []

    async def fake_run_once(iteration: int = 0) -> None:
        regular_started.set()
        await regular_release.wait()  # имитация длинного «холодного» прохода

    async def fake_refresh_pass(iteration: int = 0) -> None:
        refresh_passes.append(sorted(scheduler._refresh_ids))  # noqa: SLF001
        scheduler._refresh_ids.clear()  # noqa: SLF001

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(scheduler, "_run_refresh_pass", fake_refresh_pass)

    task = asyncio.create_task(scheduler.run_service())
    await asyncio.wait_for(regular_started.wait(), timeout=1)

    # Регулярный проход ещё идёт (заблокирован на regular_release) — просим
    # внеочередной обход прямо сейчас.
    scheduler.request_profile_refresh(7)
    for _ in range(10):
        await asyncio.sleep(0.01)
    assert refresh_passes == [[7]]  # выполнился, НЕ дожидаясь завершения регулярного

    regular_release.set()
    scheduler._stop.set()  # noqa: SLF001
    await asyncio.wait_for(task, timeout=2)


def test_platform_sem_reused_and_rebuilt_on_limit_change(app_config: Any) -> None:
    """``_get_platform_sem``: один и тот же объект, пока лимит не поменялся
    (горячая правка config_parser.yaml -> max_concurrent_platforms без
    рестарта парсера — см. routes/config.py: state_setter)."""
    scheduler = _make_scheduler(app_config, max_concurrent=3)

    sem1 = scheduler._get_platform_sem()  # noqa: SLF001
    sem2 = scheduler._get_platform_sem()  # noqa: SLF001
    assert sem1 is sem2

    scheduler._cfg.parser.max_concurrent_platforms = 5  # noqa: SLF001
    sem3 = scheduler._get_platform_sem()  # noqa: SLF001
    assert sem3 is not sem1


def test_domain_sem_reused_per_domain_and_rebuilt_on_limit_change(app_config: Any) -> None:
    """``_get_domain_sem``: отдельный объект на каждый домен, переиспользуется,
    пока не поменялся max_concurrent_per_domain."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)

    a1 = scheduler._get_domain_sem("zakupki.gov.ru")  # noqa: SLF001
    a2 = scheduler._get_domain_sem("zakupki.gov.ru")  # noqa: SLF001
    b1 = scheduler._get_domain_sem("roseltorg.ru")  # noqa: SLF001
    assert a1 is a2
    assert a1 is not b1

    scheduler._cfg.parser.max_concurrent_per_domain = 2  # noqa: SLF001
    a3 = scheduler._get_domain_sem("zakupki.gov.ru")  # noqa: SLF001
    assert a3 is not a1


@pytest.mark.asyncio
async def test_stop_cancels_detached_refresh_tasks(app_config: Any) -> None:
    """``stop()`` отменяет ещё не завершившиеся детached-задачи внеочередных
    обходов (запущены asyncio.create_task в _refresh_loop — отмена run_service
    сама по себе их не отменяет, см. __init__/_refresh_tasks) и дожидается их
    отмены, прежде чем закрыть пул БД."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    started = asyncio.Event()
    cancelled = False

    async def never_finishes() -> None:
        nonlocal cancelled
        started.set()
        try:
            await asyncio.Event().wait()  # висит, пока не отменят
        except asyncio.CancelledError:
            cancelled = True
            raise

    task = asyncio.create_task(never_finishes())
    scheduler._refresh_tasks.add(task)  # noqa: SLF001
    task.add_done_callback(scheduler._refresh_tasks.discard)  # noqa: SLF001
    await asyncio.wait_for(started.wait(), timeout=1)

    await scheduler.stop()

    assert cancelled is True
    assert scheduler._refresh_tasks == set()  # noqa: SLF001


class _GatherRepo:
    """Фейковый репозиторий для ``_gather_profile_ctxs`` (профили + аккаунты)."""

    def __init__(
        self,
        profiles: list[Any],
        accounts: dict[int, list[UserAccount]] | None = None,
        keywords: dict[int, dict[str, list[str]]] | None = None,
    ) -> None:
        self._profiles = profiles
        self._accounts = accounts or {}
        self._keywords = keywords or {}

    async def list_enabled_profiles_for_active_users(self) -> list[Any]:
        return self._profiles

    async def get_users_with_trial(self, user_ids: list[int]) -> dict[int, Any]:
        return {int(uid): None for uid in user_ids}

    async def accounts_by_users(self, user_ids: list[int]) -> dict[int, list[UserAccount]]:
        return {int(uid): self._accounts.get(int(uid), []) for uid in user_ids}

    async def list_profiles_keywords(
        self, profile_ids: list[int]
    ) -> dict[int, dict[str, list[str]]]:
        return {
            int(pid): self._keywords.get(int(pid), {"keywords": [], "exclusion_words": []})
            for pid in profile_ids
        }


def _profile(pid: int, uid: int, *, competencies: str | None = None) -> Any:
    """Профиль-заглушка с crawl-полями (для ``_gather_profile_ctxs``)."""
    return SimpleNamespace(
        id=pid,
        user_id=uid,
        enabled=True,
        competencies=competencies or "",
        okpd_codes=["62.02"],
        nmck_min=None,
        nmck_max=None,
        target_etp=[],
        target_laws=[],
        target_regions=[],
        max_region_distance_km=None,
        search_in_documents=False,
    )


@pytest.mark.asyncio
async def test_gather_includes_profiles_without_scoring_option(
    app_config: Any,
) -> None:
    """Мониторинг работает без скоринга: профиль владельца с бесплатным аккаунтом
    попадает в обход (поисковый профиль), но ``scoring_allowed`` у него False."""
    scheduler = Scheduler(app_config)
    scheduler._repository = _GatherRepo(  # type: ignore[assignment]  # noqa: SLF001
        [_profile(11, 2), _profile(12, 2, competencies=_competencies_json())],
        accounts={2: [_free_account(2)]},
    )

    ctxs = await scheduler._gather_profile_ctxs()  # noqa: SLF001

    by_id = {c.profile.id: c for c in ctxs}
    assert set(by_id) == {11, 12}
    # У владельца нет опции scoring -> LLM-задания по этим профилям не ставятся.
    assert by_id[11].scoring_allowed is False
    assert by_id[12].scoring_allowed is False


@pytest.mark.asyncio
async def test_gather_scoring_allowed_needs_option_and_competencies(
    app_config: Any,
) -> None:
    """scoring_allowed=True только когда владелец имеет опцию scoring И у профиля
    валидные непустые компетенции; иначе профиль всё равно собирается (мониторинг)."""
    scheduler = Scheduler(app_config)
    # Пользователи 1 и 3 — активный аккаунт со всеми платными опциями.
    full_account = UserAccount(
        user_id=3,
        name="full",
        options=paid_default_options(True),
        is_active=True,
    )
    scheduler._repository = _GatherRepo(  # type: ignore[assignment]  # noqa: SLF001
        [
            _profile(1, 1, competencies=_competencies_json()),
            _profile(2, 1),  # владелец с полным доступом, но без компетенций
            _profile(3, 3, competencies=_competencies_json()),
        ],
        accounts={1: [full_account], 3: [full_account]},
    )

    ctxs = await scheduler._gather_profile_ctxs()  # noqa: SLF001

    by_id = {c.profile.id: c for c in ctxs}
    assert set(by_id) == {1, 2, 3}
    # Компетенции + опция есть -> LLM-скоринг допустим.
    assert by_id[1].scoring_allowed is True
    assert by_id[3].scoring_allowed is True
    # Опция есть, но компетенций нет -> профиль собирается без постановки на LLM.
    assert by_id[2].scoring_allowed is False


def _indexing_scheduler(
    app_config: Any, *, enabled: bool, okpd2_prefixes: list[str], excluded: list[str] | None = None
) -> Scheduler:
    cfg = app_config.model_copy(deep=True)
    cfg.service.indexing.enabled = enabled
    cfg.service.indexing.okpd2_prefixes = okpd2_prefixes
    if excluded is not None:
        cfg.service.indexing.excluded_platforms = excluded
    scheduler = Scheduler(cfg)
    scheduler._repository = _GatherRepo([])  # type: ignore[assignment]  # noqa: SLF001
    return scheduler


@pytest.mark.asyncio
async def test_gather_adds_system_index_ctx_when_enabled(app_config: Any) -> None:
    """Индексация включена и есть коды ОКПД2 -> системный профиль добавлен в обход."""
    scheduler = _indexing_scheduler(app_config, enabled=True, okpd2_prefixes=["62", "38"])

    ctxs = await scheduler._gather_profile_ctxs()  # noqa: SLF001

    assert len(ctxs) == 1
    ctx = ctxs[0]
    assert ctx.is_system_index is True
    assert ctx.keywords == []
    assert ctx.exclusion_words == []
    assert ctx.scoring_allowed is False
    assert ctx.profile.id < 0
    assert ctx.profile.user_id is None
    assert ctx.profile.okpd_codes == ["62", "38"]
    # Тестовый DOM-конфиг не содержит b2b_center — обе площадки идут в target_etp.
    assert set(ctx.profile.target_etp) == set(app_config.dom.platforms)


@pytest.mark.asyncio
async def test_gather_skips_system_index_ctx_when_disabled(app_config: Any) -> None:
    scheduler = _indexing_scheduler(app_config, enabled=False, okpd2_prefixes=["62"])
    assert await scheduler._gather_profile_ctxs() == []  # noqa: SLF001


@pytest.mark.asyncio
async def test_gather_skips_system_index_ctx_when_no_prefixes(app_config: Any) -> None:
    scheduler = _indexing_scheduler(app_config, enabled=True, okpd2_prefixes=[])
    assert await scheduler._gather_profile_ctxs() == []  # noqa: SLF001


@pytest.mark.asyncio
async def test_gather_system_index_ctx_excludes_configured_platforms(app_config: Any) -> None:
    platform_ids = list(app_config.dom.platforms)
    excluded = platform_ids[:1]
    scheduler = _indexing_scheduler(
        app_config, enabled=True, okpd2_prefixes=["62"], excluded=excluded
    )

    ctxs = await scheduler._gather_profile_ctxs()  # noqa: SLF001

    assert len(ctxs) == 1
    assert set(ctxs[0].profile.target_etp) == set(platform_ids) - set(excluded)


@pytest.mark.asyncio
async def test_gather_system_index_ctx_absent_when_all_platforms_excluded(app_config: Any) -> None:
    scheduler = _indexing_scheduler(
        app_config,
        enabled=True,
        okpd2_prefixes=["62"],
        excluded=list(app_config.dom.platforms),
    )
    assert await scheduler._gather_profile_ctxs() == []  # noqa: SLF001


@pytest.mark.asyncio
async def test_gather_profile_ctxs_only_ids_excludes_system_index(app_config: Any) -> None:
    """Целевой внеочередной обход (only_ids) — системный профиль в него не попадает."""
    scheduler = _indexing_scheduler(app_config, enabled=True, okpd2_prefixes=["62"])
    assert await scheduler._gather_profile_ctxs(only_ids=set()) == []  # noqa: SLF001


class _RebuildRepo:
    """Записывает аргументы вызова rebuild_profile_results (без реальной БД)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def rebuild_profile_results(
        self,
        profile: Any,
        keywords: list[str],
        exclusion_words: list[str],
        *,
        rescore: bool = False,
        use_document_index: bool = False,
    ) -> dict[str, int]:
        self.calls.append(
            {
                "profile_id": profile.id,
                "rescore": rescore,
                "use_document_index": use_document_index,
            }
        )
        return {"created": 0, "updated": 0, "removed": 0, "reset": 0}


def _rebuild_ctx(*, okpd_codes: list[str] | None) -> ProfileRunContext:
    profile = SimpleNamespace(id=42, okpd_codes=okpd_codes or [])
    return ProfileRunContext(profile=cast(Profile, profile), keywords=["слово"], exclusion_words=[])


@pytest.mark.asyncio
async def test_rebuild_profile_results_uses_document_index_when_enabled_and_scoped(
    app_config: Any,
) -> None:
    """Индексация включена + у профиля есть ОКПД2 — use_document_index=True."""
    cfg = app_config.model_copy(deep=True)
    cfg.service.indexing.enabled = True
    cfg.service.indexing.okpd2_prefixes = ["62", "38"]
    scheduler = Scheduler(cfg)
    repo = _RebuildRepo()
    scheduler._repository = repo  # type: ignore[assignment]  # noqa: SLF001

    await scheduler._rebuild_profile_results(  # noqa: SLF001
        _rebuild_ctx(okpd_codes=["62.01"])
    )

    assert repo.calls == [{"profile_id": 42, "rescore": False, "use_document_index": True}]


@pytest.mark.asyncio
async def test_rebuild_profile_results_skips_document_index_when_disabled(
    app_config: Any,
) -> None:
    cfg = app_config.model_copy(deep=True)
    cfg.service.indexing.enabled = False
    cfg.service.indexing.okpd2_prefixes = ["62"]
    scheduler = Scheduler(cfg)
    repo = _RebuildRepo()
    scheduler._repository = repo  # type: ignore[assignment]  # noqa: SLF001

    await scheduler._rebuild_profile_results(_rebuild_ctx(okpd_codes=["62.01"]))  # noqa: SLF001

    assert repo.calls[0]["use_document_index"] is False


@pytest.mark.asyncio
async def test_rebuild_profile_results_skips_document_index_when_profile_has_no_okpd(
    app_config: Any,
) -> None:
    cfg = app_config.model_copy(deep=True)
    cfg.service.indexing.enabled = True
    cfg.service.indexing.okpd2_prefixes = ["62"]
    scheduler = Scheduler(cfg)
    repo = _RebuildRepo()
    scheduler._repository = repo  # type: ignore[assignment]  # noqa: SLF001

    await scheduler._rebuild_profile_results(_rebuild_ctx(okpd_codes=[]))  # noqa: SLF001

    assert repo.calls[0]["use_document_index"] is False


# ===== Stage D: маршрутизация discovery через индекс ПО КОДУ ОКПД2 =====


def _index_ctx(
    *, okpd_codes: list[str] | None, is_system_index: bool = False, profile_id: int = 1
) -> ProfileRunContext:
    profile = SimpleNamespace(id=profile_id, okpd_codes=okpd_codes or [])
    return ProfileRunContext(
        profile=cast(Profile, profile),
        keywords=["слово"],
        exclusion_words=[],
        is_system_index=is_system_index,
    )


@pytest.mark.asyncio
async def test_split_ctxs_fully_covered_goes_to_index_sync_only(app_config: Any) -> None:
    """Все коды профиля покрыты индексом — только синхронизация из БД, живой обход не нужен."""
    scheduler = _indexing_scheduler(app_config, enabled=True, okpd2_prefixes=["62", "38"])
    ctx = _index_ctx(okpd_codes=["62.01", "38.11"])

    index_sync, live = scheduler._split_ctxs_for_index_routing([ctx])  # noqa: SLF001

    assert index_sync == [ctx]
    assert live == []


@pytest.mark.asyncio
async def test_split_ctxs_partially_covered_goes_to_both_with_narrowed_live_codes(
    app_config: Any,
) -> None:
    """Частичное покрытие: синхронизация из БД ПЛЮС узкий живой обход только по
    непокрытым кодам (не по всему профилю) — маршрутизация по коду, не по профилю."""
    scheduler = _indexing_scheduler(app_config, enabled=True, okpd2_prefixes=["62"])
    ctx = _index_ctx(okpd_codes=["62.01", "71.20"])

    index_sync, live = scheduler._split_ctxs_for_index_routing([ctx])  # noqa: SLF001

    assert index_sync == [ctx]  # полный профиль — rebuild_profile_results сам сверяет область
    assert len(live) == 1
    live_ctx = live[0]
    assert live_ctx is not ctx  # копия, исходный ctx не мутирован
    assert live_ctx.profile is ctx.profile
    assert live_ctx.crawl_okpd_codes == ["71.20"]
    assert ctx.crawl_okpd_codes is None


@pytest.mark.asyncio
async def test_split_ctxs_not_covered_at_all_goes_to_live_unmodified(app_config: Any) -> None:
    scheduler = _indexing_scheduler(app_config, enabled=True, okpd2_prefixes=["62"])
    ctx = _index_ctx(okpd_codes=["71.20"])

    index_sync, live = scheduler._split_ctxs_for_index_routing([ctx])  # noqa: SLF001

    assert index_sync == []
    assert live == [ctx]
    assert live[0].crawl_okpd_codes is None


@pytest.mark.asyncio
async def test_split_ctxs_without_okpd_codes_goes_to_live_unmodified(app_config: Any) -> None:
    scheduler = _indexing_scheduler(app_config, enabled=True, okpd2_prefixes=["62"])
    ctx = _index_ctx(okpd_codes=[])

    index_sync, live = scheduler._split_ctxs_for_index_routing([ctx])  # noqa: SLF001

    assert index_sync == []
    assert live == [ctx]


@pytest.mark.asyncio
async def test_split_ctxs_indexing_disabled_goes_to_live_unmodified(app_config: Any) -> None:
    scheduler = _indexing_scheduler(app_config, enabled=False, okpd2_prefixes=["62"])
    ctx = _index_ctx(okpd_codes=["62.01"])

    index_sync, live = scheduler._split_ctxs_for_index_routing([ctx])  # noqa: SLF001

    assert index_sync == []
    assert live == [ctx]


@pytest.mark.asyncio
async def test_split_ctxs_system_index_profile_always_live_unmodified(app_config: Any) -> None:
    """Системный индексный профиль всегда идёт живым обходом целиком — он и есть
    источник индекса."""
    scheduler = _indexing_scheduler(app_config, enabled=True, okpd2_prefixes=["62"])
    ctx = _index_ctx(okpd_codes=["62"], is_system_index=True)

    index_sync, live = scheduler._split_ctxs_for_index_routing([ctx])  # noqa: SLF001

    assert index_sync == []
    assert live == [ctx]
    assert live[0].crawl_okpd_codes is None


@pytest.mark.asyncio
async def test_sync_profiles_via_index_calls_rebuild_for_each(app_config: Any) -> None:
    scheduler = Scheduler(app_config.model_copy(deep=True))
    repo = _RebuildRepo()
    scheduler._repository = repo  # type: ignore[assignment]  # noqa: SLF001
    ctxs = [
        _index_ctx(okpd_codes=["62.01"], profile_id=1),
        _index_ctx(okpd_codes=["38"], profile_id=2),
    ]

    await scheduler._sync_profiles_via_index(ctxs)  # noqa: SLF001

    assert [c["profile_id"] for c in repo.calls] == [1, 2]
    assert all(c["rescore"] is False and c["use_document_index"] is True for c in repo.calls)


@pytest.mark.asyncio
async def test_sync_profiles_via_index_one_failure_does_not_stop_others(app_config: Any) -> None:
    class _FlakyRebuildRepo(_RebuildRepo):
        async def rebuild_profile_results(
            self,
            profile: Any,
            keywords: list[str],
            exclusion_words: list[str],
            *,
            rescore: bool = False,
            use_document_index: bool = False,
        ) -> dict[str, int]:
            if profile.id == 1:
                raise RuntimeError("db unavailable")
            return await super().rebuild_profile_results(
                profile,
                keywords,
                exclusion_words,
                rescore=rescore,
                use_document_index=use_document_index,
            )

    scheduler = Scheduler(app_config.model_copy(deep=True))
    repo = _FlakyRebuildRepo()
    scheduler._repository = repo  # type: ignore[assignment]  # noqa: SLF001
    ctxs = [
        _index_ctx(okpd_codes=["62.01"], profile_id=1),
        _index_ctx(okpd_codes=["38"], profile_id=2),
    ]

    await scheduler._sync_profiles_via_index(ctxs)  # noqa: SLF001

    assert [c["profile_id"] for c in repo.calls] == [2]


@pytest.mark.asyncio
async def test_run_once_routes_by_code_not_by_whole_profile(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверка на всех четырёх случаях сразу: профиль, полностью покрытый индексом,
    синхронизируется из БД и не обходится живьём вовсе; частично покрытый —
    синхронизируется И обходится живьём, но ТОЛЬКО по непокрытому остатку кодов
    (не по всему профилю); непокрытый и системный индексный — обычным живым обходом
    без сужения."""
    cfg = app_config.model_copy(deep=True)
    cfg.service.indexing.enabled = True
    cfg.service.indexing.okpd2_prefixes = ["62"]
    scheduler = _make_scheduler(cfg, max_concurrent=2)
    fully_covered = _index_ctx(okpd_codes=["62.01"], profile_id=1)
    partially_covered = _index_ctx(okpd_codes=["62.02", "71.20"], profile_id=2)
    not_covered = _index_ctx(okpd_codes=["71.20"], profile_id=3)
    system = _index_ctx(okpd_codes=["62"], is_system_index=True, profile_id=-1)

    async def fake_gather_ctxs() -> list[ProfileRunContext]:
        return [fully_covered, partially_covered, not_covered, system]

    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", fake_gather_ctxs)
    monkeypatch.setattr(scheduler, "_ordered_enabled_platforms", lambda enabled: ["p1"])
    monkeypatch.setattr(scheduler, "_profile_on_platform", lambda ctx, platform_id: True)

    live_calls: list[tuple[int, list[str] | None]] = []

    async def fake_process(
        platform_id: str,
        profiles: list[ProfileRunContext],
        iteration: int = 0,
        *,
        full_window: bool = False,
        cycle: object = None,
    ) -> None:
        live_calls.extend((c.profile.id, c.crawl_okpd_codes) for c in profiles)

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)

    sync_calls: list[int] = []

    async def fake_sync(ctxs: list[ProfileRunContext]) -> None:
        sync_calls.extend(c.profile.id for c in ctxs)

    monkeypatch.setattr(scheduler, "_sync_profiles_via_index", fake_sync)

    await scheduler.run_once()

    assert sorted(sync_calls) == [1, 2]  # fully_covered и partially_covered
    assert sorted(live_calls) == [
        (-1, None),  # системный индексный — целиком, без сужения
        (2, ["71.20"]),  # partially_covered — только непокрытый остаток
        (3, None),  # not_covered — целиком, индекс ему не подходит вовсе
    ]


def _ctx_with_etp(target_etp: list[str]) -> ProfileRunContext:
    profile = SimpleNamespace(target_etp=target_etp)
    return ProfileRunContext(profile=cast(Any, profile))


def test_profile_on_platform_empty_target_etp_matches_nothing(app_config: Any) -> None:
    """2026-09, решение пользователя: пустой target_etp — ни одной площадки
    (раньше означал «все» — форма профиля с пустым списком выглядела как
    «ничего не выбрано», что вводило в заблуждение)."""
    scheduler = Scheduler(app_config)
    ctx = _ctx_with_etp([])
    assert scheduler._profile_on_platform(ctx, "zakupki_mos") is False  # noqa: SLF001
    assert scheduler._profile_on_platform(ctx, "b2b_center") is False  # noqa: SLF001


def test_profile_on_platform_sentinel_matches_any_platform(app_config: Any) -> None:
    """ALL_PLATFORMS_SENTINEL — явное «все площадки», включая любую, не
    перечисленную в диапазоне (устойчиво к добавлению новых площадок)."""
    scheduler = Scheduler(app_config)
    ctx = _ctx_with_etp([ALL_PLATFORMS_SENTINEL])
    assert scheduler._profile_on_platform(ctx, "zakupki_mos") is True  # noqa: SLF001
    assert scheduler._profile_on_platform(ctx, "a-brand-new-platform") is True  # noqa: SLF001


def test_profile_on_platform_explicit_list_matches_only_listed(app_config: Any) -> None:
    scheduler = Scheduler(app_config)
    ctx = _ctx_with_etp(["zakupki_mos", "fabrikant"])
    assert scheduler._profile_on_platform(ctx, "zakupki_mos") is True  # noqa: SLF001
    assert scheduler._profile_on_platform(ctx, "fabrikant") is True  # noqa: SLF001
    assert scheduler._profile_on_platform(ctx, "b2b_center") is False  # noqa: SLF001
