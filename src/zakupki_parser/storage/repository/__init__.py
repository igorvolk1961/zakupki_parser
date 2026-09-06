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
from zakupki_parser.storage.repository.evaluations import EvaluationMixin
from zakupki_parser.storage.repository.procurements import ProcurementMixin
from zakupki_parser.storage.repository.profiles import ProfileMixin
from zakupki_parser.storage.repository.users import UserMixin
from zakupki_parser.storage.repository.work import WorkMixin


class ProcurementRepository(
    ProcurementMixin,
    CustomerMixin,
    UserMixin,
    ProfileMixin,
    AccountMixin,
    EvaluationMixin,
    WorkMixin,
):
    """Операции с таблицей ``procurements`` (и смежными доменами).

    Наследует реализацию из доменных миксинов; собственной логики не добавляет.
    """


__all__ = [
    "ProcurementRepository",
    "effective_is_active",
    "_round_score",
]
