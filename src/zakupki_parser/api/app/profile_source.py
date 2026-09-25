"""Заполнение профиля (компетенции + лицензии) по URL сайта поставщика.

Пайплайн: скачать страницу (SSRF-защищённо — публичные IP, http/https, ограниченный
размер и число редиректов) -> извлечь видимый текст (``markitdown``, тот же
конвертер, что и для файлов ТЗ) -> попросить LLM сформировать компетенции по
канонической схеме (``zakupki_parser.storage.competencies.Profile``) и лицензии,
сопоставленные со справочником ``license_types`` (LLM получает список id/название
и обязан выбрать один из них — иначе запись лицензии отбрасывается) -> результат
компетенций провалидировать той же схемой, что и ручной ввод/импорт профиля.

LLM — OpenAI-совместимый эндпоинт (по умолчанию DeepSeek, как у scoring_service/
analysis_service), настраивается отдельными env-переменными этого сервиса
(``ZAKUPKI_PROFILE_LLM_*``): ключ живёт только в env, никогда не в конфиг-файле.
"""

from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from zakupki_parser.net_safety import UnsafeUrlError, ensure_public_host
from zakupki_parser.storage.competencies import CompetenciesError, normalize_competencies

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml", "text/plain")
_MAX_PAGE_BYTES = 2_000_000
_MAX_REDIRECTS = 3
_MAX_TEXT_CHARS = 8000
_USER_AGENT = "Mozilla/5.0 (compatible; ZakupkiParserBot/1.0; +profile-from-url)"

_SYSTEM_PROMPT_TEMPLATE = """\
Ты помогаешь тендерологу заполнить профиль поставщика по тексту с сайта его
компании. Верни СТРОГО JSON без пояснений и без markdown-обрамления вида
```json, со следующей схемой:
{{
  "positioning": "позиционирование компании одним-двумя предложениями",
  "breadth": "broad" | "narrow",
  "competencies": [{{"area": "направление", "description": "что компания делает", \
"examples": ["пример работы/кейса"]}}],
  "exclusions": ["чего компания НЕ делает"],
  "licenses": [{{"name": "название лицензии/допуска/сертификата, как на сайте", \
"license_type_id": <id из списка ниже или null>, "number": "номер или null", \
"authority": "выдавший орган или null", "issue_date": "YYYY-MM-DD или null", \
"expiry_date": "YYYY-MM-DD или null", "notes": "уточнение или null"}}]
}}
Используй только факты из текста страницы, ничего не придумывай. Если сайт не даёт
однозначно понять узкую специализацию — используй "broad". Если данных для какого-то
поля нет — оставь его пустым (пустая строка/пустой список), не выдумывай.

Для "licenses": включай запись, если на сайте явно упомянута лицензия/допуск/
сертификат. "name" — обязательно, как лицензия названа на сайте (это поле
заполняется ВСЕГДА, независимо от совпадения со справочником). "license_type_id"
заполняй, ТОЛЬКО если название явно соответствует ОДНОМУ из типов из списка ниже —
тогда это ОБЯЗАТЕЛЬНО один из перечисленных id. Если подходящего типа в списке нет
— оставь "license_type_id": null (не выдумывай id и не подставляй ближайший по
смыслу) — запись всё равно верни, с "name" и найденными реквизитами. Реквизиты
(номер/орган/срок), которых нет на странице, — null.

Список типов лицензий (id: название):
{license_catalog}\
"""


class ProfileFromUrlError(Exception):
    """Пользовательская ошибка формирования профиля по URL (400)."""


class ProfileFromUrlNotConfigured(ProfileFromUrlError):
    """Функция не настроена: не задан LLM API-ключ этого сервиса (503)."""


@dataclass
class ProfileFromUrl:
    """Результат разбора сайта: компетенции (канонический JSON) + лицензии.

    ``licenses`` — записи с реальным ``license_type_id`` из справочника, готовые
    к сохранению как есть. ``unmatched_licenses`` — упомянутые на сайте лицензии,
    для которых LLM не нашла соответствия в справочнике (справочник не
    исчерпывающий): без ``license_type_id`` их нельзя сохранить как запись
    профиля (внешний ключ обязателен), но информация не должна теряться молча —
    показываются пользователю как есть, для ручного решения.
    """

    competencies: str
    licenses: list[dict[str, Any]] = field(default_factory=list)
    unmatched_licenses: list[dict[str, Any]] = field(default_factory=list)


async def _ensure_public_host(host: str) -> None:
    """SSRF-защита (``net_safety.ensure_public_host``) с ошибкой этого модуля."""
    try:
        await ensure_public_host(host)
    except UnsafeUrlError as exc:
        raise ProfileFromUrlError(str(exc)) from exc


