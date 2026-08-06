"""Модели данных имитатора и категории тестовой выборки.

Категории соответствуют ТЗ: тестовая выборка для проверки точности сервиса
скоринга, сбалансированная по 5 типам закупок относительно компетенций поставщика.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Категории тестовой выборки (ground-truth метки для валидации скоринга).
Category = Literal["perfect", "synonym", "close", "far", "false_friend"]

CATEGORIES: tuple[Category, ...] = (
    "perfect",
    "synonym",
    "close",
    "far",
    "false_friend",
)

CATEGORY_LABELS: dict[Category, str] = {
    "perfect": "идеально подходит по семантике и набору терминов",
    "synonym": "подходит по семантике, термины синонимичны и не совпадают",
    "close": "близко к компетенциям, но не полностью покрывается ими",
    "far": "далеко от компетенций поставщика",
    "false_friend": "далеко, но использует ряд терминов компетенций в другом смысле",
}

# Допустимый разброс доли категории (в % от общего числа) при балансировке.
BALANCE_TOLERANCE_PERCENT = 10.0


class FileMeta(BaseModel):
    """Файл закупки: имя + URL скачивания с ЭТП."""

    name: str
    url: str


class Procurement(BaseModel):
    """Закупка в структуре, которую парсит «Парсер закупок»."""

    id: int = Field(description="числовой идентификатор закупки (номер)")
    number: str = Field(description="номер закупки как строка")
    purchase_type: str = "Закупка по потребностям"
    status: str = "Прием предложений"
    subject: str = Field(description="предмет закупки (текст для скоринга)")
    customer: str = Field(description="наименование организации-заказчика")
    customer_id: int = Field(description="id организации на странице /companyProfile/customer")
    inn: str | None = Field(default=None, description="ИНН заказчика")
    nmck: float = Field(description="начальная (максимальная) цена контракта")
    region: str = Field(default="Москва")
    law: str = Field(default="44-ФЗ")
    publication_date: str = Field(description="строка дат «с ДД.ММ.ГГГГ до ДД.ММ.ГГГГ HH:MM (МСК)»")
    okpd2_name: str = Field(default="", description="наименование ОКПД2")
    okpd2_code: str = Field(default="", description="код ОКПД2")
    files: list[FileMeta] = Field(default_factory=list)
    category: Category = Field(description="ground-truth категория скоринга")
    category_reason: str = Field(default="", description="обоснование метки")

    @field_validator("nmck")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v < 0:
            raise ValueError("nmck не может быть отрицательным")
        return v

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str) -> Category:
        if v not in CATEGORIES:
            raise ValueError(f"неизвестная категория: {v!r}")
        return v


class Customer(BaseModel):
    """Организация-заказчик (справочник)."""

    customer_id: int
    name: str
    inn: str | None = None


class Dataset(BaseModel):
    """Полная тестовая выборка + справочник заказчиков."""

    competencies: str = Field(default="", description="текст компетенций поставщика")
    okpd2_sections: list[str] = Field(default_factory=list)
    procurements: list[Procurement] = Field(default_factory=list)
    customers: list[Customer] = Field(default_factory=list)

    def category_counts(self) -> dict[Category, int]:
        """Число закупок по каждой категории."""
        counts: dict[Category, int] = dict.fromkeys(CATEGORIES, 0)
        for p in self.procurements:
            counts[p.category] += 1
        return counts
