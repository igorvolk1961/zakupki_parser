"""Каноническая нормализация/валидация компетенций профиля (BR-07).

Компетенции (``profiles.competencies``) хранятся всегда как КАНОНИЧЕСКИЙ JSON
одной схемы — ``scoring_service.profile.Profile`` (``positioning``, ``breadth``,
``competencies[]`` (``area``/``description``/``examples``), ``exclusions``,
``scoring_policy``). Свободный текст/legacy-markdown не допускаются: перед
сохранением выполняется обязательная валидация, что строка — правильный JSON
нужной схемы. Один и тот же результат формируется независимо от источника
(веб-форма, импорт JSON-файла, JSON-текст, seed).

Единая точка входа: ``parse_competencies`` (разбор/валидация) и
``normalize_competencies`` (каноническая строка для записи в БД). Хэш
канонического JSON — ключ дедупликации скоринга: профили с идентичным
содержанием компетенций (например, igor и аналитик) дают одинаковый хэш.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class Competency(BaseModel):
    """Одна компетенция поставщика: факты, без инструкций модели.

    Схема строго повторяет ``scoring_service.profile.Competency`` (BR-07),
    чтобы канонический JSON, сохраняемый в ``profiles.competencies``, понимался
    воркером скоринга без преобразований.
    """

    area: str = Field(description="Направление/область")
    description: str = Field(default="", description="Что компания делает в этой области")
    examples: list[str] = Field(default_factory=list, description="Примеры работ/кейсов")


class ScoringPolicy(BaseModel):
    """Числовые параметры политики скоринга (дефолты — как в системном промпте)."""

    uncovered_penalty: float = Field(
        default=1.5, description="Баллы за неперечисленный, но близкий к компетенциям кейс"
    )
    ambiguous_range: tuple[float, float] = Field(
        default=(4.0, 6.0), description="Диапазон оценок при неоднозначности предмета"
    )


class Profile(BaseModel):
    """Структурированный профиль поставщика: факты + параметры политики.

    Схема повторяет ``scoring_service.profile.Profile``: ``positioning``,
    ``breadth``, ``competencies[]``, ``exclusions``, ``scoring_policy``.
    """

    name: str = Field(default="", description="Название поставщика")
    positioning: str = Field(default="", description="Позиционирование одним-двумя предложениями")
    breadth: Literal["broad", "narrow"] = Field(
        default="broad",
        description="Широта охвата: broad/narrow",
    )
    competencies: list[Competency] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list, description="Чего компания НЕ делает")
    scoring_policy: ScoringPolicy = Field(default_factory=ScoringPolicy)


class CompetenciesError(ValueError):
    """Некорректные компетенции: не JSON, не схема ``Profile`` или пустой профиль."""


def parse_competencies(raw: str | None) -> Profile:
    """Разбирает строку компетенций в валидированную модель ``Profile``.

    Пустая строка/``None`` -> пустой профиль (дефолты). Не-JSON или JSON не
    подходящей схемы -> ``CompetenciesError``.
    """
    if not raw or not raw.strip():
        return Profile()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CompetenciesError(
            "Компетенции должны быть JSON: не удалось разобрать строку"
        ) from exc
    if not isinstance(data, dict):
        raise CompetenciesError("Компетенции должны быть JSON-объектом схемы Profile")
    try:
        return Profile.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise CompetenciesError(f"Компетенции должны соответствовать схеме Profile: {exc}") from exc


def normalize_competencies(raw: str | None) -> str:
    """Строка компетенций -> канонический JSON схемы ``Profile`` (для записи в БД).

    Единый результат для любого источника: пустой — JSON пустого профиля, иначе —
    компактный JSON провалидированного ``Profile`` в каноническом порядке полей.
    """
    profile = parse_competencies(raw)
    return json.dumps(profile.model_dump(), ensure_ascii=False, separators=(",", ":"))


def competencies_hash(raw: str | None) -> str:
    """Стабильный SHA-256 хэш канонического JSON компетенций.

    Профили с идентичным содержанием компетенций (независимо от исходной формы
    JSON) дают одинаковый хэш — используется как ключ дедупликации скоринга.
    """
    return hashlib.sha256(normalize_competencies(raw).encode("utf-8")).hexdigest()


def is_empty(profile: Profile) -> bool:
    """Пустой ли профиль: нет ни компетенций, ни позиционирования, ни исключений."""
    return not (profile.name or profile.positioning or profile.competencies or profile.exclusions)


def ensure_competencies(data: dict[str, Any]) -> dict[str, Any]:
    """Предобработка seed-словаря: нормализует ``competencies`` перед ``upsert_profile``.

    Возвращает копию ``data`` полем ``competencies``, приведённым к каноническому
    JSON. Отсутствующее/пустое значение трактуется как пустой профиль (JSON).
    """
    out = dict(data)
    out["competencies"] = normalize_competencies(out.get("competencies"))
    return out
