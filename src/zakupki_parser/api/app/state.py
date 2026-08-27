"""Состояние API-приложения и фоновые операции парсера.

Выделено из прежнего монолитного ``api/app.py``: ``AppState``, создание состояния,
широковещательная рассылка WebSocket-клиентам и запуск/остановка парсера.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket

from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import AppConfig
from zakupki_parser.notify import Notifier
from zakupki_parser.scoring import ScoringTransportClient
from zakupki_parser.storage.db import Database, User
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


class AppState:
    def __init__(self, cfg: AppConfig, configs_dir: str) -> None:
        self.cfg = cfg
        self.configs_dir = configs_dir
        self.db: Database | None = None
        self.repository: ProcurementRepository | None = None
        # Кеш сервис-аккаунта (первый пользователь): backfill осиротевших профилей и
        # сид default-профиля выполняются один раз, а не на каждый запрос.
        self.service_account: User | None = None
        # Управление парсером (запуск/остановка из web-интерфейса).
        self.parser_lock = asyncio.Lock()
        self.parser_task: asyncio.Task[None] | None = None
        self.parser_status: dict[str, Any] = {
            "running": False,
            "stopped": False,
            "error": None,
            "started_at": None,
            "finished_at": None,
        }
        # WebSocket-клиенты web-интерфейса (живые обновления при изменении БД).
        self.ws_clients: set[WebSocket] = set()
        # Отложенное пороговое уведомление (ADR-7): Notifier + порог fit_score,
        # используется в POST /score.
        self.notifier: Notifier | None = None
        self.notify_min_fit_score: float = 0.0
        # Транспорт каскада скоринга: постановка задач следующих стадий (P(win)/Margin).
        self.score_transport: ScoringTransportClient | None = None


async def _broadcast(state: AppState, message: str = "data-changed") -> None:
    """Оповещает подключённых клиентов web-интерфейса об изменении данных."""
    for ws in list(state.ws_clients):
        try:
            await ws.send_text(message)
        except Exception:  # noqa: BLE001
            state.ws_clients.discard(ws)


async def _run_parser(state: AppState) -> None:
    """Запускает постоянный мониторинг парсера (периодические проходы) в фоне."""
    from zakupki_parser.scheduler import Scheduler

    scheduler = Scheduler(state.cfg, on_update=lambda: _broadcast(state))
    try:
        await scheduler.run_service()
    except asyncio.CancelledError:
        # Остановка по команде пользователя — это не ошибка.
        state.parser_status["stopped"] = True
        state.parser_status["error"] = None
    except Exception as exc:  # noqa: BLE001
        state.parser_status["error"] = str(exc)
    finally:
        with suppress(Exception):
            await scheduler.stop()
        await _broadcast(state)
        state.parser_status["running"] = False
        state.parser_status["finished_at"] = datetime.now(UTC).isoformat()
        state.parser_task = None


async def _enqueue_next_stage(
    state: AppState, procurement_id: int, stage: str, priority: float
) -> bool:
    """Поставить задачу следующей стадии каскада через транспорт (best-effort).

    Возвращает True, если транспорт настроен и постановка выполнена. Постановка
    идемпотентна: повторная доставка результата той же стадии не дублирует задачу
    (ZADD по одному члену очереди). Ошибки постановки не роняют обработчик — они
    обрабатываются как «каскад не продолжился» (fallback-уведомление в set_score).
    """
    if state.score_transport is None:
        logger.warning(
            "Транспорт не настроен: следующая стадия %s для закупки %s не поставлена",
            stage,
            procurement_id,
        )
        return False
    try:
        await state.score_transport.enqueue(procurement_id, priority, stage=stage)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Не удалось поставить задание стадии %s для закупки %s: %s",
            stage,
            procurement_id,
            exc,
        )
        return False


def _create_state(configs_dir: str) -> AppState:
    cfg = load_config(configs_dir)
    state = AppState(cfg, configs_dir)
    if cfg.score.scoring_transport_url:
        state.score_transport = ScoringTransportClient(cfg.score.scoring_transport_url)
    return state
