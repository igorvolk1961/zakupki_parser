"""Настройки сервиса скоринга.

Порядок приоритета (от высшего к низшему):
1. аргументы конструктора;
2. переменные окружения ``SCORE_*``;
3. файл ``.env``;
4. YAML-конфиг (по умолчанию ``config.yaml``, путь можно переопределить env
   ``SCORE_CONFIG_FILE``);
5. значения по умолчанию в модели.

LangFuse — стандартные переменные ``LANGFUSE_*``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class YamlConfigSource(PydanticBaseSettingsSource):
    """Источник настроек из YAML-файла (ниже по приоритету, чем env/.env)."""

    def __init__(self, settings_cls: type[BaseSettings], path: Path) -> None:
        super().__init__(settings_cls)
        self._path = path

    def _load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {}
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        data = self._load()
        return data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return self._load()


class Settings(BaseSettings):
    """Конфигурация сервиса скоринга закупок."""

    model_config = SettingsConfigDict(env_prefix="SCORE_", env_file=".env", extra="ignore")

    # LLM (OpenAI-совместимый)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = "sk-dummy"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    # Таймаут одного LLM-запроса (сек) и число повторов при сетевой ошибке/таймауте.
    # Без таймаута зависший запрос блокирует весь прогон (например, оценку --repeat).
    llm_request_timeout: float = 45.0
    llm_max_retries: int = 1
    # Дедлайн на одну закупку в прогоне оценки (сек): если предмет не уложился —
    # помечается failed и прогон переходит к следующему (circuit breaker).
    eval_item_timeout_seconds: float = 300.0
    # Способ строгого JSON-выхода для with_structured_output:
    # "json_mode" — response_format={"type":"json_object"} (совместимо с DeepSeek и
    # большинством OpenAI-совместимых API); "json_schema"/"function_calling" — для
    # провайдеров, поддерживающих строгие схемы/тул-коллинг.
    llm_structured_method: Literal["json_mode", "json_schema", "function_calling"] = "json_mode"

    # Парсер закупок (REST, без БД)
    parser_api_url: str = "http://localhost:8000"

    # Redis-очередь
    redis_url: str = "redis://localhost:6379/0"
    jobs_key: str = "scoring:jobs"
    results_key: str = "scoring:results"
    processing_key: str = "scoring:processing"
    processing_meta_key: str = "scoring:processing_meta"
    processing_ttl_seconds: int = 600
    processing_recovery_priority: float = 0.0
    queue_poll_seconds: float = 2.0

    # Компетенции поставщика
    competencies_file: Path = Path("data/competencies.md")

    # Стубы P(win)/Margin (дефолтный подход парсера)
    p_win: float = 1.0
    margin_rate: float = 1.0

    # Пайплайн
    num_refine_rounds: int = 1
    max_fit_score: float = 10.0
    min_fit_score: float = 0.0
    score_round_digits: int = 2
    normalize_fit_for_score: bool = True

    # Уточнение скора по тексту ТЗ (requires_tz_review): поиск файла ТЗ в карточке,
    # извлечение текста и повторный fit/judge. Включается/выключается целиком.
    tz_review_enabled: bool = True
    # Таймаут скачивания файла ТЗ (сек).
    tz_download_timeout: float = 30.0

    # Параллельная ветка векторной близости (Giga Embedder): сравнение текста
    # компетенций и описания закупки через эмбеддинги. Если не задан ключ доступа
    # (giga_client_id/giga_client_secret) — ветка не выполняется, но факт пропуска
    # фиксируется в метаданных LangFuse-трейса (падения нет).
    giga_enabled: bool = False
    giga_base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    # Giga Embedder, окно 4096 токенов (умещает профиль компетенций без разбиения).
    giga_embeddings_model: str = "EmbeddingsGigaR"
    giga_auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    giga_client_id: str = ""
    giga_client_secret: str = ""
    giga_auth_scope: str = "GIGACHAT_API_PERS"
    # Влияние ветки на score: 0.0 — ветка не влияет (только диагностика/просмотр).
    giga_embedding_alpha: float = 0.0
    # Таймаут запроса эмбеддингов (сек).
    giga_timeout_seconds: float = 30.0
    # Порог остаточного времени жизни токена (сек): при значении меньше — обновить.
    giga_min_token_ttl_seconds: float = 60.0
    # Проверять SSL-сертификат при обращении к Giga. Giga OAuth (ngw...:9443)
    # использует самоподписанный сертификат — для локальной разработки можно
    # выключить (false). По умолчанию проверка включена (безопасно).
    giga_verify_ssl: bool = True

    @property
    def giga_configured(self) -> bool:
        """Ключ доступа Giga задан (можно выполнять эмбеддинги)."""
        return bool(self.giga_client_id and self.giga_client_secret)

    # Заглушка: возвращать score, уже присутствующий в данных закупки (без LLM-пайплайна).
    # Включать, пока LLM-пайплайн не отлажен.
    # AliasChoices: имя поля уже содержит префикс "score_", поэтому без явного
    # env-алиаса pydantic ждёт переменную SCORE_score_use_stub вместо SCORE_USE_STUB.
    score_use_stub: bool = Field(
        default=False, validation_alias=AliasChoices("score_use_stub", "SCORE_USE_STUB")
    )

    # LangFuse (None = выключен)
    # LangFuse-трассировка (стандартные переменные LANGFUSE_*, без SCORE_ префикса).
    # AliasChoices: из-за env_prefix="SCORE_" без явного алиаса pydantic ждёт
    # SCORE_LANGFUSE_* вместо стандартного LANGFUSE_*.
    langfuse_public_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("langfuse_public_key", "LANGFUSE_PUBLIC_KEY"),
    )
    langfuse_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("langfuse_secret_key", "LANGFUSE_SECRET_KEY"),
    )
    langfuse_host: str | None = Field(
        default=None,
        validation_alias=AliasChoices("langfuse_host", "LANGFUSE_HOST"),
    )

    # Опциональная авторизация HTTP-эндпоинтов (None = выключено, dev)
    auth_token: str | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Источники в порядке приоритета (первый — самый высокий).
        yaml_path = Path(os.getenv("SCORE_CONFIG_FILE", "config.yaml"))
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSource(settings_cls, yaml_path),
            file_secret_settings,
        )

    def competencies(self) -> str:
        """Текст компетенций поставщика из файла."""
        return self.competencies_file.read_text(encoding="utf-8")


def get_settings() -> Settings:
    return Settings()
