"""Конвертеры карточек (закупка/профиль) и хелперы конфигурации/промптов.

Выделено из прежнего монолитного ``api/app.py``: функции, которые не зависят от
FastAPI-запросов и используются несколькими роутерами.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from zakupki_parser.api.app.schemas import ProcurementDetailOut, ProcurementOut
from zakupki_parser.api.app.state import AppState
from zakupki_parser.config.models import (
    SCORE_METHOD_FIT,
    SCORE_METHOD_MARGIN,
    SCORE_METHOD_PWIN,
    OpsConfig,
    ServiceConfig,
)
from zakupki_parser.storage.db import Procurement
from zakupki_parser.storage.repository import effective_is_active


def _service_config_public(service: ServiceConfig) -> dict[str, Any]:
    """Сериализация config_service.yaml для веб-редактора.

    ``search_criteria`` хранит только ``active_only`` и ``deadline_not_expired``:
    критерии ОКПД2/НМЦК/слова задаются активным профилем (таблица ``keywords``,
    канонический источник), глобальный конфиг их не содержит.
    """
    data = service.model_dump()
    scoring = data.get("scoring")
    if isinstance(scoring, dict):
        scoring.pop("scoring_transport_token", None)
    sc = data.get("search_criteria")
    if isinstance(sc, dict):
        data["search_criteria"] = {
            "active_only": bool(sc.get("active_only", False)),
            "deadline_not_expired": bool(sc.get("deadline_not_expired", True)),
        }
    return data


def _ops_config_public(ops: OpsConfig) -> dict[str, Any]:
    """Сериализация config_ops.yaml для веб-редактора (без секретов из env).

    Секреты (auth.secret, auth.internal_token, токены бэкендов уведомлений)
    хранятся только в env — в форму и в YAML не попадают.
    """
    data: dict[str, Any] = ops.model_dump()
    auth = data.get("auth")
    if isinstance(auth, dict):
        auth.pop("secret", None)
        auth.pop("internal_token", None)
    notif = data.get("notifications")
    if isinstance(notif, dict):
        for block in ("telegram", "max", "webhook"):
            item = notif.get(block)
            if isinstance(item, dict):
                item.pop("token", None)
    return data


def _prompt_file(base: Path, name: str) -> Path:
    """Файл промпта: только существующий .md/.json внутри prompts_dir.

    Защита от path traversal: имя должно быть простым именем файла, а итоговый
    путь — прямым ребёнком prompts_dir (resolve + сравнение parent).
    """
    base = base.resolve()
    candidate = (base / name).resolve()
    if candidate.parent != base:
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")
    if candidate.suffix not in (".md", ".json") or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Файл промпта не найден")
    return candidate


def _prompt_kind(name: str) -> str:
    """Тип промпта: json-файл (примеры) или markdown (текст промпта)."""
    return "json" if name.endswith(".json") else "markdown"


def _prompt_dir_rel(base: Path, state: AppState) -> str:
    """Каталог промптов для вкладки: относительный путь от корня проекта.

    В Docker каталог вне корня проекта (например, /app/prompts) — показываем
    как есть (абсолютный и короткий путь).
    """
    root = Path(state.configs_dir).resolve().parent
    try:
        return str(base.relative_to(root))
    except ValueError:
        return str(base)


def _procurement_out(row: Procurement) -> ProcurementOut:
    """Карточка закупки с именем заказчика (имя — из связи customers, не колонки)."""
    out = ProcurementOut.model_validate(row)
    out.customer_id = row.customer_id
    out.customer = row.customer_rel.name if row.customer_rel is not None else None
    out.procedure_type_id = row.procedure_type_id
    out.procedure_type = row.procedure_type_rel.name if row.procedure_type_rel is not None else None
    out.platform_id = row.platform_id
    out.platform_name = row.platform_rel.name if row.platform_rel is not None else None
    out.platform_url = row.platform_rel.url if row.platform_rel is not None else None
    # Клиентская сторона: активность учитывает текущую дату (срок актуальности).
    out.is_active = effective_is_active(row.is_active, row.deadline)
    return out


def _procurement_detail_out(row: Procurement) -> ProcurementDetailOut:
    out = ProcurementDetailOut.model_validate(row)
    out.customer_id = row.customer_id
    out.customer = row.customer_rel.name if row.customer_rel is not None else None
    out.procedure_type_id = row.procedure_type_id
    out.procedure_type = row.procedure_type_rel.name if row.procedure_type_rel is not None else None
    out.platform_id = row.platform_id
    out.platform_name = row.platform_rel.name if row.platform_rel is not None else None
    out.platform_url = row.platform_rel.url if row.platform_rel is not None else None
    out.is_active = effective_is_active(row.is_active, row.deadline)
    return out


def _row_to_record(row: Procurement) -> dict[str, Any]:
    """Карточка закупки как dict для уведомлений (поля, понятные Notifier)."""
    return {
        "number": row.number,
        "platform_id": row.platform_id,
        "url": row.url,
        "customer": row.customer_rel.name if row.customer_rel is not None else None,
        "procedure_type": (
            row.procedure_type_rel.name if row.procedure_type_rel is not None else None
        ),
        "law": row.law,
        "subject": row.subject,
        "nmck": row.nmck,
        "publication_date": row.publication_date,
        "deadline": row.deadline,
        "score": row.score,
        "fit_score": row.fit_score,
        "p_win": row.p_win,
        "margin": row.margin,
        "score_method": row.score_method,
        "embedding_similarity": row.embedding_similarity,
        "is_active": effective_is_active(row.is_active, row.deadline),
    }


def _meets_stage_notify_threshold(row: Procurement, state: Any) -> bool:
    """Порог уведомления по возвращаемому значению стадии (не по score-произведению).

    Уведомление отправляется ПОСЛЕ КАЖДОЙ стадии каскада (fit → pwin → margin):
    каждая стадия имеет собственный порог по своему возвращаемому значению.
    Стадия целиком выключается флагом ``notify_fit_enabled``/``notify_pwin_enabled``/
    ``notify_margin_enabled`` (при false уведомление после стадии не отправляется вовсе).
    """
    if row.score_method == SCORE_METHOD_FIT:
        if not state.cfg.ops.notifications.notify_fit_enabled:
            return False
        return row.fit_score is not None and row.fit_score >= state.notify_min_fit_score
    if row.score_method == SCORE_METHOD_PWIN:
        if not state.cfg.ops.notifications.notify_pwin_enabled:
            return False
        return row.p_win is not None and row.p_win >= state.cfg.ops.notifications.notify_min_pwin
    if row.score_method == SCORE_METHOD_MARGIN:
        if not state.cfg.ops.notifications.notify_margin_enabled:
            return False
        return (
            row.margin is not None and row.margin >= state.cfg.ops.notifications.notify_min_margin
        )
    return False
