#!/usr/bin/env python3
"""Утилита для удаления всех сообщений/постов из канала MAX.

Перечисляет сообщения через ``GET /messages?chat_id=...`` (бот должен быть
администратором канала) и удаляет каждое через ``DELETE /messages?message_id=...``
с соблюдением лимита платформы (не более 2 удалений в секунду в одном канале).

Токен берётся из env ``ZAKUPKI_MAX_TOKEN`` (или из ``.env`` в корне проекта),
``chat_id`` — из env ``ZAKUPKI_MAX_CHAT_ID`` или из аргумента ``--chat-id``.

Примеры:
    uv run scripts/delete_max_messages.py --dry-run   # только перечислить
    uv run scripts/delete_max_messages.py --confirm   # удалить всё (после подтверждения)
    uv run scripts/delete_max_messages.py --insecure
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

DEFAULT_API = "https://platform-api2.max.ru"
TOKEN_ENV = "ZAKUPKI_MAX_TOKEN"
CHAT_ID_ENV = "ZAKUPKI_MAX_CHAT_ID"
# Платформенный лимит: не более 2 удалений/сек в одном канале.
DELETE_DELAY = 0.55
# Пагинация: максимум сообщений за один GET.
PAGE_SIZE = 100


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


def _verify_flag(token: str, api: str, insecure: bool) -> bool:
    """Возвращает ``verify`` для httpx (True — проверять сертификат).

    Если сертификат MAX (Минцифры) не в доверенных, предупреждает и отключает
    проверку TLS автоматически; явный ``--insecure`` даёт то же самое без
    маршрута пробного запроса.
    """
    if insecure:
        return False
    try:
        with httpx.Client(headers={"Authorization": token}, verify=True, timeout=15.0) as client:
            client.get(f"{api}/me")
    except httpx.ConnectError as exc:
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            print(
                "Внимание: сертификат MAX (Минцифры) не в доверенных — проверка TLS "
                "отключена. Можно явно передать --insecure или добавить сертификат в "
                "доверенные.",
                file=sys.stderr,
            )
            return False
        raise
    return True


def _message_id(msg: dict[str, Any]) -> str | None:
    """Достаёт message_id из объекта сообщения (идентификатор лежит в ``body.mid``)."""
    body = msg.get("body")
    if isinstance(body, dict) and body.get("mid"):
        return str(body["mid"])
    for key in ("id", "message_id"):
        if msg.get(key):
            return str(msg[key])
    recipient = msg.get("recipient")
    if isinstance(recipient, dict):
        for key in ("message_id", "id"):
            if recipient.get(key):
                return str(recipient[key])
    return None


def fetch_message_ids(client: httpx.Client, api: str, chat_id: str, dry_run: bool) -> list[str]:
    """Перечисляет все message_id канала, пагинируясь назад по времени."""
    ids: list[str] = []
    seen: set[str] = set()
    from_ms: int | None = None

    while True:
        params: dict[str, Any] = {"chat_id": chat_id, "count": PAGE_SIZE}
        # from — верхняя временная граница (мс); берём на 1 мс строже,
        # чтобы не зациклиться на текущем хвосте.
        if from_ms is not None:
            params["from"] = from_ms - 1

        resp = client.get(f"{api}/messages", params=params)
        resp.raise_for_status()
        messages = resp.json().get("messages") or []

        if not messages:
            break

        added_any = False
        for msg in messages:
            mid = _message_id(msg)
            if mid is None or mid in seen:
                continue
            seen.add(mid)
            ids.append(mid)
            added_any = True

            ts = msg.get("timestamp")
            if isinstance(ts, (int, float)) and ts > 0:
                ts_int = int(ts)
                from_ms = ts_int if from_ms is None else min(from_ms, ts_int)

            if dry_run:
                sender = (msg.get("sender") or {}).get("name", "?")
                text = (msg.get("body") or {}).get("text", "")
                print(f"  [{mid}] {sender}: {str(text)[:80]}")

        if not added_any or from_ms is None:
            break
        if not dry_run:
            print(f"  … продолжаю, собрано {len(ids)} сообщений")
        time.sleep(DELETE_DELAY)

    return ids


def delete_message(client: httpx.Client, api: str, message_id: str) -> bool:
    """Удаляет сообщение; возвращает True при успехе."""
    resp = client.delete(f"{api}/messages", params={"message_id": message_id})
    if resp.status_code == 429:  # превышен лимит запросов
        time.sleep(5.0)
        return delete_message(client, api, message_id)
    if resp.status_code in (401, 403, 404, 500):
        raise SystemExit(f"Ошибка удаления {message_id}: HTTP {resp.status_code} {resp.text[:200]}")
    # API возвращает 200 даже при логической ошибке — смотрим поле success.
    data = resp.json()
    if not data.get("success", resp.is_success):
        raise SystemExit(f"Ошибка удаления {message_id}: {data.get('message', data)!r}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-id", help=f"chat_id канала (по умолчанию из env {CHAT_ID_ENV})")
    parser.add_argument(
        "--api", default=DEFAULT_API, help=f"Base URL API (по умолчанию: {DEFAULT_API})"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Только перечислить сообщения, не удалять"
    )
    parser.add_argument("--confirm", action="store_true", help="Подтверждение реального удаления")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Отключить проверку TLS-сертификата MAX (сертификат Минцифры)",
    )
    args = parser.parse_args()

    token, env_chat_id = _load_secrets()
    chat_id = args.chat_id or env_chat_id
    verify = _verify_flag(token, args.api, args.insecure)

    mode = "dry-run" if args.dry_run else "сбор"
    print(f"Перечисляю сообщения канала {chat_id} ({mode})…")
    with httpx.Client(headers={"Authorization": token}, verify=verify, timeout=30.0) as client:
        ids = fetch_message_ids(client, args.api, chat_id, args.dry_run)

    print(f"\nНайдено сообщений: {len(ids)}")
    if not ids:
        print("Удалять нечего.")
        return

    if args.dry_run:
        print("Это был dry-run. Повторите без --dry-run и с --confirm, чтобы удалить.")
        return

    if not args.confirm:
        print("Отмена: для удаления добавьте --confirm.", file=sys.stderr)
        return

    with httpx.Client(headers={"Authorization": token}, verify=verify, timeout=30.0) as client:
        for i, mid in enumerate(ids, start=1):
            delete_message(client, args.api, mid)
            if i % 100 == 0:
                print(f"  удалено {i}/{len(ids)}")
            time.sleep(DELETE_DELAY)

    print(f"Готово: удалено {len(ids)} сообщений.")


if __name__ == "__main__":
    main()
