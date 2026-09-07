"""Unit-тесты сигнала планировщику о внеочередном обходе профиля (fast-start)."""

from __future__ import annotations

from typing import Any

from zakupki_parser.api.app.state import AppState, _request_profile_refresh


class _FakeScheduler:
    """Заглушка планировщика: записывает запрошенные id профилей и флаги."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, bool, bool]] = []

    def request_profile_refresh(
        self,
        profile_id: int,
        *,
        rebuild: bool = False,
        rescore: bool = False,
    ) -> None:
        self.calls.append((profile_id, rebuild, rescore))


def test_request_profile_refresh_forwards_to_running_scheduler(app_config: Any) -> None:
    state = AppState(app_config, "configs")
    scheduler = _FakeScheduler()
    state.parser_scheduler = scheduler

    _request_profile_refresh(state, 7)

    assert scheduler.calls == [(7, False, False)]
    assert state.pending_profile_refresh_ids == set()


def test_request_profile_refresh_forwards_rebuild_and_rescore(app_config: Any) -> None:
    state = AppState(app_config, "configs")
    scheduler = _FakeScheduler()
    state.parser_scheduler = scheduler

    _request_profile_refresh(state, 7, rebuild=True, rescore=True)

    assert scheduler.calls == [(7, True, True)]


def test_request_profile_refresh_queued_while_parser_stopped(app_config: Any) -> None:
    """Сигнал при остановленном парсере сохраняется и будет передан при старте."""
    state = AppState(app_config, "configs")
    assert state.parser_scheduler is None

    _request_profile_refresh(state, 7)
    _request_profile_refresh(state, 7)  # дубликаты схлопываются

    assert state.pending_profile_refresh_ids == {7}
    assert state.pending_profile_rebuild_ids == set()
    assert state.pending_profile_rescore_ids == set()


def test_request_profile_refresh_queues_rebuild_and_rescore_flags(app_config: Any) -> None:
    state = AppState(app_config, "configs")

    _request_profile_refresh(state, 7, rebuild=True, rescore=True)

    assert state.pending_profile_refresh_ids == {7}
    assert state.pending_profile_rebuild_ids == {7}
    assert state.pending_profile_rescore_ids == {7}
