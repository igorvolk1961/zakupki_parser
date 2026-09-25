"""Фоновый пересчёт условий отчётных полей профиля без LLM.

После сохранения профиля условия полей (``scoring_common.conditions``) и
вердикт приемлемости пересчитываются по уже извлечённым значениям во всех
отчётах профиля (``EvaluationMixin.recheck_profile_conditions``) — повторный
LLM-анализ для правки условия или флага «блокирует» не нужен.

Пересчёт идёт фоновой задачей API-процесса: у профиля могут быть тысячи
отчётов. Ход виден через ``GET /api/clients/{id}/recheck`` (сколько отчётов
пересчитано из скольких) — пользователь понимает, что происходит. Новая правка
того же профиля отменяет незавершённый пересчёт и запускает его заново.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from scoring_common.sources.matching import source_contexts
from zakupki_parser.api.app.source_links import with_source_meta
from zakupki_parser.api.app.state import AppState, _broadcast

logger = logging.getLogger(__name__)


@dataclass
class RecheckStatus:
    """Состояние пересчёта условий одного профиля."""

    profile_id: int
    running: bool = True
    total: int = 0
    done: int = 0
    stale: int = 0
    started_at: str = ""
    finished_at: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def recheck_status(state: AppState, profile_id: int) -> RecheckStatus | None:
    return state.condition_rechecks.get(profile_id)


def start_condition_recheck(
    state: AppState,
    profile: Any,
    *,
    snapshot_from: datetime | None,
    keep_fresh: bool,
) -> RecheckStatus:
    """Запускает (перезапускает) пересчёт условий по отчётам профиля.

    ``keep_fresh`` — правка не затронула ничего, что требует повторного
    анализа (регионы, лицензии и т.п.): полностью пересчитанные отчёты,
    актуальные до правки, остаются актуальными.
    """
    previous = state.condition_recheck_tasks.get(profile.id)
    if previous is not None and not previous.done():
        previous.cancel()
    status = RecheckStatus(profile_id=profile.id, started_at=datetime.now(UTC).isoformat())
    state.condition_rechecks[profile.id] = status
    task = asyncio.create_task(
        _run(
            state,
            status,
            list(profile.report_fields or []),
            dict(profile.requirement_severity or {}),
            snapshot_from=snapshot_from if keep_fresh else None,
            snapshot_to=profile.updated_at if keep_fresh else None,
        )
    )
    state.condition_recheck_tasks[profile.id] = task
    return status


async def _run(
    state: AppState,
    status: RecheckStatus,
    field_defs: list[dict[str, Any]],
    requirement_severity: dict[str, Any],
    *,
    snapshot_from: datetime | None,
    snapshot_to: datetime | None,
) -> None:
    repo = state.repository
    if repo is None:
        status.running = False
        status.error = "БД недоступна"
        return

    def progress(done: int, total: int) -> None:
        status.done, status.total = done, total

    try:
        # Условия со значением-сайтом проверяются по тексту сайта из хранилища.
        defs = await with_source_meta(state, field_defs)
        sources = await asyncio.to_thread(source_contexts, defs)
        # Опыт профиля — для правила BR-03 (способ подтверждения опыта).
        facts = await repo.get_profile_facts(status.profile_id)
        stats = await repo.recheck_profile_conditions(
            status.profile_id,
            defs,
            requirement_severity,
            experience_codes=list(facts.get("experience_codes") or []),
            soft_pwin_factor=state.cfg.service.scoring.soft_pwin_factor,
            snapshot_from=snapshot_from,
            snapshot_to=snapshot_to,
            on_progress=progress,
            sources=sources,
        )
        status.stale = stats["stale"]
        logger.info(
            "Условия профиля %s пересчитаны без LLM: %d отчётов, %d требуют повторного анализа",
            status.profile_id,
            stats["rechecked"],
            stats["stale"],
        )
    except asyncio.CancelledError:
        status.error = "перезапущен после новой правки профиля"
        raise
    except Exception as exc:  # noqa: BLE001 — сбой пересчёта не должен ронять API
        logger.exception("Пересчёт условий профиля %s не выполнен", status.profile_id)
        status.error = str(exc)
    finally:
        status.running = False
        status.finished_at = datetime.now(UTC).isoformat()
    await _broadcast(state)
