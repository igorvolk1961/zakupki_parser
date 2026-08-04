"""Pydantic-модели конфигурации парсера.

Все параметры парсера задаются исключительно через YAML-файлы в ``configs/``.
Эти модели валидируют загруженный конфиг и предоставляют типизированный доступ.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# config_parser.yaml
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# config_dom.yaml
# --------------------------------------------------------------------------- #
class DomVariable(BaseModel):
    """Описание одной извлекаемой переменной."""

    name: str
    selector: str
    attribute: str | None = None
    index: int | None = Field(
        default=None,
        ge=0,
        description="взять N-й элемент, совпавший с селектором (вместо первого)",
    )
    handler: str | None = Field(
        default=None,
        description=(
            "опциональная постобработка: none|strip|float|int|date_iso|lower|"
            "pub_date|deadline|law|regex"
        ),
    )
    handler_arg: str | None = Field(
        default=None, description="аргумент для обработчика (например, regex-паттерн)"
    )
    default: Any = None


class DomListConfig(BaseModel):
    """Селекторы страницы списка закупок."""

    container: str = Field(description="CSS-селектор контейнера записи о закупке")
    variables: list[DomVariable] = Field(default_factory=list)
    detail_link: str = Field(description="CSS-селектор ссылки на детальную страницу")
    next_page: str = Field(description="CSS-селектор кнопки/ссылки следующей страницы")
    publication_date: str = Field(
        default="publication_date",
        description="имя переменной в list.variables с датой публикации (для стоп-порога)",
    )


class DomDetailConfig(BaseModel):
    """Селекторы страницы детальной информации."""

    variables: list[DomVariable] = Field(default_factory=list)
    files: list[DomVariable] = Field(
        default_factory=list, description="элементы ссылок на скачиваемые файлы"
    )


# --------------------------------------------------------------------------- #
# Фильтры и сортировка (задаются внутри config_dom.yaml, в блоке площадки)
# --------------------------------------------------------------------------- #
class FilterStep(BaseModel):
    """Один шаг DOM-манипуляции для установки/применения фильтра."""

    action: Literal["click", "fill", "select", "press", "wait", "set_checkbox"]
    selector: str
    value: str | None = None
    wait_ms: int = Field(default=500, ge=0)


class PurchaseFilter(BaseModel):
    """Описание одного фильтра."""

    name: str
    steps: list[FilterStep] = Field(description="DOM-шаги, приводящие к выбору значения")


class SortConfig(BaseModel):
    """Установка порядка сортировки списка закупок."""

    dropdown: str | None = Field(default=None, description="селектор выпадающего списка сортировки")
    option_text: str | None = Field(
        default=None, description="текст пункта сортировки (например, «По дате публикации»)"
    )


class PlatformDom(BaseModel):
    """DOM-конфигурация одной площадки закупок.

    Содержит и селекторы извлечения (``list``/``detail``), и селекторы
    сортировки/фильтров (``sort``/``filters``) — всё, что связано с DOM площадки.
    """

    name: str
    url: str = Field(description="базовый адрес платформы")
    list_path: str = Field(default="", description="путь к странице списка закупок")
    list_config: DomListConfig
    detail: DomDetailConfig
    sort: SortConfig | None = Field(default=None, description="установка сортировки списка")
    filters: list[PurchaseFilter] = Field(
        default_factory=list, description="фильтры и порядок их DOM-шагов"
    )


class DomConfig(BaseModel):
    """Конфигурация DOM-элементов по площадкам."""

    platforms: dict[str, PlatformDom] = Field(
        description="ключ platform_id -> конфигурация площадки"
    )

    @field_validator("platforms")
    @classmethod
    def _non_empty(cls, v: dict[str, PlatformDom]) -> dict[str, PlatformDom]:
        if not v:
            raise ValueError("config_dom должен содержать хотя бы одну площадку")
        return v


# --------------------------------------------------------------------------- #
# config_service.yaml
# --------------------------------------------------------------------------- #
class DbConfig(BaseModel):
    """Параметры подключения к базе данных."""

    dsn: str = Field(default="postgresql://postgres:postgres@localhost:5432/zakupki")
    enabled: bool = Field(default=True)
    connect_timeout_seconds: float = Field(default=5.0, ge=0)
    pool_min: int = Field(default=1, ge=0)
    pool_max: int = Field(default=5, ge=1)


class WebhookConfig(BaseModel):
    """Параметры webhook-уведомлений."""

    enabled: bool = Field(default=False)
    url: str | None = None
    token: str | None = None
    timeout_seconds: float = Field(default=10.0, ge=0)


class SiteServiceEntry(BaseModel):
    """Одна запись в списке сайтов для периодического обхода."""

    platform_id: str = Field(description="ключ площадки в config_dom.yaml")
    enabled: bool = Field(default=True)


class StopConditions(BaseModel):
    """Набор флагов-условий прекращения обработки очередной заявки.

    Каждый флаг — это условие, при котором заявка пропускается (не сохраняется
    и не уведомляется). Набор расширяется добавлением новых флагов.
    """

    enabled: bool = Field(default=True, description="главный переключатель условий")
    deadline_not_expired: bool = Field(
        default=True,
        description=(
            "не обрабатывать заявку, если срок приёма заявок (переменная 'deadline' "
            "из config_dom.yaml) истёк к текущей дате"
        ),
    )


class ServiceConfig(BaseModel):
    """Сервисная конфигурация: таймер, список сайтов, пороги, флаги."""

    timeout_seconds: int = Field(default=3600, ge=1)
    sites: list[SiteServiceEntry] = Field(default_factory=list)
    default_cutoff_days: int = Field(
        default=7, ge=0, description="порог 'дата последней обработанной записи' в днях"
    )
    download_files: bool = Field(default=False)
    delete_files_after_processing: bool = Field(default=True)
    documents_dir: str = Field(default="documents")
    data_dir: str = Field(default="data")
    db: DbConfig = Field(default_factory=DbConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)
    circuit_breaker_reset_timeout_seconds: float = Field(default=60.0, ge=1)


# --------------------------------------------------------------------------- #
# config_log.yaml
# --------------------------------------------------------------------------- #
class LoggingConfig(BaseModel):
    """Конфигурация логирования."""

    level: str = Field(default="INFO")
    format: str = Field(default="%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    file: str | None = Field(default=None, description="путь к файлу лога")
    file_level: str = Field(default="DEBUG")
    console: bool = Field(default=True)
    truncate_on_start: bool = Field(
        default=False,
        description="очищать файл лога при старте сервиса (True) или дописывать (False)",
    )


# --------------------------------------------------------------------------- #
# Корневая модель всех конфигов
# --------------------------------------------------------------------------- #
class AppConfig(BaseModel):
    """Собирает все конфиги вместе для удобной передачи."""

    configs_dir: Path
    parser: ParserConfig
    dom: DomConfig
    service: ServiceConfig
    logging: LoggingConfig
