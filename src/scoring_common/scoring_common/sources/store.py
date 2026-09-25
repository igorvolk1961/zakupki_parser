"""Хранилище текстов сайтов-источников в S3/MinIO.

Текст сайта, собранный по всем страницам пагинации (``zakupki_parser.sources``),
— это ДАННЫЕ, а не ускорение: в отличие от кэша текста документов
(``tz/object_cache.py``) ошибки обращения к хранилищу здесь пробрасываются.
Отсутствующий объект — ``None`` (источник ещё не собирался).

Ключ объекта — хэш нормализованного URL: один URL — один объект, кто бы его
ни запросил (URL в условии поля, сайт профиля).

Формат полного текста: страницы подряд, перед каждой — маркер
``=== page N: <url> ===`` на отдельной строке (``PAGE_MARKER_RE``). Маркер
отделяет страницы при поиске «между значениями» (окно не переходит на
следующую страницу) и помогает при отладке.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from scoring_common.object_storage import get_client, get_settings

PAGE_MARKER_RE = re.compile(r"^=== page (\d+): (.*) ===$", re.MULTILINE)


def _digest(url_norm: str) -> str:
    return hashlib.sha256(url_norm.encode("utf-8")).hexdigest()


def text_key(url_norm: str) -> str:
    """Ключ полного текста источника (все страницы)."""
    return f"sources/{_digest(url_norm)}/text.txt"


def first_page_key(url_norm: str) -> str:
    """Ключ исходного текста первой страницы (без удаления шапки/подвала)."""
    return f"sources/{_digest(url_norm)}/first-page.txt"


def join_pages(pages: Iterable[tuple[str, str]]) -> str:
    """``[(url, text)]`` -> полный текст источника с маркерами страниц."""
    return "\n\n".join(
        f"=== page {n}: {url} ===\n{text}" for n, (url, text) in enumerate(pages, start=1)
    )


def split_pages(text: str) -> list[tuple[str, str]]:
    """Полный текст источника -> ``[(url, text)]`` (обратное ``join_pages``)."""
    marks = list(PAGE_MARKER_RE.finditer(text))
    pages: list[tuple[str, str]] = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        pages.append((mark.group(2), text[mark.end() + 1 : end].rstrip("\n")))
    return pages


def put_text(key: str, text: str) -> None:
    """Записать текст (ошибка хранилища пробрасывается)."""
    get_client().put_object(
        Bucket=get_settings().sources_bucket,
        Key=key,
        Body=text.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )


def get_text(key: str) -> str | None:
    """Прочитать текст; ``None`` — объекта нет. Прочие ошибки пробрасываются."""
    try:
        obj = get_client().get_object(Bucket=get_settings().sources_bucket, Key=key)
    except Exception as exc:
        if _is_missing(exc):
            return None
        raise
    body: bytes = obj["Body"].read()
    return body.decode("utf-8")


def _is_missing(exc: Exception) -> bool:
    if isinstance(exc, KeyError):  # InMemoryS3
        return True
    response = getattr(exc, "response", None)  # botocore.exceptions.ClientError
    code = (response or {}).get("Error", {}).get("Code") if isinstance(response, dict) else None
    return code in ("NoSuchKey", "404")
