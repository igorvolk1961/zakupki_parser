"""Контексты профилей для мультипрофильного обхода площадки (BR-07).

``ProfileRunContext`` — один профиль, участвующий в обходе: сам объект ``Profile``
(критерии поиска: коды ОКПД2/НМЦК) и слова клиентской пост-фильтрации (R9,
таблица ``keywords``). Оркестратор собирает из набора таких контекстов поисковые
обходы, объединяя идентичные запросы к площадке (дедупликация).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from zakupki_parser.config.models import SearchCriteria
from zakupki_parser.storage.db import Profile


@dataclass
class ProfileRunContext:
    """Один профиль для обхода: критерии + слова фильтрации."""

    profile: Profile
    keywords: list[str] = field(default_factory=list)
    exclusion_words: list[str] = field(default_factory=list)
    # Целевые регионы профиля: клиентская пост-фильтрация (как ключевые слова R9),
    # в серверный запрос/дедупликацию обходов не входят.
    target_regions: list[str] = field(default_factory=list)
    # Макс. расстояние от центра региона (км): проверяется ТОЛЬКО на этапе анализа.
    # При заданном значении парсер НЕ отсекает закупку по строковому региону.
    max_region_distance_km: float | None = None


@dataclass
class CrawlUnit:
    """Один поисковый обход площадки с набором профилей-потребителей.

    ``criteria`` — серверные критерии запроса (только коды ОКПД2/НМЦК/активность,
    без ключевых слов — они применяются клиентски, R9). ``profiles`` — профили,
    которым нужен этот обход: обход выполняется ОДИН раз, записи раздаются каждому
    профилю веером для клиентской фильтрации и записи оценки.
    """

    criteria: SearchCriteria
    kind: Literal["codes", "no_code", "keywords"]
    profiles: list[ProfileRunContext] = field(default_factory=list)
