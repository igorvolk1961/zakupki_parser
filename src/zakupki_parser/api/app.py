"""FastAPI-сервис: чтение закупок из БД, web-демо и управление парсером."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError

from zakupki_parser.auth import (
    ROLE_ADMIN,
    ROLE_TENDEROLOGIST,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from zakupki_parser.config.loader import load_config
from zakupki_parser.config.models import (
    SCORE_METHOD_FIT,
    SCORE_METHOD_MARGIN,
    SCORE_METHOD_PWIN,
    SCORE_METHOD_STAGES,
    AppConfig,
    ServiceConfig,
)
from zakupki_parser.notify import Notifier
from zakupki_parser.scoring import ScoringTransportClient
from zakupki_parser.storage.db import Database, Procurement, Profile, User
from zakupki_parser.storage.repository import ProcurementRepository, effective_is_active

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Схемы ответов
# --------------------------------------------------------------------------- #
class ProcurementOut(BaseModel):
    """Карточка закупки (без detail_json)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    platform_id: str
    platform_name: str | None = None
    platform_url: str | None = None
    url: str | None = None
    customer_id: int | None = None
    customer: str | None = None
    procedure_type_id: int | None = None
    procedure_type: str | None = None
    law: str | None = None
    subject: str | None = None
    nmck: float | None = None
    publication_date: datetime | None = None
    update_date: datetime | None = None
    deadline: datetime | None = None
    execution_term: str | None = None
    okpd2_codes: str | None = None
    kpgz_codes: str | None = None
    security_amount: float | None = None
    security_amount_unit: str | None = None
    files_json: list[dict[str, Any]] | None = None
    score: float | None = None
    fit_score: float | None = None
    p_win: float | None = None
    margin: float | None = None
    score_method: str | None = None
    embedding_similarity: float | None = None
    # Per-client RAG-отчёт анализа стоп-условий (профиль активного клиента).
    rag_report: dict[str, Any] | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class ProcurementDetailOut(ProcurementOut):
    """Карточка закупки с полным detail_json."""

    detail_json: dict[str, Any] | None = None


class ProcurementListOut(BaseModel):
    total: int
    items: list[ProcurementOut]


