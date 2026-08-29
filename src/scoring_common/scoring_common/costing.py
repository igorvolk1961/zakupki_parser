"""Центральная тарификация LLM и эмбеддингов (app-side).

Единый источник цен для DeepSeek (``deepseek-v4-flash``/``-pro``) и GigaChat
(``EmbeddingsGigaR``). Позволяет считать себестоимость точно — с учётом
кэш-хита/мисса по входу и пиковых/непиковых тарифов DeepSeek — и передавать её в
Langfuse через ``usage_details``/``cost_details``, не завися от каталога моделей
Langfuse (который не знает DeepSeek и не умеет пик/непик по времени суток).

Цены (официальные, per 1M токенов):

DeepSeek (USD), пик/непик (непик = 50% от пика), пиковые часы 01:00–04:00 и
06:00–10:00 UTC, пн–пт:
  - ``deepseek-v4-flash``: вход cache-hit 0.007/0.014, cache-miss 0.22/0.44,
    выход 0.66/1.32;
  - ``deepseek-v4-pro``: вход cache-hit 0.022/0.044, cache-miss 0.66/1.32,
    выход 1.98/3.96;
  - ``deepseek-v4-flash-vision-exp`` — как flash.

GigaChat ``EmbeddingsGigaR`` (RUB): 14 ₽/1M токенов.

Тарифы переопределяются переменными окружения ``COSTING_*`` (см. ``_env_float``),
курс рубль→доллар — ``COSTING_RUB_TO_USD`` (по умолчанию 90.0).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = [
    "llm_cost_usd",
    "llm_cost_details",
    "embedding_cost_rub",
    "embedding_cost_usd",
    "embedding_input_tokens",
    "is_deepseek_peak",
    "normalize_model",
    "deepseek_peak_rates",
    "GIGA_EMBEDDING_RUB_PER_1M",
]


@dataclass(frozen=True)
class _DeepSeekRates:
    """Пиковые цены DeepSeek (USD / 1M токенов); непик = peak * OFFPEAK_FACTOR."""

    input_cache_hit_peak: float
    input_cache_miss_peak: float
    output_peak: float


OFFPEAK_FACTOR = 0.5
PIC_1M = 1_000_000.0


def _env_float(name: str, default: float) -> float:
    """Прочитать float из окружения; при отсутствии/ошибке — ``default``."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Пиковые часы DeepSeek (минуты от полуночи UTC) и дни недели (пн=0 … пт=4).
_DEEPSEEK_PEAK_RANGES = ((60, 240), (360, 600))  # 01:00–04:00 и 06:00–10:00 UTC
_DEEPSEEK_PEAK_DAYS = frozenset({0, 1, 2, 3, 4})

_DEEPSEEK: dict[str, _DeepSeekRates] = {
    "deepseek-v4-flash": _DeepSeekRates(
        _env_float("COSTING_DEEPSEEK_FLASH_CACHE_HIT_PEAK", 0.014),
        _env_float("COSTING_DEEPSEEK_FLASH_CACHE_MISS_PEAK", 0.44),
        _env_float("COSTING_DEEPSEEK_FLASH_OUTPUT_PEAK", 1.32),
    ),
    "deepseek-v4-flash-vision-exp": _DeepSeekRates(
        _env_float("COSTING_DEEPSEEK_FLASH_CACHE_HIT_PEAK", 0.014),
        _env_float("COSTING_DEEPSEEK_FLASH_CACHE_MISS_PEAK", 0.44),
        _env_float("COSTING_DEEPSEEK_FLASH_OUTPUT_PEAK", 1.32),
    ),
    "deepseek-v4-pro": _DeepSeekRates(
        _env_float("COSTING_DEEPSEEK_PRO_CACHE_HIT_PEAK", 0.044),
        _env_float("COSTING_DEEPSEEK_PRO_CACHE_MISS_PEAK", 1.32),
        _env_float("COSTING_DEEPSEEK_PRO_OUTPUT_PEAK", 3.96),
    ),
}

# Алиасы (исторически использовавшиеся имена) → каноническая модель.
_MODEL_ALIASES: dict[str, str] = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
}

# GigaChat эмбеддинги: RUB / 1M токенов.
GIGA_EMBEDDING_RUB_PER_1M = _env_float("COSTING_GIGA_EMBEDDING_RUB_PER_1M", 14.0)
DEFAULT_RUB_TO_USD = _env_float("COSTING_RUB_TO_USD", 90.0)


