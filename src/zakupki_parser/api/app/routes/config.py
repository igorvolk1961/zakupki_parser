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
редактируются — они управляются через env. Авторизация всегда включена
(переключателя ``enabled`` нет) и через API не меняется: иначе devops мог бы
отключить авторизацию для всего сервиса.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
from zakupki_parser.api.app.restart_services import (
    find_worker_pids,
    launch_worker,
    terminate_pids,
)
from zakupki_parser.api.app.schemas import PlatformOut, PlatformsListOut, PromptUpdate
from zakupki_parser.api.app.state import AppState
from zakupki_parser.config.models import (
    AnalysisServiceConfig,
    LoggingConfig,
    MarginServiceConfig,
    OpsConfig,
    ParserConfig,
    PwinServiceConfig,
    ScoringServiceConfig,
    ServiceConfig,
)

logger = logging.getLogger(__name__)


def _errors_to_jsonable(exc: ValidationError) -> list[dict[str, Any]]:
    """Pydantic-ошибки в JSON-безопасный вид (для HTTPException detail).

    Pydantic v2 кладёт в ``ctx.error`` сырой экземпляр исключения для
    ``value_error`` (например, ``ValueError`` из ``field_validator``). Такой объект
    не сериализуется ``json.dumps`` — FastAPI падает при формировании ответа 422.
    Сводим ``ctx`` к строкам и отбрасываем поля, которые не нужны клиенту.
    """
    return [
        {
            "type": e["type"],
            "loc": list(e["loc"]),
            "msg": e["msg"],
            "ctx": {k: str(v) for k, v in (e.get("ctx") or {}).items()},
        }
        for e in exc.errors(include_url=False, include_input=False)
    ]


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
    ``validate`` — пост-валидационная проверка (например, запрет смены auth-секретов);
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
                raise HTTPException(status_code=422, detail=_errors_to_jsonable(exc)) from exc
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
    - scoring: в одну колонку, короткие подписи правил оценки закупки;
    - прочие комментарии полей (``description``) переезжают в tooltip'ы.
    """
    scoring_labels: dict[str, str] = {
        "embedding_filter_threshold": "Порог векторной близости",
        "giga_embedding_alpha": "Вес векторной близости",
        "giga_enabled": "Ветка векторной близости",
        "num_refine_rounds": "Повторные fit-итерации",
        "max_fit_score": "Максимальный Fit",
        "min_fit_score": "Минимальный Fit",
        "score_round_digits": "Округление score",
        "tz_download_timeout": "Таймаут скачивания ТЗ",
    }
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
        elif field.get("key") == "scoring" and field.get("kind") == "object":
            field["stack"] = True
            for sub in field["fields"]:
                label = scoring_labels.get(sub["key"])
                if label:
                    sub["label"] = label
        elif field.get("key") == "deduplicate_requests":
            field["label"] = "Объединять одинаковые обходы"
            field["description"] = (
                "Объединять одинаковые поисковые обходы одной площадки (совпадающие коды "
                "ОКПД2/НМЦК/состояние разных профилей) в один запрос. Что это даёт и когда "
                "отключать — см. docs/profile-crawling.md"
            )
    return schema


# Именованные конфигурации фоновых сервисов (вкладка «Сервисы»). Каждый сервис
# читает собственный src/<dir>/config.yaml (несекретная часть) и src/<dir>/.env
# (секреты и env-переопределения). Секреты в config.yaml/форму НЕ попадают.
@dataclass(frozen=True)
class _ServiceConfig:
    name: str  # слаг в URL (/api/services/<name>/...)
    dir: str  # каталог сервиса в src/
    model: type[BaseModel]
    secrets: frozenset[str]
    title: str
    groups: tuple[tuple[str, tuple[str, ...]], ...]  # (подпись группы, ключи полей)
    # Метаданные рестарта (вариант A: subprocess, как в scripts/run_all.sh).
    module: str = ""  # модуль для `python -m <module> worker`
    worker_cmd: str = "worker"  # подкоманда воркера
    parser_env: str = ""  # env-переменная с URL парсера
    log_name: str = ""  # имя файла лога (data/logs/<log_name>.log)


SERVICE_CONFIGS: dict[str, _ServiceConfig] = {
    cfg.name: cfg
    for cfg in (
        _ServiceConfig(
            name="scoring",
            dir="scoring_service",
            model=ScoringServiceConfig,
            secrets=frozenset(
                {
                    "llm_api_key",
                    "parser_internal_token",
                    "giga_client_id",
                    "giga_client_secret",
                    "langfuse_public_key",
                    "langfuse_secret_key",
                    "langfuse_host",
                    "auth_token",
                }
            ),
            title="Скоринг-сервис",
            module="scoring_service",
            worker_cmd="worker",
            parser_env="SCORE_PARSER_API_URL",
            log_name="scoring_service",
            groups=(
                (
                    "LLM (OpenAI-совместимый)",
                    (
                        "llm_base_url",
                        "llm_model",
                        "llm_temperature",
                        "llm_request_timeout",
                        "llm_max_retries",
                        "llm_structured_method",
                    ),
                ),
                ("Парсер закупок", ("parser_api_url", "parser_retry_backoff_seconds")),
                (
                    "Redis-очередь",
                    (
                        "redis_url",
                        "jobs_key",
                        "results_key",
                        "processing_key",
                        "processing_meta_key",
                        "processing_ttl_seconds",
                        "processing_recovery_priority",
                        "queue_poll_seconds",
                        "jobs_retry_key",
                        "llm_retry_max_attempts",
                        "llm_retry_backoff_seconds",
                    ),
                ),
                ("Профиль поставщика", ("competencies_file",)),
                (
                    "Пайплайн",
                    (
                        "num_refine_rounds",
                        "max_fit_score",
                        "min_fit_score",
                        "score_round_digits",
                        "normalize_fit_for_score",
                        "eval_item_timeout_seconds",
                    ),
                ),
                (
                    "Уточнение по тексту ТЗ",
                    (
                        "tz_review_enabled",
                        "tz_download_timeout",
                        "tz_verify_ssl",
                    ),
                ),
                (
                    "Giga Embedder (ветка близости)",
                    (
                        "giga_enabled",
                        "giga_base_url",
                        "giga_embeddings_model",
                        "giga_auth_url",
                        "giga_auth_scope",
                        "giga_embedding_alpha",
                        "giga_timeout_seconds",
                        "giga_min_token_ttl_seconds",
                        "giga_verify_ssl",
                        "embedding_filter_threshold",
                    ),
                ),
                ("Логирование", ("logging",)),
            ),
        ),
        _ServiceConfig(
            name="analysis",
            dir="analysis_service",
            model=AnalysisServiceConfig,
            secrets=frozenset({"llm_api_key", "embedding_api_key", "parser_internal_token"}),
            title="Анализ ТЗ",
            module="analysis_service.cli",
            worker_cmd="worker",
            parser_env="ANALYSIS_PARSER_API_URL",
            log_name="analysis_service",
            groups=(
                (
                    "LLM (OpenAI-совместимый)",
                    (
                        "llm_base_url",
                        "llm_model",
                        "llm_temperature",
                        "llm_request_timeout",
                    ),
                ),
                ("Эмбеддинги", ("embedding_base_url", "embedding_model", "embedding_timeout")),
                ("Парсер закупок", ("parser_api_url", "parser_retry_backoff_seconds")),
                (
                    "Redis-очередь",
                    (
                        "redis_url",
                        "jobs_key",
                        "results_key",
                        "processing_key",
                        "processing_meta_key",
                        "processing_ttl_seconds",
                        "processing_recovery_priority",
                        "queue_poll_seconds",
                        "jobs_retry_key",
                    ),
                ),
                (
                    "RAG-параметры",
                    (
                        "chunk_max_chars",
                        "top_k",
                        "tz_download_timeout",
                        "tz_verify_ssl",
                    ),
                ),
                ("Логирование", ("logging",)),
            ),
        ),
        _ServiceConfig(
            name="pwin",
            dir="pwin_service",
            model=PwinServiceConfig,
            secrets=frozenset({"parser_internal_token"}),
            title="P(win)",
            module="pwin_service",
            worker_cmd="worker",
            parser_env="PWIN_PARSER_API_URL",
            log_name="pwin_service",
            groups=(
                ("Парсер закупок", ("parser_api_url", "parser_retry_backoff_seconds")),
                (
                    "Redis-очередь",
                    (
                        "redis_url",
                        "jobs_key",
                        "results_key",
                        "processing_key",
                        "processing_meta_key",
                        "processing_ttl_seconds",
                        "processing_recovery_priority",
                        "queue_poll_seconds",
                        "jobs_retry_key",
                    ),
                ),
                ("Пайплайн", ("score_round_digits",)),
                ("Заглушка", ("use_stub", "stub_pwin")),
                (
                    "Модель P(win)",
                    (
                        "base_pwin",
                        "k_smp",
                        "k_license_present",
                        "k_license_absent",
                        "k_large_threshold",
                        "k_large",
                        "k_procedure_auction",
                        "k_procedure_contest",
                        "k_procedure_quotation",
                        "k_ai",
                        "max_pwin_cap",
                    ),
                ),
                ("Маркеры ИИ-закупки", ("ai_markers",)),
                ("Логирование", ("logging",)),
            ),
        ),
        _ServiceConfig(
            name="margin",
            dir="margin_service",
            model=MarginServiceConfig,
            secrets=frozenset({"parser_internal_token"}),
            title="Margin",
            module="margin_service",
            worker_cmd="worker",
            parser_env="MARGIN_PARSER_API_URL",
            log_name="margin_service",
            groups=(
                ("Парсер закупок", ("parser_api_url", "parser_retry_backoff_seconds")),
                (
                    "Redis-очередь",
                    (
                        "redis_url",
                        "jobs_key",
                        "results_key",
                        "processing_key",
                        "processing_meta_key",
                        "processing_ttl_seconds",
                        "processing_recovery_priority",
                        "queue_poll_seconds",
                        "jobs_retry_key",
                    ),
                ),
                ("Пайплайн", ("margin_rate", "score_round_digits")),
                ("Логирование", ("logging",)),
            ),
        ),
    )
}


def _service_paths(state: AppState, service: _ServiceConfig) -> tuple[Path, Path]:
    """Пути к config.yaml и .env сервиса (относительно корня проекта)."""
    root = Path(state.configs_dir).resolve().parent
    base = root / "src" / service.dir
    return base / "config.yaml", base / ".env"


def _read_yaml_quiet(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        detail = f"Некорректный YAML в {path.name}: {exc}"
        raise HTTPException(status_code=500, detail=detail) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail=f"Ожидается объект конфигурации в {path.name}")
    return data


def _strip_secrets(data: dict[str, Any], secrets: frozenset[str]) -> dict[str, Any]:
    """Убирает секреты из конфига (они управляются через .env / env)."""
    return {k: v for k, v in data.items() if k not in secrets}


def _group_schema(
    schema: list[dict[str, Any]], groups: tuple[tuple[str, tuple[str, ...]], ...]
) -> list[dict[str, Any]]:
    """Присваивает полям ``group`` (подпись секции) и упорядочивает по группам.

    Поля вне групп остаются в конце (без секции). Внутри группы порядок сохраняется.
    """
    index: dict[str, int] = {}
    label_of: dict[str, str] = {}
    for i, (label, keys) in enumerate(groups):
        for key in keys:
            index[key] = i
            label_of[key] = label

    def sort_key(field: dict[str, Any]) -> tuple[int, int, str]:
        key = field["key"]
        if key in index:
            return (0, index[key], key)
        return (1, 0, key)

    ordered = sorted(schema, key=sort_key)
    for field in ordered:
        group_label = label_of.get(field["key"])
        if group_label:
            field["group"] = group_label
    return ordered


def _validate_env_content(content: str) -> None:
    """Строгая проверка .env: только KEY=VALUE, комментарии и пустые строки."""
    allowed_keys = r"[A-Za-z_][A-Za-z0-9_]*"
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        eq = stripped.find("=")
        if eq <= 0:
            raise HTTPException(
                status_code=422,
                detail=f"Строка {lineno} не является KEY=VALUE",
            )
        key = stripped[:eq].strip()
        if not re.fullmatch(allowed_keys, key):
            raise HTTPException(
                status_code=422,
                detail=f"Строка {lineno}: некорректное имя переменной {key!r}",
            )


async def _read_env_body(request: Request) -> str:
    """Тело PUT env: raw text/plain или JSON {"content": "..."}."""
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        body = await request.json()
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, str):
            raise HTTPException(status_code=422, detail="Ожидается JSON с полем content")
        return content
    raw: bytes = await request.body()
    return raw.decode("utf-8")


def _register_service_config_routes(
    router: APIRouter,
    *,
    state: AppState,
    require: Callable[..., Any],
) -> None:
    """GET/PUT config.yaml и .env для каждого фонового сервиса (devops, «Сервисы»)."""

    def _register_one(svc: _ServiceConfig) -> None:
        """Определяет эндпоинты одного сервиса (замыкание на ``svc``)."""
        prefix = f"/api/services/{svc.name}"

        @router.get(
            f"{prefix}/config",
            response_model=dict[str, Any],
            include_in_schema=False,
            dependencies=[Depends(require)],
        )
        async def get_service_config() -> dict[str, Any]:
            config_path, _ = _service_paths(state, svc)
            data = _read_yaml_quiet(config_path)
            return _strip_secrets(data, svc.secrets)

        @router.get(
            f"{prefix}/schema",
            response_model=dict[str, Any],
            include_in_schema=False,
            dependencies=[Depends(require)],
        )
        async def get_service_schema() -> dict[str, Any]:
            schema = build_schema(svc.model)
            return {"schema": _group_schema(schema, svc.groups)}

        @router.get(
            f"{prefix}/raw",
            response_model=dict[str, Any],
            include_in_schema=False,
            dependencies=[Depends(require)],
        )
        async def get_service_raw() -> dict[str, Any]:
            config_path, _ = _service_paths(state, svc)
            data = _strip_secrets(_read_yaml_quiet(config_path), svc.secrets)
            return {"yaml": yaml.safe_dump(data, allow_unicode=True, sort_keys=False)}

        @router.put(
            f"{prefix}/config",
            response_model=dict[str, Any],
            include_in_schema=False,
            dependencies=[Depends(require)],
        )
        async def put_service_config(request: Request) -> dict[str, Any]:
            config_path, _ = _service_paths(state, svc)
            body = await _read_payload(request)
            data = _strip_secrets(body, svc.secrets)
            try:
                new_model = svc.model.model_validate(data)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=_errors_to_jsonable(exc)) from exc
            _write_yaml(config_path, new_model.model_dump())
            logger.info("Сохранён %s (%s)", config_path, svc.title)
            return new_model.model_dump()

        @router.get(
            f"{prefix}/env",
            response_model=dict[str, Any],
            include_in_schema=False,
            dependencies=[Depends(require)],
        )
        async def get_service_env() -> dict[str, Any]:
            _, env_path = _service_paths(state, svc)
            content = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
            return {"content": content, "exists": env_path.is_file()}

        @router.put(
            f"{prefix}/env",
            response_model=dict[str, Any],
            include_in_schema=False,
            dependencies=[Depends(require)],
        )
        async def put_service_env(request: Request) -> dict[str, Any]:
            _, env_path = _service_paths(state, svc)
            content = await _read_env_body(request)
            _validate_env_content(content)
            try:
                env_path.write_text(content, encoding="utf-8")
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"Не удалось сохранить .env: {exc}"
                ) from exc
            logger.info("Сохранён .env сервиса %s", svc.title)
            return {"content": content, "exists": True}

        @router.post(
            f"{prefix}/restart",
            include_in_schema=False,
            dependencies=[Depends(require)],
        )
        async def restart_service() -> dict[str, Any]:
            """Перезапускает фоновый сервис скоринга (вариант A: subprocess).

            Находит рабочие процессы сервиса по командной строке, завершает их и
            поднимает сервис заново той же командой, что и scripts/run_all.sh.
            Рестарт может занимать до нескольких секунд (ожидание завершения
            процессов) — выполняем в отдельном потоке, чтобы не блокировать цикл.
            """
            return await asyncio.to_thread(_restart_service, state, svc)

    for svc in SERVICE_CONFIGS.values():
        _register_one(svc)


def _restart_service(state: AppState, svc: _ServiceConfig) -> dict[str, Any]:
    """Перезапуск одного фонового сервиса скоринга (вариант A: subprocess)."""
    root = Path(state.configs_dir).resolve().parent
    port = int(getattr(state, "parser_port", 8000) or 8000)
    log_path = root / "data" / "logs" / f"{svc.log_name}.log"
    pids = find_worker_pids(svc.module, svc.worker_cmd)
    terminated = terminate_pids(pids)
    pid = launch_worker(
        project_root=root,
        service_dir=svc.dir,
        module=svc.module,
        cmd=svc.worker_cmd,
        parser_env=svc.parser_env,
        parser_url=f"http://127.0.0.1:{port}",
        log_path=log_path,
    )
    logger.info(
        "Перезапущен сервис %s (%s): завершено %s, новый PID %s",
        svc.title,
        svc.name,
        terminated,
        pid,
    )
    return {
        "status": "restarting",
        "service": svc.name,
        "terminated": terminated,
        "pid": pid,
    }


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
        "/api/config/scoring",
        response_model=dict[str, Any],
        include_in_schema=False,
        dependencies=[Depends(ctx.require_user_or_internal)],
    )
    async def get_scoring_config() -> dict[str, Any]:
        """Аналитические скор-настройки (config_service.yaml -> scoring).

        Читается внутренним конвейером (scoring_service) через X-Internal-Token,
        чтобы воркер применял актуальные правила оценки без рестарта.
        """
        return state.cfg.service.scoring.model_dump()

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
        """Перед валидацией подмешиваем env-секреты (secret/internal_token из env)."""
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

    # --- Сервисы (devops): конфиг + .env каждого фонового сервиса ------
    _register_service_config_routes(
        router,
        state=state,
        require=require_devops,
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
