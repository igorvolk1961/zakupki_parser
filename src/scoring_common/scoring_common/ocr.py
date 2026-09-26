"""OCR для сканов PDF без текстового слоя — сменяемая модель (как эмбеддинги/LLM).

Используется ТОЛЬКО как fallback в ``scoring_common.tz.extractors._extract_pdf``,
когда прямое извлечение текста не дало результата (ни ``pdf_to_markdown_tables``
(pdfplumber), ни MarkItDown не нашли текстовый слой — весь PDF или его часть
является сканом без текста). Прямое извлечение всегда пробуется первым — OCR
дороже и медленнее.

Провайдер выбирается переменной окружения ``OCR_PROVIDER``:

* ``yandex`` (по умолчанию) — облачный Yandex Cloud Vision; если ключ/folder_id
  не заданы ИЛИ сам запрос к сервису не удался (сеть/таймаут/HTTP-ошибка —
  ``YandexOcrClient.recognize_pdf`` уже гасит это в ``None``), автоматически
  подключается локальный ``tesseract`` как резервный вариант (``ChainOcrClient``);
* ``tesseract`` — только локальный OCR, без обращения к Yandex вовсе;
* ``none`` — OCR-фолбэк отключён целиком, поведение как до появления модуля.

Добавить нового облачного провайдера — новый класс с тем же протоколом
``Ocrable`` + своя ветка в ``get_client()`` (по образцу ``yandex``), вызывающий
код не меняется.

Настройки — из окружения процесса (``OCR_``/``YANDEX_OCR_`` namespace), не из
``settings.py`` конкретного сервиса — как ``OBJECT_STORAGE_``
(см. ``scoring_common.object_storage``): один набор переменных на весь стек.

Best-effort: не настроен/сбой — ``get_client()``/``recognize_pdf`` возвращают
``None``, вызывающий код продолжает как при сканах без OCR вовсе (не роняет
задание).
"""

from __future__ import annotations

import base64
import io
import logging
import shutil
import threading
import time
from typing import Any, Protocol

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from scoring_common.costing import ocr_cost_rub, ocr_cost_usd

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
    resolution: int = 200  # dpi рендера страницы PDF в изображение (pdfplumber)


class YandexOcrClient:
    """OCR через Yandex Cloud Vision (``POST /ocr/v1/recognizeText``, постранично).

    https://aistudio.yandex.ru/docs/vision/ocr/api-ref/TextRecognition/recognize

    ``mime_type=PDF`` у синхронного ``recognizeText`` заявлен в API, но на
    практике ненадёжен: реальный многостраничный PDF и даже искусственный
    одностраничный (``Pillow``-конвертация) оба дали ``400 Can't decode
    image`` — не помог ни один из наших тестовых файлов (при том, что
    документация сама ограничивает синхронный метод ОДНОЙ страницей PDF).
    Обходим рендером каждой страницы в JPEG через ``pdfplumber`` (тот же
    механизм, что и у ``TesseractOcrClient``) и отдельным запросом на
    страницу — так и с ограничением в 1 страницу проблем нет, и формат
    декодируется надёжно (проверено вживую на реальных сканах). Ответ на
    одно изображение — не ``result.textAnnotation.pages[]``, а прямо
    ``result.textAnnotation.fullText``.

    Платный сервис (0.1321 ₽/страница, см. ``scoring_common.costing`` —
    ``ocr_cost_rub``/``ocr_cost_usd``) — биллинговая единица Yandex это КАЖДЫЙ
    успешно выполненный запрос распознавания одного изображения/страницы,
    поэтому счётчик ``pages_billed`` растёт только на реально оплаченных
    (успешных) запросах, а не на попытках, отклонённых retry-логикой
    (429/5xx до финального успеха или отказа).
    """

    _URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
    _RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
    _MAX_RETRY_ATTEMPTS = 4
    _RETRY_BASE_DELAY_SECONDS = 2.0

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
        self._resolution = settings.resolution
        self._client = httpx.Client(timeout=settings.timeout)
        self._pages_billed = 0

    @property
    def pages_billed(self) -> int:
        """Число успешно оплаченных запросов распознавания с последнего ``reset_cost``."""
        return self._pages_billed

    @property
    def cost_usd(self) -> float:
        """Стоимость с последнего ``reset_cost`` в USD (для единообразия с LLM/эмбеддингами)."""
        return ocr_cost_usd(self._pages_billed)

    @property
    def cost_rub(self) -> float:
        """Стоимость с последнего ``reset_cost`` в рублях (нативная валюта тарифа)."""
        return ocr_cost_rub(self._pages_billed)

    def reset_cost(self) -> None:
        """Сбросить счётчик оплаченных страниц (перед прогоном, как у LLM/эмбеддингов)."""
        self._pages_billed = 0

    def recognize_pdf(self, raw: bytes) -> str | None:
        try:
            import pdfplumber
        except ImportError as exc:  # noqa: BLE001 - опциональная зависимость
            logger.warning("Yandex OCR: недоступен pdfplumber для рендера страниц: %s", exc)
            return None
        try:
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                images = [page.to_image(resolution=self._resolution).original for page in pdf.pages]
        except Exception as exc:  # noqa: BLE001 - битый PDF, best-effort
            logger.warning("Yandex OCR: не удалось отрендерить страницы PDF: %s", exc)
            return None
        texts: list[str] = []
        for image in images:
            text = self._recognize_image(image)
            if text:
                texts.append(text)
        return "\n\n".join(texts).strip() or None

    def _recognize_image(self, image: Any, attempt: int = 0) -> str | None:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG")
        payload = {
            "mimeType": "JPEG",
            "languageCodes": self._language_codes,
            "model": self._model,
            "content": base64.b64encode(buf.getvalue()).decode("ascii"),
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
            self._pages_billed += 1  # тарифицируется по факту успешного ответа Yandex
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code in self._RETRYABLE_STATUS_CODES
                and attempt < self._MAX_RETRY_ATTEMPTS
            ):
                delay = self._RETRY_BASE_DELAY_SECONDS * (2**attempt)
                logger.warning(
                    "Yandex OCR: %s на странице, повтор через %.1fс (попытка %d/%d)",
                    exc.response.status_code,
                    delay,
                    attempt + 1,
                    self._MAX_RETRY_ATTEMPTS,
                )
                time.sleep(delay)
                return self._recognize_image(image, attempt=attempt + 1)
            logger.warning("Yandex OCR: сбой распознавания страницы: %s", exc)
            return None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Yandex OCR: сбой распознавания страницы: %s", exc)
            return None
        return _text_from_yandex_response(data)


