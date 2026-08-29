"""Тесты центральной тарификации (scoring_common.costing) и хелперов инъекции."""

from __future__ import annotations

from datetime import UTC, datetime

from scoring_common.costing import (
    deepseek_peak_rates,
    embedding_cost_rub,
    embedding_cost_usd,
    embedding_input_tokens,
    is_deepseek_peak,
    llm_cost_details,
    llm_cost_usd,
    normalize_model,
)


def _utc(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _weekday(hour: int, weekday: int = 0) -> datetime:
    # 2026-01-05 — понедельник (weekday=0).
    base = _utc(2026, 1, 5, hour)
    return base.replace(day=5 + weekday)


def test_normalize_model_aliases() -> None:
    assert normalize_model("deepseek-chat") == "deepseek-v4-flash"
    assert normalize_model("deepseek-reasoner") == "deepseek-v4-pro"
    assert normalize_model("DeepSeek-V4-Flash") == "deepseek-v4-flash"
    assert normalize_model("unknown") == "unknown"


def test_is_deepseek_peak() -> None:
    # Пик: 01:00–04:00 и 06:00–10:00 UTC, пн–пт.
    assert is_deepseek_peak(_weekday(2)) is True
    assert is_deepseek_peak(_weekday(7)) is True
    assert is_deepseek_peak(_weekday(0)) is False
    assert is_deepseek_peak(_weekday(12)) is False
    # 4:00 — уже не пик; 6:00 — пик.
    assert is_deepseek_peak(_weekday(4)) is False
    assert is_deepseek_peak(_weekday(6)) is True
    # Суббота (weekday=5) — не пик в любом часу.
    assert is_deepseek_peak(_weekday(2, weekday=5)) is False


def test_llm_cost_usd_offpeak_and_peak() -> None:
    off = _weekday(0)  # 0:00 — непик
    peak = _weekday(2)  # 2:00 — пик
    # 100k miss + 50k out: peak = 0.44*0.1 + 1.32*0.05 = 0.044+0.066 = 0.11; off = 0.055.
    assert llm_cost_usd("deepseek-v4-flash", 100_000, 50_000, peak) == 0.11
    assert llm_cost_usd("deepseek-v4-flash", 100_000, 50_000, off) == 0.055
    # cache-hit дешевле: 100k hit (0.014 peak) + 50k out.
    assert round(
        llm_cost_usd("deepseek-v4-flash", 0, 50_000, peak, input_cache_hit=100_000), 8
    ) == round(0.014 * 100_000 / 1_000_000 + 1.32 * 50_000 / 1_000_000, 8)
    # Про-модель дороже.
    assert llm_cost_usd("deepseek-v4-pro", 1_000_000, 0, off) == 0.66


def test_llm_cost_details_keys() -> None:
    off = _weekday(0)
    details = llm_cost_details("deepseek-v4-flash", 100_000, 50_000, off, input_cache_hit=10_000)
    assert set(details) == {"input", "input_cached_tokens", "output"}
    # input (мисс) отдельно от input_cached_tokens (хит); сумма = полная стоимость.
    assert round(
        details["input"] + details["input_cached_tokens"] + details["output"], 8
    ) == llm_cost_usd("deepseek-v4-flash", 100_000, 50_000, off, input_cache_hit=10_000)


def test_unknown_model_zero() -> None:
    assert llm_cost_usd("gpt-4o", 1000, 1000, _weekday(2)) == 0.0
    assert llm_cost_details("gpt-4o", 1000, 1000, _weekday(2)) == {}


def test_embedding_cost() -> None:
    assert embedding_cost_rub(1_000_000) == 14.0
    assert round(embedding_cost_usd(1_000_000, rub_to_usd=100.0), 8) == 0.14
    assert embedding_cost_usd(1_000_000, rub_to_usd=0.0) == 0.0


def test_embedding_input_tokens_usage_or_estimate() -> None:
    # usage из ответа приоритетнее оценки.
    assert embedding_input_tokens({"usage": {"prompt_tokens": 123}}, ["текст"]) == 123
    assert embedding_input_tokens({}, ["0123456789"]) == 3  # 10 симв. / 3 → 3
    assert embedding_input_tokens({}, ["0123456789", "ab"]) == 4  # 3 + 1


def test_deepseek_peak_rates() -> None:
    rates = deepseek_peak_rates("deepseek-v4-flash")
    assert rates is not None
    hit, miss, out = rates
    assert miss == 0.44 and out == 1.32 and hit == 0.014
    assert deepseek_peak_rates("unknown") is None
