"""Unit-тесты параллельной обработки площадок (Scheduler.run_once, R5/4B)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from zakupki_parser.options import paid_default_options
from zakupki_parser.scheduler import Scheduler
from zakupki_parser.storage.db import UserAccount


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
    ) -> None:
        called.append(platform_id)

    async def no_ctxs() -> list[object]:
        return []

    monkeypatch.setattr(scheduler, "_gather_profile_ctxs", no_ctxs)
    monkeypatch.setattr(scheduler, "_process_platform", fake_process)

    await scheduler.run_once()

    assert called == []


class _FakeProfileCtx:
    """Профиль-контекст для внеочередного обхода (нужны ``id`` и ``profile.id``)."""

    def __init__(self, profile_id: int) -> None:
        self.id = profile_id
        self.profile = SimpleNamespace(id=profile_id)


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

    Debounce сбрасывается от КАЖДОГО сигнала (trailing): повторные правки в
    пределах окна не накапливают старые таймеры, а продлевают ожидание.
    """
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    assert not scheduler._refresh_ids  # noqa: SLF001
    assert not scheduler._refresh_event.is_set()  # noqa: SLF001
    assert scheduler._refresh_pending_since is None  # noqa: SLF001

    scheduler.request_profile_refresh(7)
    since = scheduler._refresh_pending_since  # noqa: SLF001
    assert scheduler._refresh_ids == {7}  # noqa: SLF001
    assert scheduler._refresh_event.is_set()  # noqa: SLF001

    # Повторный сигнал того же профиля продлевает окно от последнего сигнала.
    scheduler.request_profile_refresh(7)
    assert scheduler._refresh_ids == {7}  # noqa: SLF001
    assert scheduler._refresh_event.is_set()  # noqa: SLF001
    assert scheduler._refresh_pending_since >= since  # noqa: SLF001

    # Другой профиль в том же батче также продлевает окно.
    scheduler.request_profile_refresh(8)
    assert scheduler._refresh_ids == {7, 8}  # noqa: SLF001
    assert scheduler._refresh_pending_since >= since  # noqa: SLF001


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
    assert scheduler._refresh_handled_in_cycle == {7}  # noqa: SLF001
    assert calls == [
        ("p1", [7], 5, True),
        ("p2", [7], 5, True),
    ]


@pytest.mark.asyncio
async def test_run_refresh_pass_skips_profile_handled_this_cycle(
    app_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повторная правка того же профиля за регулярный цикл не запускает новый
    полный обход: один full-window проход на профиль за цикл (кап нагрузки)."""
    scheduler = _make_scheduler(app_config, max_concurrent=2)
    calls: list[tuple[str, list[int], int, bool]] = []
    gathers: list[set[int] | None] = []

    async def fake_process(
        platform_id: str,
        profiles: object,
        iteration: int = 0,
        *,
        full_window: bool = False,
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

    scheduler.request_profile_refresh(7)
    await scheduler._run_refresh_pass(iteration=1)  # noqa: SLF001
    assert len(calls) == 1

    # Вторая правка того же профиля в этом же регулярном цикле.
    scheduler.request_profile_refresh(7)
    await scheduler._run_refresh_pass(iteration=2)  # noqa: SLF001

    assert len(calls) == 1  # повторный полный обход не запускается
    assert gathers == [{7}]  # вторая правка не доходит даже до сбора контекста
    assert scheduler._refresh_ids == {7}  # noqa: SLF001  # остаётся до регулярного прохода
    assert scheduler._refresh_handled_in_cycle == {7}  # noqa: SLF001


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
    ) -> None:
        called.append(platform_id)

    monkeypatch.setattr(scheduler, "_process_platform", fake_process)

    await scheduler.run_once()

    assert called == []


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
    await asyncio.sleep(0)
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
    # Пользователь 1 — легаси без аккаунтов (полный доступ к платным опциям).
    # Пользователь 3 — активный аккаунт со всеми платными опциями.
    full_account = UserAccount(
        user_id=3,
        name="full",
        options=paid_default_options(True),
        is_active=True,
    )
    scheduler._repository = _GatherRepo(  # type: ignore[assignment]  # noqa: SLF001
        [
            _profile(1, 1, competencies=_competencies_json()),
            _profile(2, 1),  # легаси-владелец с полным доступом, но без компетенций
            _profile(3, 3, competencies=_competencies_json()),
        ],
        accounts={3: [full_account]},
    )

    ctxs = await scheduler._gather_profile_ctxs()  # noqa: SLF001

    by_id = {c.profile.id: c for c in ctxs}
    assert set(by_id) == {1, 2, 3}
    # Компетенции + опция есть -> LLM-скоринг допустим.
    assert by_id[1].scoring_allowed is True
    assert by_id[3].scoring_allowed is True
    # Опция есть, но компетенций нет -> профиль собирается без постановки на LLM.
    assert by_id[2].scoring_allowed is False