class CustomerOut(BaseModel):
    """Карточка заказчика (ADR-4)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    normalized_name: str
    inn: str | None = None
    rating: float | None = None
    created_at: datetime
    updated_at: datetime


class CustomerListOut(BaseModel):
    total: int
    items: list[CustomerOut]


class RatingUpdate(BaseModel):
    """Установка рейтинга заказчика внешним сервисом (ADR-4)."""

    rating: float


class ClearIrrelevantIn(BaseModel):
    """Удаление нерелевантных закупок: порог релевантности (fit_score)."""

    min_fit_score: float = 0.4


class ExportIn(BaseModel):
    """Выгрузка CSV: порог релевантности (fit_score), как в фильтре таблицы."""

    min_fit_score: float = 0.4


class HealthOut(BaseModel):
    status: str
    db: bool


# --------------------------------------------------------------------------- #
# Схемы авторизации
# --------------------------------------------------------------------------- #
class LoginIn(BaseModel):
    """Вход по логину и паролю (пока; позже — OAuth2 через Сбер ID)."""

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1)


class UserOut(BaseModel):
    """Карточка пользователя (без password_hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None = None
    role: str
    created_at: datetime
    updated_at: datetime


class RegisterIn(BaseModel):
    """Самостоятельная регистрация: пользователь сам выбирает пароль.

    Требуется подтверждение пароля (``password_confirm``). Роль при регистрации
    всегда ``tenderologist``; роль администратора регистрацией не выдаётся.
    """

    username: str = Field(min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8)
    password_confirm: str = Field(min_length=8)

    @model_validator(mode="after")
    def _passwords_match(self) -> RegisterIn:
        if self.password != self.password_confirm:
            raise ValueError("password_confirm не совпадает с password")
        return self


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class PromptUpdate(BaseModel):
    """Сохранение промпта scoring_service (вкладка «Промпты»)."""

    content: str


class ScoreUpdate(BaseModel):
    """Обновление score внешним сервисом (по его инициативе)."""

    score: float
    fit_score: float | None = None
    p_win: float | None = None
    margin: float | None = None
    score_method: str = SCORE_METHOD_FIT
    embedding_similarity: float | None = None
    rag_report: dict[str, Any] | None = None

    @field_validator("score_method")
    @classmethod
    def _score_method_known(cls, value: str) -> str:
        """Принимаем только известные результаты внешнего скоринга (ADR-7/ADR-8).

        Неизвестный метод (например, будущая стадия каскада, о которой парсер ещё
        не знает) раньше молча сохранялся в БД и «терялся» для фильтров и таблицы —
        теперь такой запрос отклоняется с 422.
        """
        if value not in SCORE_METHOD_STAGES:
            allowed = ", ".join(SCORE_METHOD_STAGES)
            raise ValueError(f"score_method должен быть одним из: {allowed}")
        return value


class ProcurementIdsIn(BaseModel):
    """Пакетная обработка выбранных закупок (on-demand)."""

    procurement_ids: list[int] = Field(min_length=1)


class ProfileIn(BaseModel):
    """Создание/обновление профиля фильтрации пользователя (ключ — user_id + name)."""

    name: str = Field(min_length=1, max_length=128)
    enabled: bool | None = None
    is_active: bool | None = None
    competencies: str | None = None
    keywords: list[str] | None = None
    exclusion_words: list[str] | None = None
    keyword_context_regexes: dict[str, str] | None = None
    questions: list[dict[str, Any]] | None = None
    target_etp: list[str] | None = None
    target_laws: list[str] | None = None
    min_fit_threshold: float | None = None


class ProfileOut(BaseModel):
    """Карточка профиля фильтрации."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    enabled: bool
    is_active: bool
    competencies: str
    keywords: list[str]
    exclusion_words: list[str]
    keyword_context_regexes: dict[str, str]
    questions: list[dict[str, Any]]
    target_etp: list[str]
    target_laws: list[str]
    min_fit_threshold: float | None = None
    created_at: datetime
    updated_at: datetime


class ProfileListOut(BaseModel):
    total: int
    items: list[ProfileOut]


# --------------------------------------------------------------------------- #
# Приложение
# --------------------------------------------------------------------------- #
class AppState:
    def __init__(self, cfg: AppConfig, configs_dir: str) -> None:
        self.cfg = cfg
        self.configs_dir = configs_dir
        self.db: Database | None = None
        self.repository: ProcurementRepository | None = None
        # Управление парсером (запуск/остановка из web-демо).
        self.parser_lock = asyncio.Lock()
        self.parser_task: asyncio.Task[None] | None = None
        self.parser_status: dict[str, Any] = {
            "running": False,
            "stopped": False,
            "error": None,
            "started_at": None,
            "finished_at": None,
        }
        # WebSocket-клиенты web-демо (живые обновления при изменении БД).
        self.ws_clients: set[WebSocket] = set()
        # Отложенное пороговое уведомление (ADR-7): Notifier + порог fit_score,
        # используется в POST /score.
        self.notifier: Notifier | None = None
        self.notify_min_fit_score: float = 0.0
        # Транспорт каскада скоринга: постановка задач следующих стадий (P(win)/Margin).
        self.score_transport: ScoringTransportClient | None = None


async def _broadcast(state: AppState, message: str = "data-changed") -> None:
    """Оповещает подключённых клиентов web-демо об изменении данных."""
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


def _prompt_file(state: AppState, name: str) -> Path:
    """Файл промпта: только существующий .md/.json внутри prompts_dir.

    Защита от path traversal: имя должно быть простым именем файла, а итоговый
    путь — прямым ребёнком prompts_dir (resolve + сравнение parent).
    """
    base = Path(state.cfg.ops.prompts_dir).resolve()
    candidate = (base / name).resolve()
    if candidate.parent != base:
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")
    if candidate.suffix not in (".md", ".json") or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Файл промпта не найден")
    return candidate


def _prompt_kind(name: str) -> str:
    """Тип промпта: json-файл (примеры) или markdown (текст промпта)."""
    return "json" if name.endswith(".json") else "markdown"


def _prompt_dir_rel(state: AppState) -> str:
    """Каталог промптов для вкладки: относительный путь от корня проекта.

    В Docker каталог вне корня проекта (например, /app/prompts) — показываем
    как есть (абсолютный и короткий путь).
    """
    base = Path(state.cfg.ops.prompts_dir)
    root = Path(state.configs_dir).parent
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


def create_app(configs_dir: str = "configs") -> FastAPI:
    state = _create_state(configs_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(state.cfg.ops.db)
        try:
            await db.connect()
            state.db = db
            state.repository = ProcurementRepository(db)
        except Exception as exc:  # noqa: BLE001
            logger.error("БД недоступна при старте API: %s", exc)
            state.db = None
            state.repository = None
        else:
            # Сид начального администратора — отдельный try: его сбой не должен
            # «ломать» общее состояние БД (иначе весь API уйдёт в 503).
            if state.cfg.ops.auth.enabled:
                try:
                    await _seed_initial_admin()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Не удалось создать начального администратора: %s", exc)
        yield
        if state.db is not None:
            await state.db.dispose()

    app = FastAPI(title="Zakupki Parser API", version="0.1.0", lifespan=lifespan)
    app.state.parser = state

    zakupki_html = Path(__file__).parent / "zakupki.html"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def demo() -> str:
        """Простое web-приложение для демонстрации MVP (читает данные через API)."""
        return zakupki_html.read_text(encoding="utf-8")

    def _repo() -> ProcurementRepository:
        if state.repository is None:
            raise HTTPException(status_code=503, detail="БД недоступна")
        return state.repository

    async def _ensure_service_account() -> User:
        """Сервис-аккаунт: первый пользователь (admin), осиротевшие профили — его.

        Используется в dev-режиме (auth off) и конвейером скоринга: профиль
        «активного клиента» теперь принадлежит пользователю (BR-07). Создаёт
        пользователя, если таблица пуста (env-сид ZAKUPKI_ADMIN_* или fallback
        «admin» со сгенерированным паролем), и присваивает профили без user_id.
        """
        user = await _repo().first_user()
        if user is not None:
            await _repo().backfill_orphaned_profiles(user.id)
        else:
            username = os.environ.get("ZAKUPKI_ADMIN_USERNAME") or "admin"
            password = os.environ.get("ZAKUPKI_ADMIN_PASSWORD") or secrets.token_urlsafe(24)
            user = await _repo().create_user(
                username, await asyncio.to_thread(hash_password, password), ROLE_ADMIN
            )
            logger.warning(
                "Создан сервис-аккаунт %s (пароль %s)",
                username,
                "из env" if os.environ.get("ZAKUPKI_ADMIN_PASSWORD") else "сгенерирован",
            )
            await _repo().backfill_orphaned_profiles(user.id)
        # Профиль default создаётся пустым (слова загружаются скриптом seed-profile, R8).
        await _repo().ensure_default_profile(user.id)
        return user

    async def _effective_user(user: User | None) -> User:
        """Текущий пользователь; при выключенной авторизации — сервис-аккаунт."""
        if user is not None:
            return user
        return await _ensure_service_account()

    async def _active_context(user: User | None) -> tuple[User, Profile]:
        """Эффективный пользователь и его активный профиль (BR-07).

        Оценки (procurement_evaluations) ключуются по ``user_id``, профиль —
        контекст фильтрации. Возвращает пару, чтобы не резолвить пользователя дважды.
        """
        eff_user = await _effective_user(user)
        profile = await _repo().get_active_profile(eff_user.id)
        if profile is None:
            raise HTTPException(
                status_code=503,
                detail="Активный профиль не найден (примените миграции)",
            )
        return eff_user, profile

    async def _profile_out(profile: Profile) -> ProfileOut:
        """Карточка профиля со словами из таблицы ``keywords`` (канонический источник)."""
        data = ProfileOut.model_validate(profile).model_dump()
        keywords = await _repo().get_profile_keywords(profile.id)
        data["keywords"] = keywords["keywords"]
        data["exclusion_words"] = keywords["exclusion_words"]
        return ProfileOut(**data)

    # ------------------------------------------------------------------ #
    # Авторизация (вход по логину/паролю; позже — OAuth2 через Сбер ID).
    # При auth.enabled=false зависимости пропускают запрос (dev-режим).
    # ------------------------------------------------------------------ #
    def _extract_bearer(request: Request) -> str | None:
        authz = request.headers.get("Authorization")
        if not authz or not authz.startswith("Bearer "):
            return None
        return authz[len("Bearer ") :].strip()

    async def require_user(request: Request) -> User | None:
        """Текущий пользователь по bearer-токену; None при выключенной авторизации."""
        if not state.cfg.ops.auth.enabled:
            return None
        token = _extract_bearer(request)
        if token is None:
            raise HTTPException(status_code=401, detail="Требуется авторизация")
        payload = decode_token(token, state.cfg.ops.auth.secret or "")
        if payload is None:
            raise HTTPException(status_code=401, detail="Недействительный или истёкший токен")
        user = await _repo().get_user(payload["sub"])
        if user is None:
            raise HTTPException(status_code=401, detail="Пользователь не найден")
        return user

    def require_admin(user: User | None = Depends(require_user)) -> User | None:
        """Только администратор; None при выключенной авторизации."""
        if user is None:
            return None
        if user.role != ROLE_ADMIN:
            raise HTTPException(status_code=403, detail="Требуется роль администратора")
        return user

    def require_internal(request: Request) -> None:
        """Доступ только для внутренних сервисов конвейера (по X-Internal-Token).

        Применяется к служебным эндпоинтам (POST /score, POST /customers/{id}/rating),
        которые вызывают компоненты конвейера, а не пользователи. Fail-closed:
        при включённой авторизации без заданного токена эндпоинты закрыты
        (конфиг-валидатор отклоняет такой запуск ещё на старте; ветка — страховка).
        """
        if not state.cfg.ops.auth.enabled:
            return
        internal = state.cfg.ops.auth.internal_token
        if not internal:
            raise HTTPException(
                status_code=503,
                detail="Внутренний токен конвейера не задан (ZAKUPKI_INTERNAL_TOKEN)",
            )
        if request.headers.get("X-Internal-Token") != internal:
            raise HTTPException(status_code=401, detail="Неверный внутренний токен")

    async def require_user_or_internal(request: Request) -> User | None:
        """Пользователь ИЛИ внутренний токен конвейера (например, /api/clients/active).

        Конвейер скоринга (scoring_service/analysis_service) не имеет пользовательского
        токена, но читает активный профиль клиента: внутренний токен пропускает запрос.
        """
        if not state.cfg.ops.auth.enabled:
            return None
        internal = state.cfg.ops.auth.internal_token
        if internal and request.headers.get("X-Internal-Token") == internal:
            return None
        return await require_user(request)

    def _auth_disabled() -> None:
        if not state.cfg.ops.auth.enabled:
            raise HTTPException(status_code=404, detail="Авторизация отключена")

    async def _seed_initial_admin() -> None:
        """Создаёт первого администратора из env, если таблица пользователей пуста.

        Удобно для первого развёртывания (Docker): задайте ZAKUPKI_ADMIN_USERNAME и
        ZAKUPKI_ADMIN_PASSWORD; при наличии пользователей env игнорируется.
        """
        username = os.environ.get("ZAKUPKI_ADMIN_USERNAME")
        password = os.environ.get("ZAKUPKI_ADMIN_PASSWORD")
        if not username or not password:
            return
        if await _repo().count_users() > 0:
            return
        await _repo().create_user(username, hash_password(password), ROLE_ADMIN)
        logger.info("Создан начальный администратор %s (из env)", username)

    # Уведомления подписчиков — отправляются в POST /score после прихода внешнего
    # скора и прохождения порога notify_min_fit_score (ADR-7).
    state.notifier = Notifier(state.cfg.ops.notifications)
    state.notify_min_fit_score = state.cfg.ops.notifications.notify_min_fit_score

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        db_ok = False
        if state.db is not None:
            try:
                async with state.db.session() as session:
                    await session.execute(sql_text("SELECT 1"))
                db_ok = True
            except Exception:  # noqa: BLE001
                db_ok = False
        return HealthOut(status="ok", db=db_ok)

    # ------------------------------------------------------------------ #
    # Авторизация: вход / выход / текущий пользователь / управление (admin)
    # ------------------------------------------------------------------ #
    @app.post("/api/auth/login", response_model=TokenOut)
    async def login(body: LoginIn) -> TokenOut:
        """Вход по логину и паролю: возвращает bearer-токен и профиль пользователя."""
        _auth_disabled()
        user = await _repo().get_user_by_username(body.username)
        # PBKDF2 (600k итераций) — CPU-bound: не блокируем event loop (~190 мс).
        ok = user is not None and await asyncio.to_thread(
            verify_password, body.password, user.password_hash
        )
        if user is None or not ok:
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")
        ttl = state.cfg.ops.auth.token_ttl_seconds
        token = create_token(user.id, user.role, state.cfg.ops.auth.secret or "", ttl)
        logger.info("Вход пользователя %s (роль %s)", user.username, user.role)
        return TokenOut(access_token=token, expires_in=ttl, user=UserOut.model_validate(user))

    @app.post("/api/auth/logout", include_in_schema=False)
    async def logout(user: User | None = Depends(require_user)) -> dict[str, str]:
        """Выход (stateless: клиент удаляет токен; серверная сессия не ведётся)."""
        _auth_disabled()
        return {"status": "ok"}

    @app.get("/api/auth/me", response_model=UserOut)
    async def me(user: User | None = Depends(require_user)) -> UserOut:
        """Текущий пользователь. 404 — авторизация отключена (клиент не логинится)."""
        if user is None:
            raise HTTPException(status_code=404, detail="Авторизация отключена")
        return UserOut.model_validate(user)

    @app.post("/api/auth/register", response_model=TokenOut)
    async def register(body: RegisterIn) -> TokenOut:
        """Самостоятельная регистрация: пользователь сам выбирает пароль.

        Роль при регистрации всегда ``tenderologist``. Роль администратора
        регистрацией не выдаётся — её задаёт администратор системы (env-сид
        ``ZAKUPKI_ADMIN_USERNAME``/``ZAKUPKI_ADMIN_PASSWORD`` при первом старте
        либо правка таблицы ``users``).
        """
        _auth_disabled()
        if await _repo().get_user_by_username(body.username) is not None:
            raise HTTPException(status_code=409, detail="Пользователь с таким логином уже есть")
        password_hash = await asyncio.to_thread(hash_password, body.password)
        try:
            user = await _repo().create_user(
                body.username, password_hash, ROLE_TENDEROLOGIST, email=body.email
            )
        except IntegrityError as exc:
            # Гонка двух одновременных регистраций с одним логином: констрейнт
            # uq_users_username срабатывает позже pre-check — отдаём 409, а не 500.
            raise HTTPException(
                status_code=409, detail="Пользователь с таким логином уже есть"
            ) from exc
        # Каждому новому пользователю — активный профиль default (BR-07): без него
        # список закупок недоступен (нет контекста фильтрации). Профиль создаётся
        # пустым — ключевые слова/компетенции загружаются скриптом seed-profile (R8).
        await _repo().seed_default_profile(
            user.id,
            {
                "name": "default",
                "enabled": True,
                "is_active": True,
                "competencies": "",
                "keywords": [],
                "exclusion_words": [],
                "keyword_context_regexes": {},
                "questions": [],
            },
        )
        ttl = state.cfg.ops.auth.token_ttl_seconds
        token = create_token(user.id, user.role, state.cfg.ops.auth.secret or "", ttl)
        logger.info("Зарегистрирован пользователь %s (роль %s)", user.username, user.role)
        return TokenOut(access_token=token, expires_in=ttl, user=UserOut.model_validate(user))

    @app.get(
        "/api/procurements",
        response_model=ProcurementListOut,
        dependencies=[Depends(require_user)],
    )
    async def list_procurements(
        number: str | None = None,
        platform_id: str | None = None,
        okpd2: str | None = None,
        customer: str | None = None,
        active: bool | None = None,
        min_fit_score: float | None = None,
        scored: bool | None = None,
        sort: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        user: User | None = Depends(require_user),
    ) -> ProcurementListOut:
        # Per-user скоринг эффективного пользователя (мультитенантность, BR-07).
        eff_user, _ = await _active_context(user)
        rows, total = await _repo().list_procurements(
            number=number,
            platform_id=platform_id,
            okpd2=okpd2,
            customer=customer,
            active=active,
            min_fit_score=min_fit_score,
            scored=scored,
            sort=sort,
            limit=limit,
            offset=offset,
            user_id=eff_user.id,
        )
        return ProcurementListOut(total=total, items=[_procurement_out(r) for r in rows])

    # Плоские колонки для CSV-выгрузки (без detail_json/files_json).
    CSV_COLUMNS = [
        "id",
        "number",
        "platform_id",
        "url",
        "customer",
        "procedure_type",
        "law",
        "subject",
        "nmck",
        "publication_date",
        "update_date",
        "deadline",
        "execution_term",
        "okpd2_codes",
        "kpgz_codes",
        "security_amount",
        "security_amount_unit",
        "advance",
        "score",
        "fit_score",
        "p_win",
        "margin",
        "score_method",
        "is_active",
    ]

    @app.post(
        "/api/procurements/export",
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def export_procurements(
        body: ExportIn | None = None, user: User | None = Depends(require_user)
    ) -> dict[str, Any]:
        """Выгружает активные релевантные закупки из БД в CSV (каталог export_dir).

        В выгрузку попадают ТОЛЬКО активные (по статусу и сроку актуальности) и
        релевантные (прошедшие внешний скоринг с fit_score >= порога) закупки —
        как фильтр «Только релевантные» в таблице закупок. Порог по умолчанию 0.4.

        Файл пишется в ``config_service.yaml -> export_dir`` (создаётся при
        необходимости). Операция read-only — безопасна при работающем парсере.
        """
        threshold = body.min_fit_score if body is not None else 0.4
        eff_user, _ = await _active_context(user)
        rows, _ = await _repo().list_procurements(
            active=True,
            min_fit_score=threshold,
            limit=10**9,
            user_id=eff_user.id,
        )

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = _procurement_out(row).model_dump()
            for col in ("publication_date", "update_date", "deadline"):
                if isinstance(out.get(col), datetime):
                    out[col] = out[col].isoformat()
            writer.writerow(out)

        export_dir = Path(state.cfg.ops.export_dir)
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
            target = export_dir / "procurements.csv"
            target.write_bytes(buf.getvalue().encode("utf-8-sig"))
        except OSError as exc:
            logger.error("Не удалось записать CSV %s: %s", export_dir, exc)
            raise HTTPException(status_code=500, detail=f"Не удалось выгрузить CSV: {exc}") from exc

        logger.info("Выгружено закупок в CSV: %s -> %s", len(rows), target)
        return {"status": "exported", "count": len(rows), "path": str(target)}

    @app.get(
        "/api/procurements/{procurement_id}",
        response_model=ProcurementDetailOut,
        dependencies=[Depends(require_user)],
    )
    async def get_procurement(
        procurement_id: int, user: User | None = Depends(require_user)
    ) -> ProcurementDetailOut:
        eff_user, _ = await _active_context(user)
        row = await _repo().get_by_id(procurement_id, user_id=eff_user.id)
        if row is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        return _procurement_detail_out(row)

    @app.post(
        "/api/procurements/{procurement_id}/score",
        response_model=ProcurementDetailOut,
        dependencies=[Depends(require_internal)],
    )
    async def set_score(procurement_id: int, body: ScoreUpdate) -> ProcurementDetailOut:
        """Обновление score внешним сервисом по его инициативе.

        Результат пишется в per-user скоринг (``procurement_evaluations``) сервис-аккаунта;
        базовые колонки ``procurements`` обновляются для совместимости (дефолтный скор).
        Автокаскад Fit -> P(win) -> Margin отключён: P(win)/Margin вычисляются только по
        явному запросу тендеролога.

        RAG-отчёт (``rag_report``) сохраняется отдельно и не меняет score_method.
        Уведомляет подписчиков ПОСЛЕ стадии (fit/pwin/margin), когда результат
        стадии изменён и прошёл её порог (ADR-7).
        """
        existing = await _repo().get_by_id(procurement_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Закупка не найдена")
        # Внутренний вызов конвейера: результат пишется под сервис-аккаунт (BR-07).
        eff_user, _ = await _active_context(None)
        if body.rag_report is not None:
            # Анализ стоп-условий: сохраняем отчёт, результат скоринга не меняем.
            await _repo().update_rag_report(procurement_id, eff_user.id, body.rag_report)
        else:
            await _repo().upsert_score(
                procurement_id,
                eff_user.id,
                score=body.score,
                fit_score=body.fit_score,
                p_win=body.p_win,
                margin=body.margin,
                score_method=body.score_method,
            )
        # Базовые колонки — дефолтный скор (совместимость, ветка sim/эмбеддинги).
        await _repo().update_score(
            procurement_id,
            body.score,
            body.fit_score,
            body.score_method,
            embedding_similarity=body.embedding_similarity,
            p_win=body.p_win,
            margin=body.margin,
        )
        await _broadcast(state)
        row = await _repo().get_by_id(procurement_id, user_id=eff_user.id)
        if row is None:  # pragma: no cover - проверено выше
            raise HTTPException(status_code=404, detail="Закупка не найдена")

        # Уведомление после стадии: только когда результат стадии изменён
        # (не повторная доставка) и возвращаемое значение прошло её порог.
        stage_changed = existing.score_method != row.score_method
        if (
            stage_changed
            and state.notifier is not None
            and _meets_stage_notify_threshold(row, state)
        ):
            await state.notifier.notify(_row_to_record(row))
        return _procurement_detail_out(row)

    @app.post(
        "/api/procurements/analyze",
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def analyze_procurements(
        body: ProcurementIdsIn, user: User | None = Depends(require_user)
    ) -> dict[str, Any]:
        """Обработать выбранные закупки: авто-Fit (если нет) + RAG-анализ ТЗ.

        Внутренние стадии скрыты от заказчика: для каждой закупки ставится
        задание fit (если per-user fit ещё не посчитан) и затем analysis.
        Ручная корректировка оценок — вне MVP (Эпик 5, пост-MVP).
        """
        if state.score_transport is None:
            raise HTTPException(status_code=409, detail="Транспорт скоринга не настроен")
        eff_user, _ = await _active_context(user)
        queued: list[int] = []
        for procurement_id in body.procurement_ids:
            current = await _repo().get_score(procurement_id, eff_user.id)
            if current is None or current.fit_score is None:
                await _enqueue_next_stage(state, procurement_id, "fit", 0.5)
            await _enqueue_next_stage(state, procurement_id, "analysis", 0.5)
            queued.append(procurement_id)
        logger.info("Поставлено на обработку (fit+analysis): %s", queued)
        return {"status": "queued", "procurement_ids": queued}

    @app.post(
        "/api/procurements/pwin-margin",
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def pwin_margin_procurements(
        body: ProcurementIdsIn, user: User | None = Depends(require_user)
    ) -> dict[str, Any]:
        """Оценить P(win) и Margin для выбранных закупок (on-demand, обе стадии)."""
        if state.score_transport is None:
            raise HTTPException(status_code=409, detail="Транспорт скоринга не настроен")
        cfg = state.cfg.score
        await _active_context(user)
        queued: list[int] = []
        for procurement_id in body.procurement_ids:
            if cfg.pwin_enabled:
                await _enqueue_next_stage(state, procurement_id, "pwin", 0.5)
            if cfg.margin_enabled:
                await _enqueue_next_stage(state, procurement_id, "margin", 0.5)
            queued.append(procurement_id)
        logger.info("Поставлено на оценку P(win)/Margin: %s", queued)
        return {"status": "queued", "procurement_ids": queued}

    # --- Профили фильтрации (tenant-скоуп BR-07; пути /api/clients — для совместимости) ---
    @app.get(
        "/api/clients/active",
        response_model=ProfileOut,
        dependencies=[Depends(require_user_or_internal)],
    )
    async def active_client(
        user: User | None = Depends(require_user_or_internal),
    ) -> ProfileOut:
        """Активный профиль эффективного пользователя (внутренний токен — сервис-аккаунт)."""
        _, profile = await _active_context(user)
        return await _profile_out(profile)

    @app.get(
        "/api/clients",
        response_model=ProfileListOut,
        dependencies=[Depends(require_user)],
    )
    async def list_clients(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        user: User | None = Depends(require_user),
    ) -> ProfileListOut:
        eff_user = await _effective_user(user)
        rows, total = await _repo().list_profiles(user_id=eff_user.id, limit=limit, offset=offset)
        return ProfileListOut(total=total, items=[await _profile_out(r) for r in rows])

    @app.get(
        "/api/clients/{client_id}",
        response_model=ProfileOut,
        dependencies=[Depends(require_user)],
    )
    async def get_client(client_id: int, user: User | None = Depends(require_user)) -> ProfileOut:
        eff_user = await _effective_user(user)
        row = await _repo().get_profile(eff_user.id, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        return await _profile_out(row)

    @app.post(
        "/api/clients",
        response_model=ProfileOut,
        dependencies=[Depends(require_user)],
    )
    async def create_client(
        body: ProfileIn, user: User | None = Depends(require_user)
    ) -> ProfileOut:
        eff_user = await _effective_user(user)
        return await _profile_out(
            await _repo().upsert_profile(body.model_dump(exclude_none=True), eff_user.id)
        )

    @app.put(
        "/api/clients/{client_id}",
        response_model=ProfileOut,
        dependencies=[Depends(require_user)],
    )
    async def update_client(
        client_id: int, body: ProfileIn, user: User | None = Depends(require_user)
    ) -> ProfileOut:
        eff_user = await _effective_user(user)
        existing = await _repo().get_profile(eff_user.id, client_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        data = body.model_dump(exclude_none=True)
        data["name"] = existing.name if body.name == existing.name else body.name
        updated = await _repo().upsert_profile(data, eff_user.id)
        return await _profile_out(updated)

    @app.post(
        "/api/clients/{client_id}/activate",
        response_model=ProfileOut,
        dependencies=[Depends(require_user)],
    )
    async def activate_client(
        client_id: int, user: User | None = Depends(require_user)
    ) -> ProfileOut:
        """Делает профиль активным (per-user состояние; остальные деактивируются)."""
        eff_user = await _effective_user(user)
        try:
            profile = await _repo().set_active_profile(eff_user.id, client_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await _profile_out(profile)

    @app.get(
        "/api/customers",
        response_model=CustomerListOut,
        dependencies=[Depends(require_user)],
    )
    async def list_customers(
        name: str | None = None,
        inn: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> CustomerListOut:
        rows, total = await _repo().list_customers(name=name, inn=inn, limit=limit, offset=offset)
        return CustomerListOut(total=total, items=[CustomerOut.model_validate(r) for r in rows])

    @app.get(
        "/api/customers/{customer_id}",
        response_model=CustomerOut,
        dependencies=[Depends(require_user)],
    )
    async def get_customer(customer_id: int) -> CustomerOut:
        row = await _repo().get_customer(customer_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Заказчик не найден")
        return CustomerOut.model_validate(row)

    @app.post(
        "/api/customers/{customer_id}/rating",
        response_model=CustomerOut,
        dependencies=[Depends(require_internal)],
    )
    async def set_customer_rating(customer_id: int, body: RatingUpdate) -> CustomerOut:
        """Установка рейтинга заказчика внешним сервисом (ADR-4)."""
        if not await _repo().set_customer_rating(customer_id, body.rating):
            raise HTTPException(status_code=404, detail="Заказчик не найден")
        row = await _repo().get_customer(customer_id)
        return CustomerOut.model_validate(row)

    @app.websocket("/ws")
    async def ws_updates(websocket: WebSocket) -> None:
        """Канал живых обновлений: шлёт 'data-changed' при изменении БД.

        При включённой авторизации токен передаётся query-параметром ``?token=``
        (браузер не может задать заголовок WebSocket-запроса).
        """
        if state.cfg.ops.auth.enabled:
            token = websocket.query_params.get("token")
            payload = decode_token(token or "", state.cfg.ops.auth.secret or "")
            if payload is None or await _repo().get_user(payload["sub"]) is None:
                await websocket.close(code=1008)
                return
        await websocket.accept()
        state.ws_clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            state.ws_clients.discard(websocket)

    @app.get("/api/parser/status", include_in_schema=False, dependencies=[Depends(require_user)])
    async def parser_status() -> dict[str, Any]:
        """Текущее состояние парсера (запущен/остановлен, ошибка, время)."""
        status = dict(state.parser_status)
        if state.parser_task is not None and not state.parser_task.done():
            status["running"] = True
        return status

    @app.post("/api/parser/start", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def start_parser() -> dict[str, Any]:
        """Запускает постоянный мониторинг парсера (периодические проходы) в фоне."""
        async with state.parser_lock:
            if state.parser_task is not None and not state.parser_task.done():
                raise HTTPException(status_code=409, detail="Парсер уже запущен")
            state.parser_status = {
                "running": True,
                "stopped": False,
                "error": None,
                "started_at": datetime.now(UTC).isoformat(),
                "finished_at": None,
            }
            state.parser_task = asyncio.create_task(_run_parser(state))
        logger.info("Запущен парсер (постоянный мониторинг) по команде из web-демо")
        return {"status": "started"}

    @app.post("/api/parser/stop", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def stop_parser() -> dict[str, Any]:
        """Останавливает запущенный проход парсера."""
        task = state.parser_task
        if task is None or task.done():
            return {"status": "idle"}
        task.cancel()
        logger.info("Запрошена остановка парсера из web-демо")
        return {"status": "stopping"}

    @app.post("/api/db/clear", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def clear_db() -> dict[str, Any]:
        """Очищает БД (закупки и заказчики). Доступно только при остановленном парсере."""
        if state.parser_task is not None and not state.parser_task.done():
            raise HTTPException(status_code=409, detail="Остановите парсер перед очисткой БД")
        deleted = await _repo().clear_all()
        logger.info("БД очищена из web-демо: %s", deleted)
        await _broadcast(state)
        return {"status": "cleared", "deleted": deleted}

    @app.post(
        "/api/db/clear-inactive",
        include_in_schema=False,
        dependencies=[Depends(require_admin)],
    )
    async def clear_inactive() -> dict[str, Any]:
        """Удаляет неактивные закупки (is_active=false или истёкший срок актуальности).

        Клиентская операция: активность учитывает текущую дату, как в фильтре
        ``active``. Доступно только при остановленном парсере.
        """
        if state.parser_task is not None and not state.parser_task.done():
            raise HTTPException(status_code=409, detail="Остановите парсер перед очисткой БД")
        deleted = await _repo().delete_inactive()
        logger.info("Удалены неактивные закупки из web-демо: %s", deleted)
        await _broadcast(state)
        return {"status": "cleared", "deleted": deleted}

    @app.post(
        "/api/db/clear-irrelevant",
        include_in_schema=False,
        dependencies=[Depends(require_admin)],
    )
    async def clear_irrelevant(
        body: ClearIrrelevantIn | None = None, user: User | None = Depends(require_user)
    ) -> dict[str, Any]:
        """Удаляет нерелевантные закупки среди обработанных сервисом скоринга.

        Учитываются только записи с score_method=external и fit_score < порога.
        Записи без внешнего скоринга не затрагиваются. Доступно только при
        остановленном парсере.
        """
        if state.parser_task is not None and not state.parser_task.done():
            raise HTTPException(status_code=409, detail="Остановите парсер перед очисткой БД")
        threshold = body.min_fit_score if body is not None else 0.4
        eff_user, _ = await _active_context(user)
        deleted = await _repo().delete_irrelevant(threshold, user_id=eff_user.id)
        logger.info("Удалены нерелевантные закупки из web-демо: %s", deleted)
        await _broadcast(state)
        return {"status": "cleared", "deleted": deleted}

    # ------------------------------------------------------------------ #
    # Конфигурация сервиса (config_service.yaml) — просмотр/редактирование
    # ------------------------------------------------------------------ #
    @app.get(
        "/api/config/threshold",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def get_relevance_threshold() -> dict[str, Any]:
        """Порог релевантности (fit_score) — используется переключателем «Только релевантные».

        Значение берётся из config_ops.yaml (notifications.notify_min_fit_score),
        эксплуатационные параметры целиком через API не отдаются.
        """
        return {"notify_min_fit_score": state.cfg.ops.notifications.notify_min_fit_score}

    @app.get(
        "/api/config",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def get_config() -> dict[str, Any]:
        """Текущие параметры config_service.yaml (аналитические настройки).

        Секреты и эксплуатационные параметры (БД, уведомления, таймер) живут в
        config_ops.yaml и не редактируются через этот API.
        """
        return state.cfg.service.model_dump()

    @app.put(
        "/api/config",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_admin)],
    )
    async def put_config(body: dict[str, Any]) -> dict[str, Any]:
        """Валидирует и сохраняет аналитические параметры config_service.yaml.

        Эксплуатационные параметры (БД, уведомления, секреты) не редактируются
        через API — они живут в config_ops.yaml и берутся из env.
        """
        try:
            new_service = ServiceConfig.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        target = Path(state.configs_dir) / "config_service.yaml"
        try:
            target.write_text(
                yaml.safe_dump(
                    new_service.model_dump(exclude_none=True),
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            detail = f"Не удалось записать конфиг: {exc}"
            raise HTTPException(status_code=500, detail=detail) from exc
        state.cfg.service = new_service
        logger.info("Сохранён config_service.yaml (%s)", target)
        return new_service.model_dump()

    # ------------------------------------------------------------------ #
    # Промпты scoring_service — просмотр/редактирование (вкладка «Промпты»)
    # ------------------------------------------------------------------ #
    @app.get(
        "/api/prompts",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def list_prompts() -> dict[str, Any]:
        """Список файлов промптов (md/json) для вкладки «Промпты»."""
        base = Path(state.cfg.ops.prompts_dir)
        files: list[dict[str, str]] = []
        if base.is_dir():
            for path in sorted(base.iterdir()):
                if path.is_file() and path.suffix in (".md", ".json"):
                    files.append({"name": path.name, "kind": _prompt_kind(path.name)})
        return {"files": files, "dir": _prompt_dir_rel(state)}

    @app.get(
        "/api/prompts/{name}",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_user)],
    )
    async def get_prompt(name: str) -> dict[str, Any]:
        """Содержимое файла промпта."""
        path = _prompt_file(state, name)
        return {
            "name": path.name,
            "kind": _prompt_kind(path.name),
            "content": path.read_text(encoding="utf-8"),
            "dir": _prompt_dir_rel(state),
        }

    @app.put(
        "/api/prompts/{name}",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_admin)],
    )
    async def put_prompt(name: str, body: PromptUpdate) -> dict[str, Any]:
        """Сохраняет промпт; JSON-файлы проверяются на корректность до записи."""
        path = _prompt_file(state, name)
        if path.suffix == ".json":
            try:
                json.loads(body.content)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail=f"Некорректный JSON: {exc}") from exc
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(body.content, encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Не удалось сохранить промпт: {exc}"
            ) from exc
        logger.info("Сохранён промпт %s", path)
        return {
            "name": path.name,
            "kind": _prompt_kind(path.name),
            "content": body.content,
            "dir": _prompt_dir_rel(state),
        }

    return app
