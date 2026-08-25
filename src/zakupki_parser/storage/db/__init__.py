"""SQLAlchemy 2.x модели и работа с БД (PostgreSQL).

Модели разбиты по доменам (подпакеты): ``base`` (DeclarativeBase), ``engine``
(обёртка над async engine/session), ``customer``, ``procurement``, ``evaluation``,
``user``, ``profile``. Здесь — реэкспорт для совместимости с прежним модулем
``storage/db.py``.
"""

from __future__ import annotations

from zakupki_parser.storage.db.base import Base
from zakupki_parser.storage.db.customer import Customer
from zakupki_parser.storage.db.engine import Database
from zakupki_parser.storage.db.evaluation import ProcurementEvaluation
from zakupki_parser.storage.db.procurement import (
    Platform,
    ProcedureCategory,
    ProcedureType,
    ProcedureTypeMapping,
    Procurement,
)
from zakupki_parser.storage.db.profile import (
    ExperienceConfirmationType,
    Keyword,
    LicenseType,
    Profile,
    ProfileExperience,
    ProfileLicense,
)
from zakupki_parser.storage.db.user import User

__all__ = [
    "Base",
    "Customer",
    "Database",
    "ExperienceConfirmationType",
    "Keyword",
    "LicenseType",
    "Platform",
    "ProcedureCategory",
    "ProcedureType",
    "ProcedureTypeMapping",
    "Procurement",
    "ProcurementEvaluation",
    "Profile",
    "ProfileExperience",
    "ProfileLicense",
    "User",
]
