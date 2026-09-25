"""Репозиторий закупок: запись с контролем дубликатов и чтение.

Доменные операции разнесены по миксинам (подпакеты): ``base`` (общие хелперы),
``procurements``, ``customers``, ``users``, ``profiles``, ``evaluations``.
Класс ``ProcurementRepository`` собирает миксины, сохраняя прежний публичный
интерфейс модуля ``storage/repository.py``.
"""

from __future__ import annotations

from zakupki_parser.storage.repository.accounts import AccountMixin
from zakupki_parser.storage.repository.base import _round_score, effective_is_active
from zakupki_parser.storage.repository.customers import CustomerMixin
from zakupki_parser.storage.repository.cycle_stats import CycleStatsMixin
from zakupki_parser.storage.repository.db_stats import DbStatsMixin
from zakupki_parser.storage.repository.evaluations import EvaluationMixin
from zakupki_parser.storage.repository.platform_stats import PlatformStatsMixin
from zakupki_parser.storage.repository.procurements import ProcurementMixin
from zakupki_parser.storage.repository.profiles import ProfileMixin
from zakupki_parser.storage.repository.search_index import SearchIndexMixin
from zakupki_parser.storage.repository.site_sources import SiteSourceMixin
from zakupki_parser.storage.repository.users import UserMixin


class ProcurementRepository(
    ProcurementMixin,
    CustomerMixin,
    UserMixin,
    ProfileMixin,
    AccountMixin,
    EvaluationMixin,
    SearchIndexMixin,
    CycleStatsMixin,
    DbStatsMixin,
    PlatformStatsMixin,
    SiteSourceMixin,
):
    """Операции с таблицей ``procurements`` (и смежными доменами).

    Наследует реализацию из доменных миксинов; собственной логики не добавляет.
    """


__all__ = [
    "ProcurementRepository",
    "effective_is_active",
    "_round_score",
]
