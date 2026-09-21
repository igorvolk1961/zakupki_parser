"""Заполнение компетенций профиля по URL сайта поставщика.

Пайплайн: скачать страницу (SSRF-защищённо — публичные IP, http/https, ограниченный
размер и число редиректов) -> извлечь видимый текст (``markitdown``, тот же
конвертер, что и для файлов ТЗ) -> попросить LLM сформировать компетенции по
канонической схеме (``zakupki_parser.storage.competencies.Profile``) -> провалидировать
результат той же схемой, что и ручной ввод/импорт профиля.

LLM — OpenAI-совместимый эндпоинт (по умолчанию DeepSeek, как у scoring_service/
analysis_service), настраивается отдельными env-переменными этого сервиса
(``ZAKUPKI_COMPETENCIES_LLM_*``): ключ живёт только в env, никогда не в конфиг-файле.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import logging
import os
import socket
from typing import Any

import httpx

from zakupki_parser.storage.competencies import CompetenciesError, normalize_competencies

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml", "text/plain")
_MAX_PAGE_BYTES = 2_000_000
_MAX_REDIRECTS = 3
_MAX_TEXT_CHARS = 8000
_USER_AGENT = "Mozilla/5.0 (compatible; ZakupkiParserBot/1.0; +profile-competencies)"

_SYSTEM_PROMPT = """\
Ты помогаешь тендерологу заполнить профиль компетенций поставщика по тексту с сайта
его компании. Верни СТРОГО JSON без пояснений и без markdown-обрамления вида
```json, со следующей схемой:
{
  "positioning": "позиционирование компании одним-двумя предложениями",
  "breadth": "broad" | "narrow",
  "competencies": [{"area": "направление", "description": "что компания делает", \
"examples": ["пример работы/кейса"]}],
  "exclusions": ["чего компания НЕ делает"]
}
Используй только факты из текста страницы, ничего не придумывай. Если сайт не даёт
однозначно понять узкую специализацию — используй "broad". Если данных для какого-то
поля нет — оставь его пустым (пустая строка/пустой список), не выдумывай.\
"""


class CompetenciesUrlError(Exception):
    """Пользовательская ошибка формирования компетенций по URL (400)."""


class CompetenciesUrlNotConfigured(CompetenciesUrlError):
    """Функция не настроена: не задан LLM API-ключ этого сервиса (503)."""


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True, если адрес — публичный маршрутизируемый IP (не внутренняя сеть)."""
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


async def _ensure_public_host(host: str) -> None:
    """SSRF-защита: запрещает URL, ведущие на внутренние/локальные адреса.

    Резолвит хост через DNS (или разбирает IP-литерал) и требует, чтобы ВСЕ
    полученные адреса были публичными. Не защищает от DNS rebinding (сервер меняет
    ответ между этой проверкой и фактическим запросом) — компромисс ради простоты:
    вызывающие эндпоинт — уже аутентифицированные пользователи приложения, не
    анонимные третьи лица.
    """
    if not host:
        raise CompetenciesUrlError("В URL не указан хост")
    try:
        infos = await _resolve(host)
    except OSError as exc:
        raise CompetenciesUrlError(f"Не удалось разрешить адрес «{host}»") from exc
    if not infos:
        raise CompetenciesUrlError(f"Не удалось разрешить адрес «{host}»")
    if not all(_is_public_ip(ip) for ip in infos):
        raise CompetenciesUrlError(
            "URL указывает на внутренний/локальный адрес — такие адреса запрещены"
        )


async def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    return [ipaddress.ip_address(sockaddr[0]) for _, _, _, _, sockaddr in infos]


def _llm_config() -> tuple[str, str, str, float]:
    base_url = os.environ.get("ZAKUPKI_COMPETENCIES_LLM_BASE_URL", "https://api.deepseek.com/v1")
    api_key = os.environ.get("ZAKUPKI_COMPETENCIES_LLM_API_KEY", "")
    model = os.environ.get("ZAKUPKI_COMPETENCIES_LLM_MODEL", "deepseek-chat")
    timeout = float(os.environ.get("ZAKUPKI_COMPETENCIES_LLM_TIMEOUT", "30"))
    return base_url, api_key, model, timeout


async def fetch_url_html(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = _MAX_PAGE_BYTES,
    max_redirects: int = _MAX_REDIRECTS,
    timeout: float = 10.0,
) -> bytes:
    """Скачивает страницу по URL (SSRF-защищённо, с ограничением размера).

    Редиректы разбираются вручную (не ``httpx follow_redirects``), чтобы каждый
    hop прошёл ту же проверку на публичный хост, что и исходный URL.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(
        follow_redirects=False, timeout=timeout, headers={"User-Agent": _USER_AGENT}
    )
    try:
        current = url
        for _ in range(max_redirects + 1):
            parsed = httpx.URL(current)
            if parsed.scheme not in _ALLOWED_SCHEMES:
                raise CompetenciesUrlError("Поддерживаются только http/https URL")
            await _ensure_public_host(parsed.host)
            try:
                async with http.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise CompetenciesUrlError("Сервер вернул редирект без адреса")
                        current = str(httpx.URL(current).join(location))
                        continue
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "").lower()
                    if not any(t in content_type for t in _ALLOWED_CONTENT_TYPES):
                        ct = content_type or "неизвестен"
                        raise CompetenciesUrlError(
                            f"Страница не похожа на HTML (content-type: {ct})"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise CompetenciesUrlError("Страница слишком большая")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                raise CompetenciesUrlError(f"Сайт ответил ошибкой {status}") from exc
            except httpx.TransportError as exc:
                raise CompetenciesUrlError(f"Не удалось обратиться к сайту: {exc}") from exc
        raise CompetenciesUrlError("Слишком много редиректов")
    finally:
        if owns_client:
            await http.aclose()


def html_to_text(html: bytes) -> str:
    """Видимый текст страницы (markitdown -> markdown -> обрезка по длине)."""
    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert_stream(io.BytesIO(html), file_extension=".html")
        text = (result.text_content or "").strip()
    except Exception as exc:  # noqa: BLE001 - битая/нестандартная разметка
        raise CompetenciesUrlError("Не удалось разобрать содержимое страницы") from exc
    if not text:
        raise CompetenciesUrlError("На странице не найдено текста")
    return text[:_MAX_TEXT_CHARS]


def _build_messages(text: str, source_url: str) -> tuple[str, str]:
    user = f"URL источника: {source_url}\n\nТекст страницы:\n{text}"
    return _SYSTEM_PROMPT, user


async def _call_llm(system: str, user: str, *, client: httpx.AsyncClient | None = None) -> str:
    base_url, api_key, model, timeout = _llm_config()
    if not api_key:
        raise CompetenciesUrlNotConfigured(
            "Заполнение компетенций по URL не настроено: не задан "
            "ZAKUPKI_COMPETENCIES_LLM_API_KEY (тот же ключ, что и у scoring_service/"
            "analysis_service, если используется тот же провайдер)"
        )
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        url = f"{base_url.rstrip('/')}/chat/completions"
        resp = await http.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"])
    except httpx.HTTPStatusError as exc:
        raise CompetenciesUrlError(f"LLM ответила ошибкой {exc.response.status_code}") from exc
    except httpx.TransportError as exc:
        raise CompetenciesUrlError(f"LLM недоступна: {exc}") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise CompetenciesUrlError("LLM вернула некорректный ответ") from exc
    finally:
        if owns_client:
            await http.aclose()


async def generate_competencies_from_url(
    url: str, *, http_client: httpx.AsyncClient | None = None
) -> str:
    """URL сайта поставщика -> канонический JSON компетенций (схема ``Profile``).

    Результат уже провалидирован ``normalize_competencies`` — можно напрямую
    вернуть в веб-форму (та же схема, что при ручном заполнении/импорте) без
    автосохранения: пользователь проверяет/правит перед «Сохранить профиль».
    """
    stripped = url.strip()
    if not stripped:
        raise CompetenciesUrlError("Укажите URL сайта")
    html = await fetch_url_html(stripped, client=http_client)
    text = html_to_text(html)
    system, user = _build_messages(text, stripped)
    raw = await _call_llm(system, user, client=http_client)
    try:
        return normalize_competencies(raw)
    except CompetenciesError as exc:
        raise CompetenciesUrlError(f"LLM вернула компетенции в неверном формате: {exc}") from exc