def _text_from_yandex_response(data: dict[str, Any]) -> str | None:
    """``fullText`` одной страницы (ответ ``recognizeText`` на одно изображение)."""
    text = ((data.get("result") or {}).get("textAnnotation") or {}).get("fullText") or ""
    text = text.strip()
    return text or None


class TesseractOcrSettings(BaseSettings):
    """Параметры локального OCR через системный ``tesseract``."""

    model_config = SettingsConfigDict(env_prefix="TESSERACT_OCR_", extra="ignore")

    languages: str = "rus+eng"  # см. `tesseract --list-langs`, языки через "+"
    resolution: int = 200  # dpi рендера страницы PDF в изображение (pdfplumber)


class TesseractOcrClient:
    """Локальный OCR — резервный вариант без сети/API-ключа.

    Требует установленный бинарник ``tesseract`` в системе (apt: ``tesseract-ocr``,
    ``tesseract-ocr-rus`` для русского языка) — best-effort: если бинарника нет,
    ``recognize_pdf`` возвращает ``None`` (не бросает исключение), как отсутствие
    LibreOffice/catdoc/antiword у ``scoring_common.tz.extractors._extract_doc``.
    Страницы PDF рендерятся в изображения через уже используемый pdfplumber
    (транзитивная зависимость ``markitdown[pdf]``) — Yandex OCR принимает PDF
    целиком, а pytesseract работает только с изображениями.
    """

    def __init__(self, settings: TesseractOcrSettings | None = None) -> None:
        self._settings = settings or TesseractOcrSettings()

    def recognize_pdf(self, raw: bytes) -> str | None:
        if shutil.which("tesseract") is None:
            logger.warning(
                "Локальный OCR-фолбэк недоступен: бинарник tesseract не найден "
                "в PATH (apt install tesseract-ocr tesseract-ocr-rus)"
            )
            return None
        try:
            import pdfplumber
            import pytesseract
        except ImportError as exc:  # noqa: BLE001 - опциональная зависимость
            logger.warning("Локальный OCR-фолбэк недоступен: %s", exc)
            return None
        try:
            texts: list[str] = []
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                for page in pdf.pages:
                    image = page.to_image(resolution=self._settings.resolution).original
                    page_text = pytesseract.image_to_string(image, lang=self._settings.languages)
                    if page_text.strip():
                        texts.append(page_text.strip())
        except Exception as exc:  # noqa: BLE001 - битый PDF/сбой tesseract, best-effort
            logger.warning("Tesseract: сбой распознавания PDF: %s", exc)
            return None
        text = "\n\n".join(texts).strip()
        return text or None


class ChainOcrClient:
    """Пробует несколько OCR-провайдеров по порядку — первый непустой результат.

    Нужен, чтобы облачный провайдер (Yandex) при отсутствии ключа или сбое
    сети/API не оставлял скан вовсе нераспознанным — локальный ``tesseract``
    (если установлен) подхватывает как резервный вариант.
    """

    def __init__(self, providers: list[Ocrable]) -> None:
        self._providers = providers

    def recognize_pdf(self, raw: bytes) -> str | None:
        for provider in self._providers:
            text = provider.recognize_pdf(raw)
            if text:
                return text
        return None


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
    """OCR-клиент по настройкам окружения (ленивый синглтон — пересчитывается,
    только пока не собран ни один провайдер; путь редкий (только сканы без
    текстового слоя), пересчёт дешёвый).

    ``OCR_PROVIDER=yandex`` (по умолчанию): при заданных
    ``YANDEX_OCR_API_KEY``/``YANDEX_OCR_FOLDER_ID`` — цепочка Yandex ->
    tesseract (сбой/сеть Yandex на конкретном документе -> пробуется
    tesseract); без ключа/folder_id — сразу tesseract (если бинарник не
    найден, ``TesseractOcrClient.recognize_pdf`` сама вернёт ``None``).
    ``OCR_PROVIDER=tesseract`` — только локальный OCR, Yandex не участвует.
    ``OCR_PROVIDER=none`` или неизвестное значение — OCR-фолбэк отключён
    целиком (``None``).
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
        if provider == "tesseract":
            _client = TesseractOcrClient()
            return _client
        if provider == "yandex":
            settings = YandexOcrSettings()
            if not settings.api_key or not settings.folder_id:
                logger.warning(
                    "OCR_PROVIDER=yandex, но YANDEX_OCR_API_KEY/YANDEX_OCR_FOLDER_ID "
                    "не заданы — используется только локальный OCR-фолбэк (tesseract)"
                )
                _client = TesseractOcrClient()
                return _client
            _client = ChainOcrClient([YandexOcrClient(settings), TesseractOcrClient()])
            return _client
        logger.warning("Неизвестный OCR_PROVIDER=%r — OCR-фолбэк отключён", provider)
        return None
