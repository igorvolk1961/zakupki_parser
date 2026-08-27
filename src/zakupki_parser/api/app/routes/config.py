"""Эндпоинты конфигурации: config_service.yaml (аналитик), config_ops.yaml,
config_log.yaml, config_parser.yaml (devops) и редактор промптов.

Вкладки:
- «Параметры мониторинга» (аналитик) — config_service.yaml (форма + расширенный
  режим YAML);
- «Промпты» (аналитик) — файлы промптов сервисов;
- «Конфигурация» (devops) — config_ops.yaml (форма + расширенный режим YAML);
- «Управление логами» (devops) — config_log.yaml (форма + расширенный режим YAML);
- «Парсер» (devops) — config_parser.yaml (форма + расширенный режим YAML).

Секреты (auth.secret, токены бэкендов) в YAML не пишутся и в форме не
редактируются — они управляются через env. Включение авторизации
(``auth.enabled``) также управляется через env (ZAKUPKI_AUTH_ENABLED) и через
API не меняется: иначе devops мог бы отключить авторизацию для всего сервиса.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from zakupki_parser.api.app.config_schema import build_schema
from zakupki_parser.api.app.converters import (
    _ops_config_public,
    _prompt_dir_rel,
    _prompt_file,
    _prompt_kind,
    _service_config_public,
)
from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import PlatformOut, PlatformsListOut, PromptUpdate
from zakupki_parser.api.app.state import AppState
from zakupki_parser.config.models import LoggingConfig, OpsConfig, ParserConfig, ServiceConfig

logger = logging.getLogger(__name__)


def _register_prompt_routes(
    router: APIRouter,
    prefix: str,
    base: Path,
    state: AppState,
    service_name: str,
    require_analyst: Callable[..., Any],
) -> None:
    """Список/чтение/сохранение файлов промптов одного каталога (вкладка «Промпты»)."""

    @router.get(
        f"/api/{prefix}",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_analyst)],
    )
    async def list_prompts() -> dict[str, Any]:
        """Список файлов промптов (md/json) для вкладки «Промпты»."""
        files: list[dict[str, str]] = []
        if base.is_dir():
            for path in sorted(base.iterdir()):
                if path.is_file() and path.suffix in (".md", ".json"):
                    files.append({"name": path.name, "kind": _prompt_kind(path.name)})
        return {"files": files, "dir": _prompt_dir_rel(base, state)}

    @router.get(
        f"/api/{prefix}/{{name}}",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_analyst)],
    )
    async def get_prompt(name: str) -> dict[str, Any]:
        """Содержимое файла промпта."""
        path = _prompt_file(base, name)
        return {
            "name": path.name,
            "kind": _prompt_kind(path.name),
            "content": path.read_text(encoding="utf-8"),
            "dir": _prompt_dir_rel(base, state),
        }

    @router.put(
        f"/api/{prefix}/{{name}}",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_analyst)],
    )
    async def put_prompt(name: str, body: PromptUpdate) -> dict[str, Any]:
        """Сохраняет промпт; JSON-файлы проверяются на корректность до записи."""
        path = _prompt_file(base, name)
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
        logger.info("Сохранён промпт %s (%s)", path, service_name)
        return {
            "name": path.name,
            "kind": _prompt_kind(path.name),
            "content": body.content,
            "dir": _prompt_dir_rel(base, state),
        }


async def _read_payload(request: Request) -> dict[str, Any]:
    """Тело PUT конфигурации: JSON-объект (форма) или raw YAML (расширенный режим)."""
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        body = await request.json()
    else:
        raw = (await request.body()).decode("utf-8")
        try:
            body = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            raise HTTPException(status_code=422, detail=f"Некорректный YAML: {exc}") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Ожидается объект конфигурации")
    return body


def _write_yaml(target: Path, data: dict[str, Any]) -> None:
    try:
        target.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Не удалось записать конфиг: {exc}") from exc


def _raw_yaml(state: AppState, filename: str) -> dict[str, Any]:
    target = Path(state.configs_dir) / filename
    return {"yaml": target.read_text(encoding="utf-8") if target.is_file() else ""}


def _register_config_endpoints(
    router: APIRouter,
    *,
    state: AppState,
    api_path: str,
    schema_path: str,
    raw_path: str | None,
    filename: str,
    model: type[BaseModel],
    public: Callable[[Any], dict[str, Any]],
    require: Callable[..., Any],
    state_setter: Callable[[Any], None],
    schema_options: dict[str, list[Any]] | None = None,
    schema_options_resolver: (
        Callable[[], Awaitable[dict[str, list[dict[str, str]]]]] | None
    ) = None,
    schema_transform: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    prepare: Callable[[dict[str, Any]], None] | None = None,
    validate: Callable[[Any], None] | None = None,
    read_only: bool = False,
) -> None:
    """GET/PUT (форма + raw YAML) и схема для одного конфига.

    ``prepare`` — модификация тела ДО валидации (например, подмешивание env-секретов);
    ``validate`` — пост-валидационная проверка (например, запрет смены auth.enabled);
    ``public`` — сериализация для формы/YAML без секретов.
    """

    @router.get(
        api_path,
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require)],
    )
    async def get_config() -> dict[str, Any]:
        """Текущие параметры конфигурации (для веб-формы)."""
        return public(_current())

    def _current() -> Any:
        if model is ServiceConfig:
            return state.cfg.service
        if model is OpsConfig:
            return state.cfg.ops
        if model is LoggingConfig:
            return state.cfg.logging
        return state.cfg.parser

    @router.get(
        schema_path,
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require)],
    )
    async def get_config_schema() -> dict[str, Any]:
        """Схема конфигурации для веб-формы."""
        options = schema_options
        if schema_options_resolver is not None:
            options = {**(options or {}), **(await schema_options_resolver())}
        schema = build_schema(model, options)
        if schema_transform is not None:
            schema = schema_transform(schema)
        return {"schema": schema}

    if raw_path is not None:

        @router.get(
            raw_path,
            response_model=dict[str, Any],
            include_in_schema=False,
            dependencies=[Depends(require)],
        )
        async def get_config_raw() -> dict[str, Any]:
            """Сырой YAML конфигурации для «Расширенного режима»."""
            return _raw_yaml(state, filename)

    if not read_only:

        @router.put(
            api_path,
            response_model=dict[str, Any],
            include_in_schema=False,
            dependencies=[Depends(require)],
        )
        async def put_config(request: Request) -> dict[str, Any]:
            """Валидирует и сохраняет конфигурацию (JSON-форма или raw YAML)."""
            body = await _read_payload(request)
            if prepare is not None:
                prepare(body)
            try:
                new_model = model.model_validate(body)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors()) from exc
            if validate is not None:
                validate(new_model)
            target = Path(state.configs_dir) / filename
            _write_yaml(target, public(new_model))
            state_setter(new_model)
            # Активность площадок (sites.enabled) синхронизируем в справочник
            # platforms: БД — источник истины, конфиг — редактируемый интерфейс.
            if isinstance(new_model, ServiceConfig) and state.repository is not None:
                enabled = {s.platform_id for s in new_model.sites if s.enabled}
                await state.repository.sync_platform_enabled(enabled)
            logger.info("Сохранён %s (%s)", filename, target)
            return public(new_model)


def _service_schema_transform(schema: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Кастомизация схемы config_service.yaml для формы «Параметры мониторинга».

    - sites: таблица без подписи списка, колонки platform_id (ключ),
      название/URL (из справочника platforms) и «Активная» (enabled);
    - default_cutoff_days: короткая подпись «Интервал дат в днях», пояснение —
      во всплывающую подсказку (title);
    - search_criteria: в одну колонку (stack);
    - прочие комментарии полей (``description``) переезжают в tooltip'ы.
    """
    for field in schema:
        if field.get("key") == "sites" and field.get("kind") == "list":
            field["label"] = ""
            field["addable"] = False
            item: list[dict[str, Any]] = []
            for sub in field["item"]:
                if sub["key"] == "platform_id":
                    sub["label"] = "Площадка"
                    sub["description"] = ""
                    sub["kind"] = "str"
                    sub["plain"] = True
                    item.append(sub)
            item.extend(
                [
                    {
                        "key": "name",
                        "kind": "str",
                        "label": "Название",
                        "description": "",
                        "default": None,
                        "required": False,
                        "derived": "platform_id",
                        "field": "name",
                    },
                    {
                        "key": "url",
                        "kind": "str",
                        "label": "URL",
                        "description": "",
                        "default": None,
                        "required": False,
                        "derived": "platform_id",
                        "field": "url",
                    },
                ]
            )
            for sub in field["item"]:
                if sub["key"] == "enabled":
                    sub["label"] = "Активная"
                    item.append(sub)
            field["item"] = item
        elif field.get("key") == "default_cutoff_days":
            field["label"] = "Интервал дат в днях"
            field["description"] = (
                "Интервал обрабатываемых дат обновления закупок при первом "
                "обращении к площадке в днях. При последующих обращениях "
                "обрабатываются только последние не обработанные даты, включая "
                "дату последней обработанной закупки"
            )
            field["inline"] = True
        elif field.get("key") == "search_criteria" and field.get("kind") == "object":
            field["stack"] = True
            field["fields"] = [
                sub
                for sub in field["fields"]
                if sub["key"] in ("active_only", "deadline_not_expired")
            ]
            for sub in field["fields"]:
                if sub["key"] == "active_only":
                    sub["label"] = "Поиск только активных закупок"
                    sub["description"] = (
                        "Поиск только активных закупок. Активность определяется "
                        "только состоянием закупки на площадке, без учета дат."
                    )
                elif sub["key"] == "deadline_not_expired":
                    sub["label"] = "Не обрабатывать закупку, если срок приёма заявок истёк"
    return schema


