"""Эндпоинты конфигурации сервиса (config_service.yaml) и редактора промптов."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from zakupki_parser.api.app.converters import (
    _prompt_dir_rel,
    _prompt_file,
    _prompt_kind,
    _service_config_public,
)
from zakupki_parser.api.app.deps import ApiContext
from zakupki_parser.api.app.schemas import PromptUpdate
from zakupki_parser.api.app.state import AppState
from zakupki_parser.config.models import ServiceConfig

logger = logging.getLogger(__name__)


def _register_prompt_routes(
    router: APIRouter,
    prefix: str,
    base: Path,
    state: AppState,
    service_name: str,
    require_user: Callable[..., Any],
    require_admin: Callable[..., Any],
) -> None:
    """Список/чтение/сохранение файлов промптов одного каталога."""

    @router.get(
        f"/api/{prefix}",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(require_user)],
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
        dependencies=[Depends(require_user)],
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
        dependencies=[Depends(require_admin)],
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


def build_config_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    state = ctx.state
    require_user = ctx.require_user
    require_admin = ctx.require_admin

    @router.get(
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

    @router.get(
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
        return _service_config_public(state.cfg.service)

    @router.put(
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
                    _service_config_public(new_service),
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
        return _service_config_public(new_service)

    _register_prompt_routes(
        router,
        "prompts",
        Path(state.cfg.ops.prompts_dir),
        state,
        "scoring_service",
        require_user,
        require_admin,
    )
    _register_prompt_routes(
        router,
        "analysis-prompts",
        Path(state.cfg.ops.analysis_prompts_dir),
        state,
        "analysis_service",
        require_user,
        require_admin,
    )

    return router
