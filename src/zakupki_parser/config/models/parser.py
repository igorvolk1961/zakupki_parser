"""Модели конфигурации парсера/браузера (config_parser.yaml)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RetryConfig(BaseModel):
    """Параметры повторов при сетевых ошибках/ошибках браузера."""

    max_attempts: int = Field(default=3, ge=1)
    min_backoff_seconds: float = Field(default=2.0, ge=0)
    max_backoff_seconds: float = Field(default=60.0, ge=1)
    jitter_seconds: float = Field(default=1.0, ge=0)


class RequestLimits(BaseModel):
    """Ограничение частоты запросов (вежливое поведение)."""

    enabled: bool = Field(default=True)
    max_requests_per_minute: int = Field(default=10, ge=1)


class BrowserConfig(BaseModel):
    """Параметры браузера и антиблок-мер."""

    headless: bool = Field(default=True)
    user_agent: str | None = None
    chromium_executable_path: str | None = Field(
        default=None,
        description=(
            "Путь к исполняемому файлу Chromium (полная версия). Пусто — "
            "используется headless-shell. Полная версия лучше обходит антибот."
        ),
    )
    viewport_width: int = Field(default=1366, ge=320)
    viewport_height: int = Field(default=768, ge=240)
    locale: str = Field(default="ru-RU")
    timezone: str = Field(default="Europe/Moscow")
    disable_webdriver_flag: bool = Field(default=True)
    persist_session: bool = Field(default=True)
    session_dir: str = Field(default="data/session")
    ignore_https_errors: bool = Field(
        default=False,
        description=(
            "игнорировать ошибки SSL-сертификата (нужно для площадок с "
            "некорректным/корпоративным сертификатом, напр. zakupki.gov.ru)"
        ),
    )
    delay_between_actions_seconds: tuple[float, float] = Field(
        default=(4.0, 12.0), description="рандомная задержка между действиями"
    )
    scroll_randomly: bool = Field(default=True)
    random_mouse_moves: bool = Field(default=True)


class ParserConfig(BaseModel):
    """Общий конфиг параметров парсера."""

    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    request_limits: RequestLimits = Field(default_factory=RequestLimits)