def build_config_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    require_base = ctx.require_base
    require_analyst = ctx.require_analyst
    require_devops = ctx.require_devops

    @router.get(
        "/api/config/threshold",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_base)],
    )
    async def get_relevance_threshold() -> dict[str, Any]:
        """Порог релевантности (fit_score) — переключатель «Только релевантные».

        Значение берётся из config_ops.yaml (notifications.notify_min_fit_score).
        """
        return {"notify_min_fit_score": state.cfg.ops.notifications.notify_min_fit_score}

    @router.get(
        "/api/platforms",
        response_model=PlatformsListOut,
        dependencies=[Depends(require_base)],
    )
    async def list_platforms_endpoint() -> PlatformsListOut:
        """Справочник площадок из БД (ключ/название/URL/активность).

        Источник истины — таблица ``platforms``; активность синхронизируется из
        config_service.yaml при старте и сохранении конфигурации.
        """
        repo = state.repository
        if repo is None:
            return PlatformsListOut(items=[])
        rows = await repo.list_platforms()
        return PlatformsListOut(
            items=[
                PlatformOut(
                    platform_id=r["value"],
                    name=r["name"],
                    url=r["url"],
                    enabled=r["enabled"],
                )
                for r in rows
            ]
        )

    # --- config_service.yaml: «Параметры мониторинга» (аналитик) ----------
    # Список площадок для формы берётся из БД (справочник platforms); конфиги
    # участвуют только в начальном сиде таблицы при инициализации БД.
    async def _service_platform_options() -> dict[str, list[dict[str, str]]]:
        repo = state.repository
        if repo is None:
            return {"sites.platform_id": []}
        return {"sites.platform_id": await repo.list_platforms()}

    _register_config_endpoints(
        router,
        state=state,
        api_path="/api/config",
        schema_path="/api/config/service/schema",
        raw_path="/api/config/service/raw",
        filename="config_service.yaml",
        model=ServiceConfig,
        public=_service_config_public,
        require=require_analyst,
        state_setter=lambda m: setattr(state.cfg, "service", m),
        schema_options_resolver=_service_platform_options,
        schema_transform=_service_schema_transform,
    )

    # --- config_ops.yaml: «Конфигурация» (devops) ------------------------
    def _prepare_ops(body: dict[str, Any]) -> None:
        """Перед валидацией подмешиваем env-секреты (auth.enabled=true требует secret)."""
        current = state.cfg.ops
        auth = body.setdefault("auth", {})
        auth.setdefault("secret", current.auth.secret)
        auth.setdefault("internal_token", current.auth.internal_token)
        notif = body.setdefault("notifications", {})
        for key, current_block in (
            ("telegram", current.notifications.telegram),
            ("max", current.notifications.max),
            ("webhook", current.notifications.webhook),
        ):
            block = notif.setdefault(key, {})
            block.setdefault("token", current_block.token)

    def _validate_ops(new_ops: OpsConfig) -> None:
        """Запрет смены включения авторизации через API (управляется env)."""
        if new_ops.auth.enabled != state.cfg.ops.auth.enabled:
            raise HTTPException(
                status_code=409,
                detail="Включение авторизации управляется через env (ZAKUPKI_AUTH_ENABLED)",
            )

    _register_config_endpoints(
        router,
        state=state,
        api_path="/api/config/ops",
        schema_path="/api/config/ops/schema",
        raw_path="/api/config/ops/raw",
        filename="config_ops.yaml",
        model=OpsConfig,
        public=_ops_config_public,
        require=require_devops,
        state_setter=lambda m: setattr(state.cfg, "ops", m),
        prepare=_prepare_ops,
        validate=_validate_ops,
    )

    # --- config_log.yaml: «Управление логами» (devops) ---------------------
    def _validate_log(new_log: LoggingConfig) -> None:
        """Путь файла лога — только относительный (без выхода за корень проекта)."""
        file = new_log.file
        if file and (Path(file).is_absolute() or ".." in Path(file).parts):
            raise HTTPException(
                status_code=422,
                detail="Путь файла лога должен быть относительным (от корня проекта)",
            )

    _register_config_endpoints(
        router,
        state=state,
        api_path="/api/config/log",
        schema_path="/api/config/log/schema",
        raw_path="/api/config/log/raw",
        filename="config_log.yaml",
        model=LoggingConfig,
        public=lambda m: m.model_dump(),
        require=require_devops,
        state_setter=lambda m: setattr(state.cfg, "logging", m),
        validate=_validate_log,
    )

    # --- config_parser.yaml: «Парсер» (devops) ---------------------------
    def _prepare_parser(body: dict[str, Any]) -> None:
        """Текстовое поле «диапазон задержек» превращается в кортеж чисел.

        Веб-форма отдаёт ``delay_between_actions_seconds`` строкой «4.0, 12.0»;
        модель ожидает ``tuple[float, float]`` — преобразуем до валидации.
        """
        browser = body.get("browser")
        if isinstance(browser, dict) and isinstance(
            browser.get("delay_between_actions_seconds"), str
        ):
            raw = browser["delay_between_actions_seconds"]
            try:
                parts = [float(x.strip()) for x in raw.replace(",", " ").replace(";", " ").split()]
                browser["delay_between_actions_seconds"] = parts[:2]
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="Диапазон задержек: ожидается два числа через запятую (например, 4, 12)",
                ) from exc

    _register_config_endpoints(
        router,
        state=state,
        api_path="/api/config/parser",
        schema_path="/api/config/parser/schema",
        raw_path="/api/config/parser/raw",
        filename="config_parser.yaml",
        model=ParserConfig,
        public=lambda m: m.model_dump(),
        require=require_devops,
        state_setter=lambda m: setattr(state.cfg, "parser", m),
        prepare=_prepare_parser,
    )

    _register_prompt_routes(
        router,
        "prompts",
        Path(state.cfg.ops.prompts_dir),
        state,
        "scoring_service",
        require_analyst,
    )
    _register_prompt_routes(
        router,
        "analysis-prompts",
        Path(state.cfg.ops.analysis_prompts_dir),
        state,
        "analysis_service",
        require_analyst,
    )

    return router
