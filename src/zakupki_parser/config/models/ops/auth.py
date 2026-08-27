"""Модель параметров аутентификации API (config_ops.yaml -> auth)."""

from __future__ import annotations

from pydantic import Field, model_validator

from zakupki_parser.config.models.ops.base import _BaseConfig


class AuthConfig(_BaseConfig):
    """Параметры аутентификации API (config_ops.yaml -> auth).

    Авторизация всегда включена (переключателя ``enabled`` нет — dev-режим закрыт).
    Секрет подписи токенов и внутренний токен конвейера не хранятся в YAML —
    подкладываются из env (``ZAKUPKI_AUTH_SECRET``, ``ZAKUPKI_INTERNAL_TOKEN``)
    в ``config/loader.py`` (как токены уведомлений). Без секрета или внутреннего
    токена конфигурация невалидна (fail fast): иначе служебные эндпоинты конвейера
    остались бы открытыми с «предупреждением» — токен не защищал бы ничего.
    """

    secret: str | None = Field(
        default=None,
        description="секрет подписи bearer-токенов; из env ZAKUPKI_AUTH_SECRET, в YAML не пишется",
    )
    internal_token: str | None = Field(
        default=None,
        description=(
            "внутренний токен служебных эндпоинтов конвейера (POST /score, "
            "POST /customers/{id}/rating); проверяется по заголовку X-Internal-Token; "
            "из env ZAKUPKI_INTERNAL_TOKEN, в YAML не пишется"
        ),
    )
    token_ttl_seconds: int = Field(default=12 * 3600, ge=60, description="время жизни токена (сек)")

    @model_validator(mode="after")
    def _require_secrets(self) -> AuthConfig:
        if not self.secret:
            raise ValueError("не задан секрет подписи токенов: установите env ZAKUPKI_AUTH_SECRET")
        if not self.internal_token:
            raise ValueError(
                "не задан внутренний токен конвейера: установите env "
                "ZAKUPKI_INTERNAL_TOKEN и одноимённые значения в .env сервисов "
                "конвейера (TRANSPORT_PARSER_INTERNAL_TOKEN, "
                "SCORE_PARSER_INTERNAL_TOKEN, ANALYSIS_PARSER_INTERNAL_TOKEN)"
            )
        return self
