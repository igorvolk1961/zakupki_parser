"""Структурированный профиль поставщика (строгое разделение слоёв).

Слои:
- системный промпт (``prompts/fit_system.md``) — механика и политика скоринга;
  не содержит фактов о конкретных компаниях и не зависит от профилей пользователей;
- профиль (этот модуль) — ТОЛЬКО факты о поставщике (позиционирование, компетенции,
  исключения) и два числовых параметра политики с дефолтами. Заполняется через
  структурированные данные (форма/UI), а не свободным текстом инструкций;
- рендерер (``render_profile``) — единственное место, где факты превращаются
  в текст для LLM. «Промпт-инжиниринг» делает код, а не тендеролог: пользователь
  отвечает на бытовые вопросы формы, сервис собирает канонический блок
  «КОМПЕТЕНЦИИ ПОСТАВЩИКА».
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class Competency(BaseModel):
    """Одна компетенция поставщика: факты, без инструкций модели."""

    area: str = Field(description="Направление/область")
    description: str = Field(default="", description="Что компания делает в этой области")
    examples: list[str] = Field(default_factory=list, description="Примеры работ/кейсов")


class ScoringPolicy(BaseModel):
    """Числовые параметры политики скоринга.

    Дефолты совпадают с значениями, зашитыми в системный промпт, поэтому в текст
    для LLM выводятся только не-дефолтные значения (см. ``render_profile``).
    """

    uncovered_penalty: float = Field(
        default=1.5, description="Баллы за неперечисленный, но близкий к компетенциям кейс"
    )
    ambiguous_range: tuple[float, float] = Field(
        default=(4.0, 6.0), description="Диапазон оценок при неоднозначности предмета"
    )


class Profile(BaseModel):
    """Структурированный профиль поставщика: факты + параметры политики."""

    name: str = Field(default="", description="Название поставщика")
    positioning: str = Field(default="", description="Позиционирование одним-двумя предложениями")
    breadth: Literal["broad", "narrow"] = Field(
        default="broad",
        description=(
            "Широта охвата: broad — компания берётся за задачи всей своей области "
            "(перечень кейсов не исчерпывающий), narrow — работает строго по перечню"
        ),
    )
    competencies: list[Competency] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list, description="Чего компания НЕ делает")
    scoring_policy: ScoringPolicy = Field(default_factory=ScoringPolicy)


_BREADTH_TEXT: dict[str, str] = {
    "broad": "широкий: компания берётся за задачи всей своей области, "
    "перечень кейсов не исчерпывающий",
    "narrow": "узкий: компания работает строго по перечисленному перечню",
}

_DEFAULT_POLICY = ScoringPolicy()


def _policy_lines(policy: ScoringPolicy) -> list[str]:
    """Не-дефолтные параметры политики (дефолты уже зашиты в системный промпт)."""
    lines: list[str] = []
    if policy.uncovered_penalty != _DEFAULT_POLICY.uncovered_penalty:
        line = f"- штраф за неперечисленный, но близкий кейс: {policy.uncovered_penalty:g} балла"
        lines.append(line)
    if policy.ambiguous_range != _DEFAULT_POLICY.ambiguous_range:
        lo, hi = policy.ambiguous_range
        lines.append(f"- диапазон оценок при неоднозначности: {lo:g}-{hi:g}")
    return lines


def _render(
    profile: Profile,
    *,
    with_breadth: bool,
    with_exclusions: bool,
    with_policy: bool,
) -> str:
    lines: list[str] = []
    if profile.name:
        lines.append(f"Поставщик: {profile.name}")
    if profile.positioning:
        lines.append(f"Позиционирование: {profile.positioning.strip()}")
    if with_breadth:
        lines.append(f"Охват: {_BREADTH_TEXT.get(profile.breadth, _BREADTH_TEXT['broad'])}")
    if profile.competencies:
        lines.append("")
        lines.append("Основные компетенции:")
        for comp in profile.competencies:
            parts = [comp.area] if comp.area else []
            if comp.description:
                parts.append(comp.description.strip())
            line = "- " + ": ".join(parts)
            if comp.examples:
                line += " Примеры: " + ", ".join(comp.examples)
            lines.append(line)
    if with_exclusions and profile.exclusions:
        lines.append("")
        lines.append("НЕ входят в компетенции (исключения):")
        lines.extend(f"- {exc}" for exc in profile.exclusions)
    if with_policy:
        policy = _policy_lines(profile.scoring_policy)
        if policy:
            lines.append("")
            lines.append("Параметры скоринга:")
            lines.extend(policy)
    return "\n".join(lines).strip()


def render_profile(profile: Profile) -> str:
    """Канонический текст профиля для LLM (все факты + не-дефолтная политика).

    Это единственное место, где структурированный профиль превращается в текст
    для fit/judge-цепочек; правила поведения модели остаются в системном промпте.
    """
    return _render(profile, with_breadth=True, with_exclusions=True, with_policy=True)


def render_profile_embedding(profile: Profile) -> str:
    """Компактный текст профиля для векторной близости (только позитивные факты).

    Раздел «НЕ входят в компетенции» и параметры политики НЕ включаются: исключения
    описывают, чего компания НЕ делает, и их присутствие в эмбеддинге смещает вектор
    профиля к нерелевантным темам — даёт ложную близость по терминам исключений и
    ложную удалённость релевантных закупок (риск отсечь их pre-filter по вектору).
    Для сравнения с описанием закупки берутся только позитивные факты: позиционирование
    и компетенции.
    """
    return _render(profile, with_breadth=False, with_exclusions=False, with_policy=False)


@dataclass(frozen=True)
class ProfileTexts:
    """Рендер профиля для двух разных потребителей.

    ``llm`` — полный блок «КОМПЕТЕНЦИИ ПОСТАВЩИКА» для fit/judge-цепочек (включая
    исключения и не-дефолтные параметры политики);
    ``embedding`` — текст ТОЛЬКО для ветки векторной близости (без исключений
    и политики), чтобы «чего компания НЕ делает» не создавало семантический шум.
    """

    llm: str
    embedding: str


def _from_mapping(data: Any) -> Profile:
    """Профиль из YAML/JSON-отображения."""
    if isinstance(data, dict):
        return Profile.model_validate(data)
    raise ValueError("профиль должен быть YAML/JSON-отображением")


def load_profile(path: Path) -> Profile:
    """Загрузить структурированный профиль из файла ``.yaml``/``.yml``/``.json``."""
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return Profile.model_validate(json.loads(text))
    if suffix in {".yaml", ".yml"}:
        return _from_mapping(yaml.safe_load(text))
    raise ValueError(f"Профиль поставщика — файл YAML или JSON, а не {path.name}")


def profile_to_texts(value: Any) -> ProfileTexts | None:
    """Нормализовать компетенции профиля в пару текстов (llm/embedding).

    Принимает ТОЛЬКО структурированное значение: ``Profile``, dict или JSON-строку
    (каноническая схема ``Profile`` — BR-07). Для свободного текста
    возвращается ``None``.
    """
    if isinstance(value, Profile):
        return ProfileTexts(
            llm=render_profile(value),
            embedding=render_profile_embedding(value),
        )
    if isinstance(value, dict):
        return profile_to_texts(Profile.model_validate(value))
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return profile_to_texts(Profile.model_validate(parsed))
        return None
    return None


def profile_to_text(value: Any) -> str:
    """Канонический LLM-текст профиля (полный блок «КОМПЕТЕНЦИИ ПОСТАВЩИКА»).

    Для ветки векторной близости используй ``ProfileTexts.embedding``
    (``profile_to_texts``), чтобы исключения не создавали семантический шум.
    """
    texts = profile_to_texts(value)
    return texts.llm if texts else ""
