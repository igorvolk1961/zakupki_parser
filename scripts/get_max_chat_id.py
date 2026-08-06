#!/usr/bin/env python3
"""Утилита для определения числового chat_id канала MAX.

Токен бота берётся из env ``ZAKUPKI_MAX_TOKEN`` (или из ``.env`` в корне
проекта). Подпишитесь на события Long Polling и напишите сообщение в канал
(или добавьте бота) — утилита поймает событие и выведет ``chat_id`` канала.

Примеры:
    uv run scripts/get_max_chat_id.py
    uv run scripts/get_max_chat_id.py --timeout 120 --url https://platform-api2.max.ru
"""

import argparse
import os
import time
import warnings
from pathlib import Path

import requests
from dotenv import load_dotenv
from urllib3.exceptions import InsecureRequestWarning

DEFAULT_URL = "https://platform-api2.max.ru/updates"
TOKEN_ENV = "ZAKUPKI_MAX_TOKEN"


def _load_token() -> str:
    """Возвращает токен из env/.env или завершает работу с понятной ошибкой."""
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"Токен не задан. Укажите переменную {TOKEN_ENV} (в env или в .env).")
    return token.strip()


def poll_for_chat_id(token: str, url: str, timeout: float, insecure: bool = False) -> str:
    """Long Polling: ждёт событие и возвращает первый chat_id канала."""
    session = requests.Session()
    session.headers.update({"Authorization": token})
    if insecure:
        warnings.filterwarnings("ignore", category=InsecureRequestWarning)

    print(f"Ожидание события (Long Polling, до {timeout:.0f} сек)…")
    print("Напишите сообщение в канал или добавьте бота, чтобы получить chat_id.")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = session.get(url, timeout=30, verify=not insecure)
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"Ошибка запроса: {exc}")
            time.sleep(2)
            continue

        for update in response.json().get("updates", []):
            if update.get("update_type") != "message_created":
                continue
            message = update.get("message", {})
            recipient = message.get("recipient", {})
            if recipient.get("chat_type") == "channel":
                chat_id = recipient.get("chat_id")
                print(f"Найден chat_id канала: {chat_id}")
                return str(chat_id)
        time.sleep(1)

    raise SystemExit(
        "События не получены за отведённое время. Проверьте, что бот добавлен "
        "в канал и туда было отправлено сообщение."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"URL Long Polling (по умолчанию: {DEFAULT_URL})"
    )
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="Сколько ждать событие, сек (по умолчанию: 60)"
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Отключить проверку TLS-сертификата (нужно, если сертификат MAX не в доверенных)",
    )
    args = parser.parse_args()

    chat_id = poll_for_chat_id(_load_token(), args.url, args.timeout, args.insecure)
    print(f'\nИспользуйте в конфиге: chat_id: "{chat_id}"')


if __name__ == "__main__":
    main()
