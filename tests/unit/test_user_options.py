"""Каталог опций аккаунта и триал-режим (правила #4–#9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zakupki_parser.options import (
    ALL_OPTIONS,
    FREE_KEYS,
    PAID_KEYS,
    enabled_options,
    enabled_paid_options,
    option_by_key,
    option_requires_competencies,
    paid_default_options,
)
from zakupki_parser.storage.db import UserAccount
from zakupki_parser.storage.repository.accounts import effective_options

NOW = datetime(2026, 1, 15, tzinfo=UTC)


def _account(options: dict[str, bool] | None, *, is_active: bool = True) -> UserAccount:
    return UserAccount(id=1, user_id=1, name="А", options=options or {}, is_active=is_active)


def test_catalog_has_free_and_paid() -> None:
    keys = {o.key for o in ALL_OPTIONS}
    assert set(FREE_KEYS) | set(PAID_KEYS) == keys
    # geo_premium объявлена, но отложена.
    geo = option_by_key("geo_premium")
    assert geo is not None
    assert geo.available is False


def test_default_account_is_free_only() -> None:
    assert paid_default_options(enabled=False) == dict.fromkeys(PAID_KEYS, False)
    assert paid_default_options(enabled=True) == dict.fromkeys(PAID_KEYS, True)


def test_effective_options_trial_does_not_override_account() -> None:
    """Триал больше НЕ переопределяет account.options во время действия (было —
    в триале все платные опции считались включёнными НЕЗАВИСИМО от аккаунта,
    из-за чего пользователь физически не мог выключить отдельную опцию, пока
    триал шёл). Теперь триал материализуется один раз в момент создания
    пользователя (account_paid_default=True в create_user_with_setup для
    саморегистрации) — во время действия читается РЕАЛЬНОЕ состояние аккаунта,
    ``in_trial``/``trial_end_at`` остаются информационными полями."""
    trial_end = NOW + timedelta(days=14)
    # Аккаунт с ВСЕМИ выключенными опциями (пользователь сам их выключил во
    # время триала) — триал это решение больше не переопределяет.
    eff = effective_options([_account(paid_default_options(False))], trial_end, now=NOW)
    assert eff.in_trial
    assert not eff.has_option("scoring")
    assert not eff.has_option("analysis")
    assert not eff.has_option("margin")
    assert eff.has_option("search")  # бесплатные доступны всегда

    # Материализованный триал (account_paid_default=True при регистрации) —
    # всё включено, потому что включено В АККАУНТЕ, а не потому что идёт триал.
    eff_full = effective_options([_account(paid_default_options(True))], trial_end, now=NOW)
    assert eff_full.in_trial
    assert eff_full.has_option("scoring")
    assert eff_full.has_option("analysis")
    assert eff_full.has_option("margin")


def test_effective_options_account_paid_off() -> None:
    eff = effective_options([_account(paid_default_options(False))], None, now=NOW)
    assert not eff.in_trial
    assert not eff.has_option("scoring")
    assert eff.has_option("search")  # бесплатные доступны всегда
    # Легаси-пользователь без аккаунтов — как «полный» доступ.
    assert effective_options([], None, now=NOW).has_option("scoring")


def test_effective_options_account_partial() -> None:
    options = paid_default_options(False)
    options["pwin"] = True
    eff = effective_options([_account(options)], None, now=NOW)
    assert eff.has_option("pwin")
    assert not eff.has_option("margin")
    assert not eff.has_option("scoring")


def test_geo_premium_never_enabled() -> None:
    options = paid_default_options(True)
    assert enabled_paid_options(options) == set(PAID_KEYS) - {"geo_premium"}
    # Даже в триале отложенная опция не «доступна».
    eff = effective_options([_account(options)], NOW + timedelta(days=1), now=NOW)
    assert not eff.has_option("geo_premium")


def test_account_provides_competency_scoring() -> None:
    free = _account(paid_default_options(False))
    full = _account(paid_default_options(True))
    assert effective_options([free], None, now=NOW).account_provides_competency_scoring() is False
    assert effective_options([full], None, now=NOW).account_provides_competency_scoring() is True
    # Легаси без аккаунтов — по-прежнему требует компетенций (старое поведение).
    assert effective_options([], None, now=NOW).account_provides_competency_scoring() is True
    # Триал не меняет требование компетенций по аккаунту.
    trial = NOW + timedelta(days=5)
    assert effective_options([free], trial, now=NOW).account_provides_competency_scoring() is False


def test_requires_competencies_flag_on_scoring() -> None:
    assert option_requires_competencies("scoring") is True
    assert option_requires_competencies("analysis") is False
    assert option_requires_competencies("geo_premium") is False


def test_enabled_options_free_always_present() -> None:
    assert set(FREE_KEYS) <= enabled_options({}, in_trial=False)
    assert set(FREE_KEYS) <= enabled_options({}, in_trial=True)