def normalize_model(model: str) -> str:
    """Нормализовать имя модели (алиас или полное) до ключа тарифной таблицы."""
    name = (model or "").strip().lower()
    return _MODEL_ALIASES.get(name, name)


def is_deepseek_peak(now: datetime) -> bool:
    """True, если ``now`` (UTC) попадает в пиковое окно DeepSeek (пн–пт)."""
    if now.weekday() not in _DEEPSEEK_PEAK_DAYS:
        return False
    minutes = now.hour * 60 + now.minute
    return any(start <= minutes < end for start, end in _DEEPSEEK_PEAK_RANGES)


def _rates(model: str) -> _DeepSeekRates | None:
    return _DEEPSEEK.get(normalize_model(model))


def deepseek_peak_rates(model: str) -> tuple[float, float, float] | None:
    """Пиковые цены DeepSeek (USD/1M) для модели: (cache_hit, cache_miss, output).

    None для неизвестной модели. Используется там, где нужны исходные цены
    (например, провижн моделей Langfuse), чтобы env-переопределения ``COSTING_*``
    применялись единообразно.
    """
    rates = _rates(model)
    if rates is None:
        return None
    return (rates.input_cache_hit_peak, rates.input_cache_miss_peak, rates.output_peak)


def llm_cost_usd(
    model: str,
    input_cache_miss: int,
    output: int,
    now: datetime,
    *,
    input_cache_hit: int = 0,
) -> float:
    """Стоимость вызова DeepSeek в USD (учитывает пик/непик и кэш-хит/мисс).

    ``input_cache_miss`` — токены входа без кэша; ``input_cache_hit`` — попавшие в
    кэш; ``output`` — токены выхода. Неизвестный провайдер/модель → 0.0.
    """
    rates = _rates(model)
    if rates is None:
        return 0.0
    factor = 1.0 if is_deepseek_peak(now) else OFFPEAK_FACTOR
    cost = (
        (
            input_cache_hit * rates.input_cache_hit_peak
            + input_cache_miss * rates.input_cache_miss_peak
            + output * rates.output_peak
        )
        / PIC_1M
        * factor
    )
    return round(cost, 8)


def llm_cost_details(
    model: str,
    input_cache_miss: int,
    output: int,
    now: datetime,
    *,
    input_cache_hit: int = 0,
) -> dict[str, float]:
    """Разбивка стоимости DeepSeek по типам usage (для Langfuse ``cost_details``).

    Типы ключей совпадают с ``usage_details``: ``input`` (мисс), ``output``,
    ``input_cached_tokens`` (хит). Неизвестная модель → пустой словарь.
    """
    rates = _rates(model)
    if rates is None:
        return {}
    factor = 1.0 if is_deepseek_peak(now) else OFFPEAK_FACTOR
    return {
        "input": round(input_cache_miss * rates.input_cache_miss_peak / PIC_1M * factor, 8),
        "input_cached_tokens": round(
            input_cache_hit * rates.input_cache_hit_peak / PIC_1M * factor, 8
        ),
        "output": round(output * rates.output_peak / PIC_1M * factor, 8),
    }


def embedding_cost_rub(token_count: int, *, rub_per_1m: float | None = None) -> float:
    """Стоимость эмбеддингов GigaChat в рублях."""
    rate = rub_per_1m if rub_per_1m is not None else GIGA_EMBEDDING_RUB_PER_1M
    return round(token_count / PIC_1M * rate, 6)


def embedding_cost_usd(
    token_count: int,
    *,
    rub_to_usd: float | None = None,
    rub_per_1m: float | None = None,
) -> float:
    """Стоимость эмбеддингов GigaChat в USD (для Langfuse ``cost_details``)."""
    rate = rub_to_usd if rub_to_usd is not None else DEFAULT_RUB_TO_USD
    if rate <= 0:
        return 0.0
    return round(embedding_cost_rub(token_count, rub_per_1m=rub_per_1m) / rate, 8)


def embedding_input_tokens(data: dict[str, Any], texts: Sequence[str]) -> int:
    """Число токенов входа для эмбеддингов (usage из ответа или оценка по символам).

    Единый хелпер для прямого Giga-клиента и OpenAI-совместимого прокси: приоритет
    у ``usage.prompt_tokens`` из ответа; при отсутствии — оценка ``len/3`` по каждому
    тексту. Возвращает 0, если нечего оценить.
    """
    usage = data.get("usage") or {}
    pt = usage.get("prompt_tokens")
    if isinstance(pt, (int, float)) and pt > 0:
        return int(pt)
    return sum(max(1, round(len(t) / 3)) for t in texts if isinstance(t, str)) or 0
