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
    # Доступен ли владельцу платный LLM-скоринг (опция scoring) и есть ли у профиля
    # валидные компетенции. False — профиль участвует в МОНИТОРИНГЕ (сбор закупок и
    # matched_keywords), но задания на внешний скоринг по нему не ставятся.
    scoring_allowed: bool = True
    # Искать ключевые слова также в тексте документов закупки, не только в subject
    # (Profile.search_in_documents). Для закупок вне проиндексированного диапазона
    # ОКПД2 включает live-фоллбэк в RecordProcessingMixin (дозагрузка деталей+файлов
    # для записей, не прошедших фильтр по subject, — заметно медленнее).
    search_in_documents: bool = False
    # Синтетический системный «индексный» профиль (Scheduler._build_system_index_ctx,
    # IndexingConfig) — не привязан к пользователю, не хранится в БД. keywords у него
    # всегда пусты (сохраняет ВСЕ закупки заданного диапазона ОКПД2), поэтому блок
    # LLM-скоринга/matched_keywords в RecordProcessingMixin по нему не выполняется;
    # вместо этого сохранённая запись ставится в очередь фоновой индексации документов.
    is_system_index: bool = False


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
