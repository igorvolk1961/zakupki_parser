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
from zakupki_parser.storage.db import Database
from zakupki_parser.storage.repository import ProcurementRepository

logger = logging.getLogger(__name__)


class AppState:
    def __init__(self, cfg: AppConfig, configs_dir: str) -> None:
        self.cfg = cfg
        self.configs_dir = configs_dir
        # Порт API-сервиса (парсер является тем же процессом): используется при
        # рестарте фоновых сервисов скоринга как URL парсера (для их env).
        self.parser_port: int = 8000
        self.db: Database | None = None
        self.repository: ProcurementRepository | None = None
        # Управление парсером (запуск/остановка из web-интерфейса).
        self.parser_lock = asyncio.Lock()
        self.parser_task: asyncio.Task[None] | None = None
        # Активный экземпляр Scheduler (пока запущен постоянный мониторинг):
        # API-роуты просят внеочередной обход профиля через request_profile_refresh.
        self.parser_scheduler: Any | None = None
        # Профили, для которых запрошен внеочередной обход, пока парсер остановлен
        # (parser_scheduler is None): передаются новому планировщику при старте.
        self.pending_profile_refresh_ids: set[int] = set()
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


def _request_profile_refresh(state: AppState, profile_id: int) -> None:
    """Просит планировщик выполнить внеочередной обход профиля (fast-start).

    Вызывается после создания/изменения включённого профиля: планировщик обработает
    профиль сразу после завершения текущего прохода, не дожидаясь конца периода
    цикла (timeout_seconds). Если парсер остановлен/перезапускается — запрос
    сохраняется в ``pending_profile_refresh_ids`` и передаётся планировщику при
    старте (``_run_parser``).
    """
    scheduler = state.parser_scheduler
    if scheduler is not None:
        scheduler.request_profile_refresh(profile_id)
    else:
        state.pending_profile_refresh_ids.add(profile_id)


def _spawn_parser(state: AppState) -> None:
    """Запускает постоянный мониторинг парсера в фоне и обновляет статус.

    Общая точка старта для кнопки на панели devops и автозапуска при старте
    веб-сервиса (``auto_start_monitoring`` в config_ops.yaml).
    """
    state.parser_status = {
        "running": True,
        "stopped": False,
        "error": None,
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": None,
    }
    state.parser_task = asyncio.create_task(_run_parser(state))


async def _run_parser(state: AppState) -> None:
    """Запускает постоянный мониторинг парсера (периодические проходы) в фоне."""
    from zakupki_parser.scheduler import Scheduler

    scheduler = Scheduler(state.cfg, on_update=lambda: _broadcast(state))
    state.parser_scheduler = scheduler
    # Запросы на внеочередной обход, сделанные пока парсер был остановлен,
    # передаём новому планировщику (fast-start после «настроил профиль -> запустил»).
    pending = list(state.pending_profile_refresh_ids)
    state.pending_profile_refresh_ids.clear()
    for profile_id in pending:
        scheduler.request_profile_refresh(profile_id)
    try:
        await scheduler.run_service()
    except asyncio.CancelledError:
        # Остановка по команде пользователя — это не ошибка.
        state.parser_status["stopped"] = True
        state.parser_status["error"] = None
    except Exception as exc:  # noqa: BLE001
        state.parser_status["error"] = str(exc)
    finally:
        state.parser_scheduler = None
        with suppress(Exception):
            await scheduler.stop()
        await _broadcast(state)
        state.parser_status["running"] = False
        state.parser_status["finished_at"] = datetime.now(UTC).isoformat()
        state.parser_task = None


async def _enqueue_next_stage(
    state: AppState, procurement_id: int, stage: str, priority: float, profile_id: int
) -> bool:
    """Поставить задачу следующей стадии каскада через транспорт (best-effort).

    ``profile_id`` — профиль, для которого это on-demand действие (пер-профильно,
    BR-07). Возвращает True, если транспорт настроен и постановка выполнена.
    Постановка идемпотентна: повторная доставка результата той же стадии не
    дублирует задачу (ZADD по одному члену очереди). Ошибки постановки не роняют
    обработчик — они обрабатываются как «каскад не продолжился».
    """
    if state.score_transport is None:
        logger.warning(
            "Транспорт не настроен: следующая стадия %s для закупки %s не поставлена",
            stage,
            procurement_id,
        )
        return False
    try:
        await state.score_transport.enqueue(
            procurement_id, priority, stage=stage, profile_id=profile_id
        )
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
        state.score_transport = ScoringTransportClient(
            cfg.score.scoring_transport_url, auth_token=cfg.ops.auth.internal_token
        )
    return state
