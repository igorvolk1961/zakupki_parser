#!/usr/bin/env python3
"""Утилита для проверки доставки сообщений в канал MAX с известным chat_id.

Отправляет тестовое сообщение в канал и сообщает результат. Токен берётся из
env ``ZAKUPKI_MAX_TOKEN`` (или из ``.env`` в корне проекта), ``chat_id`` — из
env ``ZAKUPKI_MAX_CHAT_ID`` или из аргумента ``--chat-id``.

Примеры:
    uv run scripts/test_max_chat.py
    uv run scripts/test_max_chat.py --chat-id 123456789012345678
    uv run scripts/test_max_chat.py --insecure --text "Проверка доставки"
"""

import argparse
import os
import warnings
from pathlib import Path

import requests
from dotenv import load_dotenv
from urllib3.exceptions import InsecureRequestWarning

DEFAULT_URL = "https://platform-api2.max.ru/messages"
TOKEN_ENV = "ZAKUPKI_MAX_TOKEN"
CHAT_ID_ENV = "ZAKUPKI_MAX_CHAT_ID"


def _load_secrets() -> tuple[str, str]:
    """Возвращает (token, chat_id) из env/.env или завершает работу с ошибкой."""
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    token = os.environ.get(TOKEN_ENV)
    chat_id = os.environ.get(CHAT_ID_ENV)
    if not token:
        raise SystemExit(f"Токен не задан. Укажите переменную {TOKEN_ENV} (в env или в .env).")
    if not chat_id:
        raise SystemExit(f"chat_id не задан. Укажите переменную {CHAT_ID_ENV} или --chat-id.")
    return token.strip(), chat_id.strip()


def send_test_message(token: str, chat_id: str, url: str, text: str, insecure: bool) -> None:
    """Шлёт тестовое сообщение в канал и проверяет ответ платформы."""
    session = requests.Session()
    session.headers.update({"Authorization": token})
    if insecure:
        warnings.filterwarnings("ignore", category=InsecureRequestWarning)

    payload = {"text": text, "format": "html", "disable_link_preview": True}
    full_url = f"{url}?chat_id={chat_id}"
    try:
        response = session.post(full_url, json=payload, verify=not insecure, timeout=30)
    except requests.RequestException as exc:
        raise SystemExit(f"Ошибка запроса: {exc}") from exc

    if response.status_code >= 400:
        raise SystemExit(f"Платформа вернула {response.status_code}: {response.text.strip()}")

    print(f"Сообщение отправлено (HTTP {response.status_code}).")
    print(f"Текст: {text}")
    print(f"chat_id: {chat_id}")
    print("Проверьте канал — там должно появиться это сообщение.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-id", help=f"chat_id канала (по умолчанию из env {CHAT_ID_ENV})")
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"URL отправки (по умолчанию: {DEFAULT_URL})"
    )
    parser.add_argument(
        "--text",
        default="<b>Тест</b>: проверка доставки в канал MAX",
        help="Текст (HTML) тестового сообщения",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Отключить проверку TLS-сертификата (сертификат MAX может отсутствовать в доверенных)",
    )
    args = parser.parse_args()

    token, env_chat_id = _load_secrets()
    chat_id = args.chat_id or env_chat_id
    send_test_message(token, chat_id, args.url, args.text, args.insecure)


if __name__ == "__main__":
    main()
