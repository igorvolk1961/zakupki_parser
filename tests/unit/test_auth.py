"""Unit-тесты аутентификации: хэширование паролей и токены."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from zakupki_parser.auth import (
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_DEVOPS,
    ROLE_USER,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify_roundtrip() -> None:
    hashed = hash_password("super-secret-123")
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password("super-secret-123", hashed)
    assert not verify_password("wrong-password", hashed)


def test_hash_salt_is_random() -> None:
    assert hash_password("same") != hash_password("same")


def test_verify_handles_bad_format() -> None:
    assert not verify_password("x", "garbage")
    assert not verify_password("x", "")
    assert not verify_password("x", "pbkdf2_sha256$abc$def")


def test_token_roundtrip() -> None:
    token = create_token(42, [ROLE_ADMIN, ROLE_DEVOPS], "secret", 3600)
    payload = decode_token(token, "secret")
    assert payload is not None
    assert payload["sub"] == 42
    assert payload["roles"] == [ROLE_ADMIN, ROLE_DEVOPS]
    assert isinstance(payload["exp"], int)


def test_token_wrong_secret_rejected() -> None:
    token = create_token(1, [ROLE_USER], "key-a", 3600)
    assert decode_token(token, "key-b") is None


def test_token_expired_rejected() -> None:
    token = create_token(1, [ROLE_ADMIN], "secret", 60, now=1_000_000)
    assert decode_token(token, "secret", now=1_100_000) is None


def test_token_tampered_rejected() -> None:
    token = create_token(1, [ROLE_ADMIN], "secret", 3600)
    header, _ = token.split(".", 1)
    assert decode_token(header + ".AAAA", "secret") is None
    assert decode_token("not-a-token", "secret") is None


def test_all_roles() -> None:
    from zakupki_parser.auth import ALL_ROLES

    assert ALL_ROLES == (ROLE_USER, ROLE_ADMIN, ROLE_ANALYST, ROLE_DEVOPS)


def test_has_default_profile_role() -> None:
    """Профиль положен только пользователю с ролью user/analyst (BR-07)."""
    from zakupki_parser.auth import has_default_profile_role

    assert has_default_profile_role(["user"]) is True
    assert has_default_profile_role(["analyst"]) is True
    assert has_default_profile_role(["user", "admin"]) is True
    assert has_default_profile_role(["admin"]) is False
    assert has_default_profile_role(["devops"]) is False
    assert has_default_profile_role(["admin", "devops"]) is False
    assert has_default_profile_role([]) is False


def test_legacy_role_claim_normalized() -> None:
    """Токены с одним claim ``role`` (до ролевой модели) принимаются как ``roles``.

    Старые сессии не «вылетают» при выкатке: payload нормализуется к списку ролей.
    """
    import hashlib
    import hmac
    import json
    import time

    from zakupki_parser.auth import _b64url

    payload = {"sub": 7, "role": "admin", "exp": int(time.time()) + 3600}
    raw = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(b"secret", raw.encode("ascii"), hashlib.sha256).digest()
    legacy = f"{raw}.{_b64url(signature)}"

    decoded = decode_token(legacy, "secret")
    assert decoded is not None
    assert decoded["sub"] == 7
    assert decoded["roles"] == ["admin"]

    # Без ролей вовсе — токен отклоняется.
    payload_bad = {"sub": 7, "exp": int(time.time()) + 3600}
    raw_bad = _b64url(json.dumps(payload_bad, separators=(",", ":")).encode("utf-8"))
    sig_bad = hmac.new(b"secret", raw_bad.encode("ascii"), hashlib.sha256).digest()
    assert decode_token(f"{raw_bad}.{_b64url(sig_bad)}", "secret") is None


def _configs_copy(tmp_path: Path) -> Path:
    src = Path(__file__).resolve().parents[1] / "configs"
    cfgdir = tmp_path / "configs"
    shutil.copytree(src, cfgdir)
    return cfgdir


def test_auth_enabled_without_secret_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ZAKUPKI_AUTH_ENABLED=true без ZAKUPKI_AUTH_SECRET — ошибка конфигурации.

    Защита от пустого HMAC-ключа: env-переопределение применяется ДО валидации,
    поэтому валидатор AuthConfig (enabled без секрета) срабатывает.
    """
    from zakupki_parser.config.loader import load_config

    cfgdir = _configs_copy(tmp_path)
    monkeypatch.setenv("ZAKUPKI_AUTH_ENABLED", "true")
    monkeypatch.delenv("ZAKUPKI_AUTH_SECRET", raising=False)
    monkeypatch.delenv("ZAKUPKI_INTERNAL_TOKEN", raising=False)
    with pytest.raises(ValueError, match="ZAKUPKI_AUTH_SECRET"):
        load_config(str(cfgdir))


def test_auth_enabled_with_secrets_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """enabled=true + секрет + внутренний токен — конфигурация валидна."""
    from zakupki_parser.config.loader import load_config

    cfgdir = _configs_copy(tmp_path)
    monkeypatch.setenv("ZAKUPKI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ZAKUPKI_AUTH_SECRET", "test-secret")
    monkeypatch.setenv("ZAKUPKI_INTERNAL_TOKEN", "internal-123")
    cfg = load_config(str(cfgdir))
    assert cfg.ops.auth.enabled is True
    assert cfg.ops.auth.secret == "test-secret"
    assert cfg.ops.auth.internal_token == "internal-123"


def test_auth_enabled_without_internal_token_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """enabled=true без внутреннего токена — ошибка конфигурации (fail-closed).

    Иначе служебные эндпоинты конвейера остались бы открытыми «с предупреждением» —
    внутренний токен не защищал бы ничего.
    """
    from zakupki_parser.config.loader import load_config

    cfgdir = _configs_copy(tmp_path)
    monkeypatch.setenv("ZAKUPKI_AUTH_ENABLED", "true")
    monkeypatch.setenv("ZAKUPKI_AUTH_SECRET", "test-secret")
    monkeypatch.delenv("ZAKUPKI_INTERNAL_TOKEN", raising=False)
    with pytest.raises(ValueError, match="ZAKUPKI_INTERNAL_TOKEN"):
        load_config(str(cfgdir))


def test_internal_token_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from zakupki_parser.config.loader import load_config

    cfgdir = _configs_copy(tmp_path)
    monkeypatch.setenv("ZAKUPKI_INTERNAL_TOKEN", "internal-123")
    monkeypatch.delenv("ZAKUPKI_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("ZAKUPKI_AUTH_SECRET", raising=False)
    cfg = load_config(str(cfgdir))
    assert cfg.ops.auth.internal_token == "internal-123"
