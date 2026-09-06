"""Unit-тесты сигнала планировщику о внеочередном обходе профиля (fast-start)."""

from __future__ import annotations

from typing import Any

from zakupki_parser.api.app.state import AppState, _request_profile_refresh


class _FakeScheduler:
    """Заглушка планировщика: записывает запрошенные id профилей."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def request_profile_refresh(self, profile_id: int) -> None:
        self.calls.append(profile_id)


def test_request_profile_refresh_forwards_to_running_scheduler(app_config: Any) -> None:
    state = AppState(app_config, "configs")
    scheduler = _FakeScheduler()
    state.parser_scheduler = scheduler

    _request_profile_refresh(state, 7)

    assert scheduler.calls == [7]


def test_request_profile_refresh_noop_without_scheduler(app_config: Any) -> None:
    state = AppState(app_config, "configs")
    assert state.parser_scheduler is None

    _request_profile_refresh(state, 7)  # парсер остановлен — ничего не делает
