"""Общая настройка логирования для сервисов каскада скоринга.

Каждый сервис управляет собственным логом (уровень, формат, файл с ротацией,
очистка при старте) из своего ``config.yaml``. В продакшене сервисы живут в
изолированных контейнерах, поэтому общий внешний ``config_log.yaml`` им
недоступен — конфигурация хранится и читается внутри сервиса. Схема и механизм
настройки едины для всех сервисов и совпадают с парсером (см.
``zakupki_parser.logging_conf``).
"""

from __future__ import annotations

import contextvars
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

# Чувствительные query-параметры (token, access_token, secret и т.п.) —
# их значения вымарываются из логов. Регэксп ловит параметр после '?' или '&'
# и всё до следующего разделителя (&, пробел, кавычка).
_SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:token|access_token|refresh_token|secret|api_key|key|password|internal_token|signature)=)[^&\s\"']*",
    re.IGNORECASE,
)


class _ScrubbingFormatter(logging.Formatter):
    """Форматтер, заменяющий значения чувствительных query-параметров на ``***``.

    Применяется к финальной строке лога, поэтому закрывает любые источники
    утечки токенов: access-логи uvicorn (WebSocket/HTTP), исключения с URL и т.д.
    """

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return _SENSITIVE_QUERY_RE.sub(lambda m: m.group(1) + "***", text)


class _NameRewriteFilter(logging.Filter):
    """Переименовывает служебные логгеры uvicorn в нейтральные.

    ``uvicorn.error`` логирует и INFO-сообщения — слово «error» в имени логгера
    пугает заказчика. Приводим к читаемым именам: uvicorn.error -> uvicorn,
    uvicorn.access -> http.
    """

    _REWRITE = {
        "uvicorn.error": "uvicorn",
        "uvicorn.access": "http",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        for prefix, new_name in self._REWRITE.items():
            if record.name == prefix or record.name.startswith(prefix + "."):
                record.name = new_name
                break
        return True


# Контекст обхода площадки парсером: (platform_id, iteration). Устанавливается на
# время обработки одной площадки (scheduler._process_platform) и подхватывается
# фильтром логов, чтобы каждая запись этого обхода несла префикс
# ``[<площадка>#<итерация>]``. Без контекста (другие сервисы) — префикса нет.
_RUN_CONTEXT: contextvars.ContextVar[tuple[str, int] | None] = contextvars.ContextVar(
    "parser_run_context", default=None
)


def set_run_context(
    platform_id: str, iteration: int
) -> contextvars.Token[tuple[str, int] | None] | None:
    """Установить (platform_id, iteration) в контекст текущей задачи."""
    return _RUN_CONTEXT.set((platform_id, iteration))


def reset_run_context(token: contextvars.Token[tuple[str, int] | None] | None) -> None:
    """Вернуть прежнее значение контекста после обработки площадки."""
    if token is not None:
        _RUN_CONTEXT.reset(token)


class _RunContextFilter(logging.Filter):
    """Дописывает ``[<площадка>#<итерация>]`` к имени логгера в контексте обхода.

    Работает через ``contextvars``: пока задан контекст площадки, каждая запись
    логирования внутри этого обхода (включая вложенные asyncio-задачи/логгеры)
    получает суффикс ``[<platform_id>#<iteration>]``. Вне контекста — no-op.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _RUN_CONTEXT.get()
        if ctx is not None:
            platform_id, iteration = ctx
            record.name = f"{record.name}[{platform_id}#{iteration}]"
        return True


class LoggingSettings(BaseModel):
    """Конфигурация логирования сервиса (собственный ``config.yaml``).

    Схема совпадает с ``zakupki_parser.config.models.LoggingConfig``
    (``config_log.yaml`` парсера) — управление логами всех сервисов единообразно.
    """

    level: str = Field(default="INFO")
    format: str = Field(default="%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    file: str | None = Field(
        default=None,
        description="путь к файлу лога (относительно корня проекта; None — только консоль)",
    )
    file_level: str = Field(default="DEBUG")
    console: bool = Field(default=True)
    truncate_on_start: bool = Field(
        default=False,
        description="очищать файл лога при старте сервиса (True) или дописывать (False)",
    )

    @field_validator("file")
    @classmethod
    def _validate_file(cls, value: str | None) -> str | None:
        """Путь файла лога — только относительный (без выхода за корень проекта)."""
        if value is None:
            return value
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("путь к файлу лога должен быть относительным (от корня проекта)")
        return value


def setup_logging(cfg: LoggingSettings) -> None:
    """Конфигурирует корневой логгер согласно ``cfg``."""
    root = logging.getLogger()
    root.setLevel(cfg.level.upper())
    root.handlers.clear()

    fmt = _ScrubbingFormatter(cfg.format)
    name_filter = _NameRewriteFilter()
    run_filter = _RunContextFilter()

    if cfg.console:
        console = logging.StreamHandler()
        console.setLevel(cfg.level.upper())
        console.setFormatter(fmt)
        console.addFilter(name_filter)
        console.addFilter(run_filter)
        root.addHandler(console)

    if cfg.file:
        file_path = Path(cfg.file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        # При truncate_on_start=True файл очищается при старте (иначе — дописываем).
        if cfg.truncate_on_start:
            file_path.write_text("", encoding="utf-8")
        file_handler = RotatingFileHandler(
            file_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(cfg.file_level.upper())
        file_handler.setFormatter(fmt)
        file_handler.addFilter(name_filter)
        file_handler.addFilter(run_filter)
        root.addHandler(file_handler)

    # Уменьшаем шум от сторонних библиотек
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    # Access-логи запросов (INFO) не пишем — только значимые события.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
