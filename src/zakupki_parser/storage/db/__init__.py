"""SQLAlchemy 2.x модели и работа с БД (PostgreSQL).

Модели разбиты по доменам (подпакеты): ``base`` (DeclarativeBase), ``engine``
(обёртка над async engine/session), ``customer``, ``procurement``, ``evaluation``,
``user``, ``profile``. Здесь — публичный интерфейс пакета (реэкспорт).
"""

from __future__ import annotations

from zakupki_parser.storage.db.account import UserAccount
from zakupki_parser.storage.db.base import Base
from zakupki_parser.storage.db.customer import Customer
from zakupki_parser.storage.db.cycle_stats import ParserCycleStats
from zakupki_parser.storage.db.engine import Database
from zakupki_parser.storage.db.evaluation import ProcurementEvaluation
from zakupki_parser.storage.db.platform_stats import ParserPlatformStats
from zakupki_parser.storage.db.procurement import (
    Platform,
    ProcedureCategory,
    ProcedureType,
    ProcedureTypeMapping,
    Procurement,
)
from zakupki_parser.storage.db.profile import (
    ALL_PLATFORMS_SENTINEL,
    ExperienceConfirmationType,
    Keyword,
    LicenseType,
    Profile,
    ProfileExperience,
    ProfileLicense,
)
from zakupki_parser.storage.db.search_index import ProcurementSearchIndex
from zakupki_parser.storage.db.site_source import SOURCE_FINAL_STATUSES, SiteSource
from zakupki_parser.storage.db.user import User

__all__ = [
    "SOURCE_FINAL_STATUSES",
    "SiteSource",
    "ALL_PLATFORMS_SENTINEL",
    "Base",
    "Customer",
    "Database",
    "ExperienceConfirmationType",
    "Keyword",
    "LicenseType",
    "ParserCycleStats",
    "ParserPlatformStats",
    "Platform",
    "ProcedureCategory",
    "ProcedureType",
    "ProcedureTypeMapping",
    "Procurement",
    "ProcurementEvaluation",
    "ProcurementSearchIndex",
    "Profile",
    "ProfileExperience",
    "ProfileLicense",
    "User",
    "UserAccount",
]
