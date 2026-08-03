"""Вежливые рандомные задержки между действиями парсера."""

from __future__ import annotations

import asyncio
import random

from zakupki_parser.config.models import BrowserConfig


class Delayer:
    """Реализует «человеческую» рандомную задержку между действиями.

    Диапазон берётся из ``config_parser.yaml -> browser.delay_between_actions_seconds``.
    По умолчанию 4–12 секунд (консервативный режим против блокировки IP).
    """

    def __init__(self, browser_cfg: BrowserConfig) -> None:
        lo, hi = browser_cfg.delay_between_actions_seconds
        self._lo = float(lo)
        self._hi = max(float(hi), self._lo)

    async def sleep(self, multiplier: float = 1.0) -> None:
        """Спит случайное время из диапазона, умноженное на ``multiplier``."""
        base = random.uniform(self._lo, self._hi)
        await asyncio.sleep(base * multiplier)
