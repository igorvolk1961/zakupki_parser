"""Аутентификация пользователей сервиса: пароли и токены.

Пока — вход по логину/паролю с bearer-токеном (позже — OAuth2 через Сбер ID).
Всё на стандартной библиотеке: PBKDF2-HMAC-SHA256 для паролей (OWASP-рекомендации),
HMAC-SHA256 для подписи токенов. Форматы хранения:
- пароль: ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``;
- токен: ``<b64url(json_payload)>.<b64url(signature)>`` (payload: sub, roles, exp).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Literal, cast

ROLE_USER: Literal["user"] = "user"
ROLE_ADMIN: Literal["admin"] = "admin"
ROLE_ANALYST: Literal["analyst"] = "analyst"
ROLE_DEVOPS: Literal["devops"] = "devops"

ALL_ROLES = (ROLE_USER, ROLE_ADMIN, ROLE_ANALYST, ROLE_DEVOPS)
Role = Literal["user", "admin", "analyst", "devops"]

# Число итераций PBKDF2 (OWASP: >= 600 000 для SHA-256).
_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16
_HASH_NAME = "sha256"


def hash_password(password: str) -> str:
    """Хэширует пароль: ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "$".join(("pbkdf2_sha256", str(_PBKDF2_ITERATIONS), salt.hex(), digest.hex()))


def verify_password(password: str, stored: str) -> bool:
    """Проверяет пароль по сохранённому хэшу (сравнение с постоянным временем).

    Устойчиво к нестандартному формату: любые ошибки — как «неверный пароль»,
    без раскрытия причины.
    """
    try:
        _, iterations, salt_hex, hash_hex = stored.split("$", 3)
        iterations_int = int(iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode("utf-8"), salt, iterations_int)
    return hmac.compare_digest(digest, expected)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_token(
    user_id: int,
    roles: list[str],
    secret: str,
    ttl_seconds: int,
    now: float | None = None,
) -> str:
    """Подписанный bearer-токен: ``b64(payload).b64(hmac)``.

    Payload: ``{"sub": user_id, "roles": [..], "exp": <unix_ts>}``.
    """
    payload = {
        "sub": int(user_id),
        "roles": list(roles),
        "exp": int((now if now is not None else time.time()) + ttl_seconds),
    }
    raw = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), raw.encode("ascii"), hashlib.sha256).digest()
    return f"{raw}.{_b64url(signature)}"


def decode_token(token: str, secret: str, now: float | None = None) -> dict[str, Any] | None:
    """Проверяет подпись и срок действия токена; возвращает payload или None."""
    try:
        raw, sig = token.split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), raw.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), expected):
            return None
        payload = cast("dict[str, Any]", json.loads(_unb64url(raw).decode("utf-8")))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return None
    if exp < int(now if now is not None else time.time()):
        return None
    if not isinstance(payload.get("sub"), int):
        return None
    roles = payload.get("roles")
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        # Совместимость с токенами до ролевой модели: один claim ``role``.
        legacy = payload.get("role")
        if not isinstance(legacy, str) or not legacy:
            return None
        payload["roles"] = [legacy]
    return payload
