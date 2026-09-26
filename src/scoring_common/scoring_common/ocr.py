"""OCR для сканов PDF без текстового слоя — сменяемая модель (как эмбеддинги/LLM).

Используется ТОЛЬКО как fallback в ``scoring_common.tz.extractors._extract_pdf``,
когда прямое извлечение текста не дало результата (ни ``pdf_to_markdown_tables``
(pdfplumber), ни MarkItDown не нашли текстовый слой — весь PDF или его часть
является сканом без текста). Прямое извлечение всегда пробуется первым — OCR
дороже и медленнее.

Провайдер переключается переменной окружения ``OCR_PROVIDER`` (сейчас — только
``yandex``; ``none``/не задано — OCR-фолбэк отключён целиком, поведение как до
появления этого модуля). Добавить второго провайдера — новый класс с тем же
протоколом ``Ocrable`` + ветка в ``get_client()``, вызывающий код не меняется.

Настройки — из окружения процесса (``OCR_``/``YANDEX_OCR_`` namespace), не из
``settings.py`` конкретного сервиса — как ``OBJECT_STORAGE_``
(см. ``scoring_common.object_storage``): один набор переменных на весь стек.

Best-effort: не настроен/сбой — ``get_client()``/``recognize_pdf`` возвращают
``None``, вызывающий код продолжает как при сканах без OCR вовсе (не роняет
задание).
"""

from __future__ import annotations

import base64
import logging
import threading
from typing import Any, Protocol

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Ocrable(Protocol):
    """OCR-клиент: PDF без текстового слоя -> распознанный текст."""

    def recognize_pdf(self, raw: bytes) -> str | None:
        """Текст со всех страниц PDF (сканы); ``None`` — сбой/текста нет."""
        ...


class OcrSettings(BaseSettings):
    """Выбор OCR-провайдера (сменяемая модель)."""

    model_config = SettingsConfigDict(env_prefix="OCR_", extra="ignore")

    provider: str = "yandex"  # "none" — OCR-фолбэк отключён вовсе


class YandexOcrSettings(BaseSettings):
    """Учётные данные и параметры Yandex Cloud Vision OCR."""

    model_config = SettingsConfigDict(env_prefix="YANDEX_OCR_", extra="ignore")

    api_key: str | None = None
    folder_id: str | None = None
    language_codes: list[str] = ["ru", "en"]  # noqa: RUF012 — pydantic-модель, не dataclass
    model: str = "page"
    timeout: float = 60.0


class YandexOcrClient:
    """OCR через Yandex Cloud Vision (``POST /ocr/v1/recognizeText``, ``mimeType=PDF``).

    https://cloud.yandex.ru/docs/vision/ocr/api-ref/TextRecognition/recognize

    Синхронный метод принимает PDF целиком (в т.ч. многостраничный) как base64
    в ``content`` — Yandex сам разбивает распознавание по страницам результата
    (``result.textAnnotation.pages[].blocks[].lines[].words[].text``), рендеринг
    страниц в изображения на нашей стороне не нужен.
    """

    _URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"

    def __init__(self, settings: YandexOcrSettings) -> None:
        if not settings.api_key or not settings.folder_id:
            raise ValueError("YANDEX_OCR_API_KEY и YANDEX_OCR_FOLDER_ID обязательны")
        # Отдельные поля (не settings.api_key/folder_id) — mypy не сужает
        # Optional-атрибут pydantic-модели по проверке выше при чтении в
        # другом методе; здесь тип уже точно ``str``.
        self._api_key: str = settings.api_key
        self._folder_id: str = settings.folder_id
        self._language_codes = settings.language_codes
        self._model = settings.model
        self._client = httpx.Client(timeout=settings.timeout)

    def recognize_pdf(self, raw: bytes) -> str | None:
        payload = {
            "mimeType": "PDF",
            "languageCodes": self._language_codes,
            "model": self._model,
            "content": base64.b64encode(raw).decode("ascii"),
        }
        headers = {
            "Authorization": f"Api-Key {self._api_key}",
            "x-folder-id": self._folder_id,
            "Content-Type": "application/json",
        }
        try:
            resp = self._client.post(self._URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Yandex OCR: сбой распознавания PDF: %s", exc)
            return None
        return _text_from_yandex_response(data)


def _text_from_yandex_response(data: dict[str, Any]) -> str | None:
    """Текст постранично из ответа Yandex OCR (строки — в порядке блоков/строк)."""
    pages = ((data.get("result") or {}).get("textAnnotation") or {}).get("pages") or []
    lines_out: list[str] = []
    for page in pages:
        for block in page.get("blocks") or []:
            for line in block.get("lines") or []:
                words = [w.get("text", "") for w in (line.get("words") or [])]
                if words:
                    lines_out.append(" ".join(words))
    text = "\n".join(lines_out).strip()
    return text or None


_lock = threading.Lock()
_override: Ocrable | None = None
_client: Ocrable | None = None


def set_client(client: Ocrable | None) -> None:
    """Подменить OCR-клиент (тесты); ``None`` — сбросить, следующий ``get_client()``
    пересчитает реальный (по актуальным на тот момент настройкам/окружению)."""
    global _override, _client
    with _lock:
        _override = client
        _client = None


def get_client() -> Ocrable | None:
    """OCR-клиент по настройкам окружения (ленивый синглтон — только успешно
    созданный реальный клиент кэшируется; «отключён/не настроен» не кэшируется
    и проверяется заново на каждый вызов — путь редкий (только сканы без
    текстового слоя), пересчёт дешёвый).

    ``None`` — OCR отключён (``OCR_PROVIDER=none``), не настроен (нет ключа/
    folder_id) или неизвестный провайдер — во всех случаях best-effort,
    вызывающий код просто не получит OCR-фолбэк.
    """
    global _client
    with _lock:
        if _override is not None:
            return _override
        if _client is not None:
            return _client
        provider = OcrSettings().provider.strip().lower()
        if provider in ("", "none"):
            return None
        if provider == "yandex":
            settings = YandexOcrSettings()
            if not settings.api_key or not settings.folder_id:
                logger.warning(
                    "OCR_PROVIDER=yandex, но YANDEX_OCR_API_KEY/YANDEX_OCR_FOLDER_ID "
                    "не заданы — OCR-фолбэк для сканов PDF отключён"
                )
                return None
            _client = YandexOcrClient(settings)
            return _client
        logger.warning("Неизвестный OCR_PROVIDER=%r — OCR-фолбэк отключён", provider)
        return None