def _llm_config() -> tuple[str, str, str, float]:
    base_url = os.environ.get("ZAKUPKI_PROFILE_LLM_BASE_URL", "https://api.deepseek.com/v1")
    api_key = os.environ.get("ZAKUPKI_PROFILE_LLM_API_KEY", "")
    model = os.environ.get("ZAKUPKI_PROFILE_LLM_MODEL", "deepseek-chat")
    timeout = float(os.environ.get("ZAKUPKI_PROFILE_LLM_TIMEOUT", "30"))
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
                raise ProfileFromUrlError("Поддерживаются только http/https URL")
            await _ensure_public_host(parsed.host)
            try:
                async with http.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise ProfileFromUrlError("Сервер вернул редирект без адреса")
                        current = str(httpx.URL(current).join(location))
                        continue
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "").lower()
                    if not any(t in content_type for t in _ALLOWED_CONTENT_TYPES):
                        ct = content_type or "неизвестен"
                        raise ProfileFromUrlError(
                            f"Страница не похожа на HTML (content-type: {ct})"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ProfileFromUrlError("Страница слишком большая")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                raise ProfileFromUrlError(f"Сайт ответил ошибкой {status}") from exc
            except httpx.TransportError as exc:
                raise ProfileFromUrlError(f"Не удалось обратиться к сайту: {exc}") from exc
        raise ProfileFromUrlError("Слишком много редиректов")
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
        raise ProfileFromUrlError("Не удалось разобрать содержимое страницы") from exc
    if not text:
        raise ProfileFromUrlError("На странице не найдено текста")
    return text[:_MAX_TEXT_CHARS]


def _build_messages(
    text: str, source_url: str, license_types: list[tuple[int, str]]
) -> tuple[str, str]:
    catalog = (
        "\n".join(f"{type_id}: {name}" for type_id, name in license_types) or "(справочник пуст)"
    )
    system = _SYSTEM_PROMPT_TEMPLATE.format(license_catalog=catalog)
    user = f"URL источника: {source_url}\n\nТекст страницы:\n{text}"
    return system, user


async def _call_llm(system: str, user: str, *, client: httpx.AsyncClient | None = None) -> str:
    base_url, api_key, model, timeout = _llm_config()
    if not api_key:
        raise ProfileFromUrlNotConfigured(
            "Заполнение профиля по URL не настроено: не задан "
            "ZAKUPKI_PROFILE_LLM_API_KEY (тот же ключ, что и у scoring_service/"
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
        raise ProfileFromUrlError(f"LLM ответила ошибкой {exc.response.status_code}") from exc
    except httpx.TransportError as exc:
        raise ProfileFromUrlError(f"LLM недоступна: {exc}") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise ProfileFromUrlError("LLM вернула некорректный ответ") from exc
    finally:
        if owns_client:
            await http.aclose()


def _clean_str(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _clean_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _split_licenses(
    raw: Any, valid_ids: set[int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Лицензии из ответа LLM: сопоставленные со справочником / нет.

    Запись с валидным (существующим) ``license_type_id`` идёт в первый список —
    готова к сохранению как ``LicenseIn`` напрямую. Запись без соответствия
    (LLM вернула ``null`` — справочник не исчерпывающий, или выдумала
    несуществующий id — такое тоже трактуется как «нет соответствия», подставить
    чужой ``license_type_id`` нельзя, это внешний ключ) идёт во второй: без
    привязки к типу её нельзя сохранить как запись профиля, но сама информация
    (название/номер/орган/срок) не теряется — возвращается для показа
    пользователю.
    """
    if not isinstance(raw, list):
        return [], []
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        common = {
            "number": _clean_str(item.get("number")),
            "authority": _clean_str(item.get("authority")),
            "issue_date": _clean_date(item.get("issue_date")),
            "expiry_date": _clean_date(item.get("expiry_date")),
            "notes": _clean_str(item.get("notes")),
        }
        type_id = item.get("license_type_id")
        if isinstance(type_id, int) and type_id in valid_ids:
            matched.append({"license_type_id": type_id, **common})
            continue
        name = _clean_str(item.get("name"))
        if name is None:
            continue  # ни названия, ни валидного типа — показывать нечего
        unmatched.append({"name": name, **common})
    return matched, unmatched


async def generate_profile_from_url(
    url: str,
    license_types: list[tuple[int, str]],
    *,
    page_text: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ProfileFromUrl:
    """URL сайта поставщика -> компетенции (схема ``Profile``) + лицензии.

    Компетенции уже провалидированы ``normalize_competencies`` — можно напрямую
    вернуть в веб-форму (та же схема, что при ручном заполнении/импорте).
    ``page_text`` — уже собранный текст первой страницы сайта (сайт-источник):
    передан — страница не скачивается повторно.
    Лицензии сопоставлены с переданным справочником ``license_types``
    (``(id, name)``): с найденным типом — в ``licenses`` (готовы к сохранению),
    без — в ``unmatched_licenses`` (справочник не исчерпывающий; сохранить как
    запись профиля нельзя — обязателен внешний ключ, но текст с сайта не
    теряется, показывается пользователю). Ничего не сохраняется автоматически:
    пользователь проверяет/правит перед «Сохранить профиль».
    """
    stripped = url.strip()
    if not stripped:
        raise ProfileFromUrlError("Укажите URL сайта")
    if page_text and page_text.strip():
        # Текст первой страницы уже собран (сайт-источник, S3) — не скачиваем заново.
        text = page_text.strip()[:_MAX_TEXT_CHARS]
    else:
        text = html_to_text(await fetch_url_html(stripped, client=http_client))
    system, user = _build_messages(text, stripped, license_types)
    raw = await _call_llm(system, user, client=http_client)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileFromUrlError("LLM вернула ответ в неверном формате: не JSON") from exc
    if not isinstance(data, dict):
        raise ProfileFromUrlError("LLM вернула ответ в неверном формате: не JSON-объект")
    try:
        competencies = normalize_competencies(json.dumps(data, ensure_ascii=False))
    except CompetenciesError as exc:
        raise ProfileFromUrlError(f"LLM вернула компетенции в неверном формате: {exc}") from exc
    valid_ids = {type_id for type_id, _ in license_types}
    licenses, unmatched_licenses = _split_licenses(data.get("licenses"), valid_ids)
    return ProfileFromUrl(
        competencies=competencies, licenses=licenses, unmatched_licenses=unmatched_licenses
    )
