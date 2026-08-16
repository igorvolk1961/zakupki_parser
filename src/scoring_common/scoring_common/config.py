"""Модели коэффициентов каскада скоринга и YAML-источник настроек.

``PwinCoefficients`` — калиброванные коэффициенты модели вероятности победы
(из ``docs/references/Модель P(win) для IT-закупок России...pdf``).
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo
from pydantic_settings import PydanticBaseSettingsSource


class PwinCoefficients(BaseModel):
    """Коэффициенты модели P(win) = base_pwin × k_smp × k_license × k_large × k_procedure × k_ai.

    На первом этапе применяются только коэффициенты, которые можно вычислить из
    уже доступных полей карточки (НМЦК, subject, okpd2). Коэффициенты СМП/лицензий/
    процедуры по умолчанию = 1.0 (поля пока не извлекаются парсером).
    """

    base_pwin: float = Field(
        default=0.4,
        ge=0,
        le=1,
        description="базовая вероятность победы (1/медианное число участников)",
    )

    k_smp: float = Field(default=1.5, ge=0, description="закупка только для СМП")
    k_license_present: float = Field(
        default=3.0, ge=0, description="лицензия ФСТЭК/ФСБ есть у компании"
    )
    k_license_absent: float = Field(default=0.1, ge=0, description="лицензии ФСТЭК/ФСБ нет")

    k_large_threshold: float = Field(
        default=50_000_000.0, ge=0, description="порог НМЦК «крупной» закупки, ₽"
    )
    k_large: float = Field(
        default=0.6, ge=0, description="коэффициент крупной закупки (НМЦК > порога)"
    )

    k_procedure_auction: float = Field(default=1.3, ge=0, description="электронный аукцион")
    k_procedure_contest: float = Field(default=1.0, ge=0, description="открытый конкурс")
    k_procedure_quotation: float = Field(default=0.8, ge=0, description="запрос котировок")

    k_ai: float = Field(default=1.8, ge=0, description="закупка ИИ-решений")

    # Маркеры ИИ-закупки в subject/okpd2 (регистронезависимый поиск подстроки).
    ai_markers: tuple[str, ...] = (
        "искусственный интеллект",
        "нейросет",
        "машинное обучение",
        "llm",
        "большая языковая модель",
        "ии-",
        " ai ",
        "ии ",
        "ии,",
        "гпт",
    )

    max_pwin_cap: float = Field(
        default=0.95, ge=0, le=1, description="кап P(win) — защита от переоценки"
    )


class YamlConfigSource(PydanticBaseSettingsSource):
    """Источник настроек из YAML-файла (ниже по приоритету, чем env/.env)."""

    def __init__(self, settings_cls: type, path: Any) -> None:
        super().__init__(settings_cls)
        self._path = path

    def _load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {}
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        data = self._load()
        return data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return self._load()
