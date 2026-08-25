"""Рендер карточки закупки для уведомлений (HTML для Telegram/MAX)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

_HTML_ESCAPE = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
}


def _html_escape(text: str) -> str:
    """Экранирует HTML-сущности (значения приходят со скрейпленных страниц)."""
    return "".join(_HTML_ESCAPE.get(ch, ch) for ch in text)


def _as_text(value: Any) -> str | None:
    """Приводит значение записи к строке; ``None``/пусто → пропуск."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def render_telegram_message(record: dict[str, Any]) -> str:
    """HTML-карточка закупки для ``sendMessage`` (``parse_mode="HTML"``).

    Пустые поля пропускаются. Все значения экранируются (контент со скрейпленных
    страниц считается ненадёжным).
    """
    fields: list[tuple[str, Any]] = [
        ("№", "number"),
        ("Площадка", "platform_id"),
        ("Предмет", "subject"),
        ("Заказчик", "customer"),
        ("Закон", "law"),
        ("НМЦК", "nmck"),
        ("Опубликовано", "publication_date"),
        ("Срок подачи", "deadline"),
        ("Этап", "score_method"),
        ("Fit", "fit_score"),
        ("P(win)", "p_win"),
        ("Маржа", "margin"),
        ("Оценка", "score"),
    ]
    lines: list[str] = []
    for label, key in fields:
        value = _as_text(record.get(key))
        if value is None:
            continue
        lines.append(f"{label}: {_html_escape(value)}")
    url = _as_text(record.get("url"))
    if url is not None:
        escaped_url = _html_escape(url)
        lines.append(f'<a href="{escaped_url}">{escaped_url}</a>')
    return "\n".join(lines)
