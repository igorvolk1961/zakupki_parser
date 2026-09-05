"""Каталог опций (функций) аккаунта пользователя и триал-режим.

Опции описывают операции, которые система выполняет для пользователя и которые
могут стоить владельцу системы (вызовы LLM/эмбеддингов/внешних сервисов) либо
бесплатны. Бесплатные опции доступны всегда; платные пользователь включает в
аккаунте (личный кабинет) либо получает автоматически на время триал-режима
(все опции поиска и скоринга, пункт «доступны бесплатно»).

Аккаунт пользователя — именованный набор переключателей платных опций
(``user_accounts.options``: ключ -> bool, False по умолчанию). В каждый момент
активен один аккаунт; активный аккаунт определяет доступность платных опций
после окончания триал-режима. Каталог зашит в код (ключ/название/описание);
в БД хранятся только переключатели.

``geo_premium`` (платное геопозиционирование) объявлена в каталоге, но
``available=False``: доступ к платному гео-сервису пока не подключён — в UI она
показывается как недоступная и не может быть включена.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Длительность триал-режима по умолчанию (сутки) для self-registered
# пользователей: 14 дней, а не 10 лет (решение владельца системы).
TRIAL_DEFAULT_DAYS = 14

GROUP_FREE = "free"
GROUP_PAID = "paid"


@dataclass(frozen=True)
class OptionDef:
    """Одна опция каталога: что это за операция и как она лицензируется.

    ``requires_competencies`` — опция использует компетенции профиля (LLM-скоринг):
    пока опция доступна, профиль должен содержать компетенции (иначе его нельзя
    сохранить); если опция недоступна — профиль можно сохранить и без них.
    ``available`` — реализована ли операция в системе: для отложенных опций
    (платное гео) False — включать её нельзя, в интерфейсе показывается
    «недоступна».
    """

    key: str
    title: str
    description: str
    group: str
    requires_competencies: bool = False
    available: bool = True


FREE_OPTIONS: tuple[OptionDef, ...] = (
    OptionDef(
        key="search",
        title="Поиск и сбор закупок",
        description=(
            "Обход площадок по критериям профиля (ключевые слова, коды ОКПД2, НМЦК, "
            "регионы) и пополнение базы закупок."
        ),
        group=GROUP_FREE,
    ),
    OptionDef(
        key="work",
        title="Работа с карточками",
        description="Просмотр карточки закупки, приём «в работу», отбраковка.",
        group=GROUP_FREE,
    ),
    OptionDef(
        key="notify",
        title="Уведомления",
        description="Уведомления о закупках по порогу релевантности (Telegram/email).",
        group=GROUP_FREE,
    ),
    OptionDef(
        key="export",
        title="Экспорт и импорт",
        description="Выгрузка CSV, экспорт/импорт профиля и документов.",
        group=GROUP_FREE,
    ),
)

PAID_OPTIONS: tuple[OptionDef, ...] = (
    OptionDef(
        key="scoring",
        title="Скоринг по компетенциям (LLM)",
        description=(
            "Обработка компетенций профиля языковой моделью и эмбеддингами: оценка "
            "соответствия закупки профилю (fit). Пока опция доступна, профиль должен "
            "содержать компетенции."
        ),
        group=GROUP_PAID,
        requires_competencies=True,
    ),
    OptionDef(
        key="analysis_embeddings",
        title="Эмбеддинги при анализе закупки",
        description=(
            "Векторный поиск по документации закупки при анализе (эмбеддинги, "
            "платный внешний сервис)."
        ),
        group=GROUP_PAID,
    ),
    OptionDef(
        key="analysis",
        title="Анализ закупки (LLM)",
        description=(
            "Разбор документации закупки языковой моделью: требования к участнику, "
            "стоп-условия, ответы на вопросы профиля (RAG)."
        ),
        group=GROUP_PAID,
    ),
    OptionDef(
        key="pwin",
        title="Оценка вероятности победы (P(win))",
        description="Оценка шанса победы в закупке (LLM-стадия каскада).",
        group=GROUP_PAID,
    ),
    OptionDef(
        key="margin",
        title="Оценка маржинальности (Margin)",
        description="Оценка маржинальности участия в закупке (LLM-стадия каскада).",
        group=GROUP_PAID,
    ),
    OptionDef(
        key="geo_premium",
        title="Платное геопозиционирование",
        description=(
            "Использование более точного, но платного сервиса геопозиционирования "
            "адресов поставки. Доступ к такому сервису пока не подключён — работает "
            "только бесплатное геопозиционирование."
        ),
        group=GROUP_PAID,
        available=False,
    ),
)

ALL_OPTIONS: tuple[OptionDef, ...] = FREE_OPTIONS + PAID_OPTIONS

PAID_KEYS: tuple[str, ...] = tuple(o.key for o in PAID_OPTIONS)
FREE_KEYS: tuple[str, ...] = tuple(o.key for o in FREE_OPTIONS)


def option_by_key(key: str) -> OptionDef | None:
    """Опция каталога по ключу (или None для неизвестного ключа)."""
    for option in ALL_OPTIONS:
        if option.key == key:
            return option
    return None


def implemented_paid_keys() -> set[str]:
    """Реализованные платные опции (кроме отложенных, напр. geo_premium)."""
    result: set[str] = set()
    for key in PAID_KEYS:
        option = option_by_key(key)
        if option is not None and option.available:
            result.add(key)
    return result


def paid_default_options(enabled: bool = False) -> dict[str, bool]:
    """Начальные переключатели платных опций аккаунта.

    По умолчанию (``enabled=False``) — только бесплатные опции (#6): все платные
    выключены. ``enabled=True`` — «полный» аккаунт (миграция существующих
    пользователей, чтобы не сломать текущее поведение).
    """
    return dict.fromkeys(PAID_KEYS, enabled)


def enabled_paid_options(account_options: dict[str, Any] | None) -> set[str]:
    """Платные опции, включённые в ``options`` аккаунта (только реализованные)."""
    raw = account_options or {}
    enabled = {key for key in PAID_KEYS if raw.get(key) is True}
    return enabled & implemented_paid_keys()


def enabled_options(account_options: dict[str, Any] | None, *, in_trial: bool) -> set[str]:
    """Множество опций, фактически доступных пользователю.

    В триал-режиме доступны бесплатно все реализованные опции поиска и скоринга
    (#7), независимо от аккаунта. После окончания триала платные опции доступны
    только если включены в аккаунте. Бесплатные опции доступны всегда.
    """
    paid = implemented_paid_keys() if in_trial else enabled_paid_options(account_options)
    return set(FREE_KEYS) | paid


def option_requires_competencies(key: str) -> bool:
    option = option_by_key(key)
    return option is not None and option.requires_competencies
