"""Операции репозитория с профилями фильтрации и их справочниками (BR-03/BR-07)."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from zakupki_parser.auth import DEFAULT_PROFILE_ROLES, has_default_profile_role
from zakupki_parser.storage.db import (
    ExperienceConfirmationType,
    Keyword,
    LicenseType,
    Profile,
    ProfileExperience,
    ProfileLicense,
    User,
)
from zakupki_parser.storage.repository.base import RepositoryMixin

logger = logging.getLogger(__name__)


_LICENSES_REF_FILE = "licenze_kind.md"


def _licenses_ref() -> Any:
    """Путь к справочнику видов лицензий: ``docs/references/licenze_kind.md`` (данные сида БД).

    Виды лицензий используются ВНЕ сервисов — для предварительного заполнения БД
    (сид ``license_types``), поэтому файл лежит как справочные данные в ``docs/``,
    а не как рантайм-ресурс какого-либо сервиса.
    """
    return Path(__file__).resolve().parents[4] / "docs" / "references" / _LICENSES_REF_FILE


def _clean_license_name(raw: str) -> str:
    """Чистка строки вида лицензии из справочника (хвостовые знаки, пробелы)."""
    s = re.sub(r"^-\s*", "", raw).strip()
    s = re.sub(r"[\.,;:\s]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s


@lru_cache(maxsize=1)
def license_type_names() -> list[str]:
    """Каталожные наименования видов лицензий (docs/references/licenze_kind.md).

    Единый источник истины справочника ``license_types`` (миграция 1.50, ``name`` —
    уникальный ключ). Повторный вызов безопасен (кэш); при недоступном файле — пустой
    список (сид не меняет существующие строки).
    """
    ref = _licenses_ref()
    if not ref.is_file():
        logger.warning("Не найден файл справочника лицензий: %s", ref)
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in ref.read_text(encoding="utf-8").splitlines():
        if not raw.lstrip().startswith("-"):
            continue
        name = _clean_license_name(raw)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    return out


class ProfileMixin(RepositoryMixin):
    """Профили фильтрации (``profiles``), ключевые слова и факты BR-03."""

    DEFAULT_PROFILE_NAME = "default"

    CONFIRMATION_TYPES_SEED = [
        ("platform", "Через электронную площадку (ПП РФ 2571)"),
        ("documents", "Сканы договоров/актов"),
        ("registry", "Выписка из реестра контрактов"),
    ]

    async def get_profile(self, user_id: int, profile_id: int) -> Profile | None:
        stmt = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_profile_by_id(self, profile_id: int) -> Profile | None:
        """Профиль по ``profile_id`` без привязки к пользователю (системный скоуп).

        Используется внутренним конвейером, когда он явно указывает профиль
        (``X-Profile-ID``) вместо сервис-аккаунта.
        """
        stmt = select(Profile).where(Profile.id == profile_id)
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_profile_by_name(self, user_id: int, name: str) -> Profile | None:
        stmt = select(Profile).where(Profile.user_id == user_id, Profile.name == name)
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def list_profiles(
        self, user_id: int, limit: int = 100, offset: int = 0
    ) -> tuple[list[Profile], int]:
        stmt = (
            select(Profile)
            .where(Profile.user_id == user_id)
            .order_by(Profile.id.asc())
            .limit(limit)
            .offset(offset)
        )
        count_stmt = select(func.count(Profile.id)).where(Profile.user_id == user_id)
        async with self._db.session() as session:
            rows = list((await session.execute(stmt)).scalars().all())
            total = int((await session.execute(count_stmt)).scalar_one())
        return rows, total

    async def list_enabled_profiles_for_active_users(self) -> list[Profile]:
        """Все включённые профили незаблокированных пользователей.

        Возвращает ``Profile.enabled = true`` для пользователей с ролью, которой
        положен профиль (``DEFAULT_PROFILE_ROLES``: ``user`` или ``analyst``,
        BR-07) и со статусом ``active`` (не ``blocked``). Это рабочий набор
        профилей, которые парсер обходит при постоянном мониторинге: включая не
        выбранные пользователем (is_active=false), но только включённые
        (enabled=true). Постоянные администраторы/devops, не имеющие профилей,
        не учитываются.
        """
        stmt = (
            select(Profile)
            .join(User, User.id == Profile.user_id)
            .where(
                Profile.enabled.is_(True),
                User.status == "active",
                or_(*[User.roles.contains([role]) for role in DEFAULT_PROFILE_ROLES]),
            )
            .order_by(User.id.asc(), Profile.id.asc())
        )
        async with self._db.session() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def get_active_profile(self, user_id: int) -> Profile | None:
        """Активный профиль пользователя (per-user состояние).

        Приоритет: 1) ``is_active=true``; 2) профиль ``default``; 3) первый включённый.
        Один запрос (ORDER BY + LIMIT 1). Полностью отключённые профили, не
        являющиеся default, не возвращаются.
        """
        stmt = (
            select(Profile)
            .where(
                Profile.user_id == user_id,
                or_(
                    Profile.is_active.is_(True),
                    Profile.name == self.DEFAULT_PROFILE_NAME,
                    Profile.enabled.is_(True),
                ),
            )
            .order_by(
                Profile.is_active.desc(),
                (Profile.name == self.DEFAULT_PROFILE_NAME).desc(),
                Profile.id.asc(),
            )
            .limit(1)
        )
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def set_active_profile(self, user_id: int, profile_id: int) -> Profile:
        """Делает профиль активным (сбрасывает остальные у пользователя)."""
        async with self._db.session() as session:
            stmt = select(Profile).where(Profile.user_id == user_id, Profile.id == profile_id)
            profile = (await session.execute(stmt)).scalar_one_or_none()
            if profile is None:
                raise ValueError("Профиль не найден у пользователя")
            await session.execute(
                update(Profile).where(Profile.user_id == user_id).values(is_active=False)
            )
            profile.is_active = True
            await session.commit()
            # updated_at (server onupdate) генерируется в БД: с expire_on_commit=False
            # SQLAlchemy не подставляет его в объект без refresh. После выхода из
            # сессии объект detached, и _profile_out упадёт с DetachedInstanceError.
            await session.refresh(profile)
            return profile

    async def delete_profile(self, user_id: int, profile_id: int) -> None:
        """Удаляет профиль пользователя.

        Нельзя удалить последний профиль пользователя или активный (сначала
        активируйте другой). Оценки профиля (procurement_evaluations) и слова
        (keywords) удаляются каскадом (FK ON DELETE CASCADE).
        """
        async with self._db.session() as session:
            profile = (
                await session.execute(
                    select(Profile).where(Profile.user_id == user_id, Profile.id == profile_id)
                )
            ).scalar_one_or_none()
            if profile is None:
                raise ValueError("Профиль не найден у пользователя")
            total = await session.scalar(
                select(func.count()).select_from(Profile).where(Profile.user_id == user_id)
            )
            if total is not None and total <= 1:
                raise ValueError("Нельзя удалить последний профиль пользователя")
            if profile.is_active:
                raise ValueError("Сначала активируйте другой профиль")
            await session.delete(profile)
            await session.commit()
            logger.info("Удалён профиль %s (id=%s, user_id=%s)", profile.name, profile_id, user_id)

    async def get_profile_keywords(self, profile_id: int) -> dict[str, list[str]]:
        """Возвращает ключевые слова профиля из таблицы ``keywords`` (канонический источник)."""
        stmt = select(Keyword).where(Keyword.profile_id == profile_id).order_by(Keyword.id)
        async with self._db.session() as session:
            rows = list((await session.execute(stmt)).scalars().all())
        return {
            "keywords": [r.word for r in rows if r.type == "keyword"],
            "exclusion_words": [r.word for r in rows if r.type == "exclusion"],
        }

    async def list_profiles_keywords(
        self, profile_ids: list[int]
    ) -> dict[int, dict[str, list[str]]]:
        """Батч-чтение слов нескольких профилей одним запросом (без N+1)."""
        if not profile_ids:
            return {}
        stmt = (
            select(Keyword)
            .where(Keyword.profile_id.in_(profile_ids))
            .order_by(Keyword.profile_id, Keyword.id)
        )
        async with self._db.session() as session:
            rows = list((await session.execute(stmt)).scalars().all())
        result: dict[int, dict[str, list[str]]] = {}
        for row in rows:
            bucket = result.setdefault(row.profile_id, {"keywords": [], "exclusion_words": []})
            key = "keywords" if row.type == "keyword" else "exclusion_words"
            bucket[key].append(row.word)
        return result

    async def ensure_reference_data(self) -> None:
        """Идемпотентный сид справочников профиля (виды лицензий и BR-03).

        Нужен и в проде (при неполной миграции), и в тестах (там схема создаётся
        через ``Base.metadata.create_all`` без Liquibase). Повторный сид безопасен:
        ``ON CONFLICT (name|code) DO NOTHING``.
        """
        async with self._db.session() as session:
            license_rows = [
                {"name": name, "sort_order": i + 1} for i, name in enumerate(license_type_names())
            ]
            if license_rows:
                stmt = pg_insert(LicenseType).values(license_rows)
                await session.execute(stmt.on_conflict_do_nothing(index_elements=["name"]))
            conf_rows = [
                {"code": code, "name": name, "sort_order": i + 1}
                for i, (code, name) in enumerate(self.CONFIRMATION_TYPES_SEED)
            ]
            stmt = pg_insert(ExperienceConfirmationType).values(conf_rows)
            await session.execute(stmt.on_conflict_do_nothing(index_elements=["code"]))
            await session.commit()

    async def list_license_types(self) -> list[LicenseType]:
        return await self.list_reference_rows(LicenseType)

    async def list_confirmation_types(self) -> list[ExperienceConfirmationType]:
        return await self.list_reference_rows(ExperienceConfirmationType)

    # --- Общий CRUD справочных таблиц (админ-страница «Справочники») ---------
    # Генерик над моделями-справочниками (LicenseType, ExperienceConfirmationType
    # и будущими): расширяется регистрацией новой таблицы в routes/reference.py
    # без добавления методов в репозиторий.

    async def list_reference_rows(self, model: type[Any]) -> list[Any]:
        """Все строки справочника; сортировка по sort_order+id, если она есть."""
        stmt = select(model)
        if hasattr(model, "sort_order"):
            stmt = stmt.order_by(model.sort_order.asc(), model.id.asc())
        else:
            stmt = stmt.order_by(model.id.asc())
        async with self._db.session() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def get_reference_row(self, model: type[Any], row_id: int) -> Any | None:
        async with self._db.session() as session:
            return await session.get(model, row_id)

    async def create_reference_row(self, model: type[Any], data: dict[str, Any]) -> Any:
        row = model(**data)
        async with self._db.session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row

    async def update_reference_row(
        self, model: type[Any], row_id: int, data: dict[str, Any]
    ) -> Any | None:
        async with self._db.session() as session:
            row = await session.get(model, row_id)
            if row is None:
                return None
            for key, value in data.items():
                setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
        return row

    async def delete_reference_row(self, model: type[Any], row_id: int) -> bool:
        async with self._db.session() as session:
            row = await session.get(model, row_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
        return True

    async def list_licenses(self, profile_id: int) -> list[ProfileLicense]:
        stmt = (
            select(ProfileLicense)
            .where(ProfileLicense.profile_id == profile_id)
            .order_by(ProfileLicense.id)
        )
        async with self._db.session() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def get_license(self, profile_id: int, license_id: int) -> ProfileLicense | None:
        stmt = select(ProfileLicense).where(
            ProfileLicense.id == license_id,
            ProfileLicense.profile_id == profile_id,
        )
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def create_license(self, profile_id: int, data: dict[str, Any]) -> ProfileLicense:
        row = ProfileLicense(profile_id=profile_id, **data)
        async with self._db.session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row

    async def update_license(
        self, profile_id: int, license_id: int, data: dict[str, Any]
    ) -> ProfileLicense | None:
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(ProfileLicense).where(
                        ProfileLicense.id == license_id,
                        ProfileLicense.profile_id == profile_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            for key, value in data.items():
                setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
        return row

    async def delete_license(self, profile_id: int, license_id: int) -> bool:
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(ProfileLicense).where(
                        ProfileLicense.id == license_id,
                        ProfileLicense.profile_id == profile_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
        return True

    async def list_experience(self, profile_id: int) -> list[ProfileExperience]:
        stmt = (
            select(ProfileExperience)
            .where(ProfileExperience.profile_id == profile_id)
            .order_by(ProfileExperience.id)
        )
        async with self._db.session() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def get_profile_facts(self, profile_id: int) -> dict[str, list[str]]:
        """Наименования видов лицензий и коды подтверждения опыта профиля.

        Лёгкий срез фактов BR-03 для Stage B (сопоставление фактов ТЗ с профилем
        выполняется кодом, профиль в промпт не попадает). Лицензии теперь
        идентифицируются наименованием (``license_types.name``), а не кодом.
        """
        async with self._db.session() as session:
            license_stmt = (
                select(LicenseType.name)
                .join(ProfileLicense, ProfileLicense.license_type_id == LicenseType.id)
                .where(ProfileLicense.profile_id == profile_id)
            )
            experience_stmt = (
                select(ExperienceConfirmationType.code)
                .join(
                    ProfileExperience,
                    ProfileExperience.confirmation_type_id == ExperienceConfirmationType.id,
                )
                .where(ProfileExperience.profile_id == profile_id)
            )
            license_names = list((await session.execute(license_stmt)).scalars().all())
            experience_codes = list((await session.execute(experience_stmt)).scalars().all())
        return {"license_names": license_names, "experience_codes": experience_codes}

    async def get_experience(self, profile_id: int, experience_id: int) -> ProfileExperience | None:
        stmt = select(ProfileExperience).where(
            ProfileExperience.id == experience_id,
            ProfileExperience.profile_id == profile_id,
        )
        async with self._db.session() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def create_experience(self, profile_id: int, data: dict[str, Any]) -> ProfileExperience:
        row = ProfileExperience(profile_id=profile_id, **data)
        async with self._db.session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row

    async def update_experience(
        self, profile_id: int, experience_id: int, data: dict[str, Any]
    ) -> ProfileExperience | None:
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(ProfileExperience).where(
                        ProfileExperience.id == experience_id,
                        ProfileExperience.profile_id == profile_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            for key, value in data.items():
                setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
        return row

    async def delete_experience(self, profile_id: int, experience_id: int) -> bool:
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(ProfileExperience).where(
                        ProfileExperience.id == experience_id,
                        ProfileExperience.profile_id == profile_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
        return True

    async def ensure_default_profile(
        self, user_id: int, roles: list[str] | None = None
    ) -> Profile | None:
        """Возвращает default-профиль пользователя, создавая пустой, если его нет.

        Профиль создаётся ТОЛЬКО для ролей ``user``/``analyst`` (BR-07): у
        администратора/devops без этих ролей профиля быть не должно — им профиль
        не положен, возвращается ``None``. Ключевые слова НЕ заполняются
        автоматически — их загружает скрипт ``seed-profile`` (R8).
        """
        if roles is None:
            user = await self._get_user(user_id)
            roles = user.roles if user is not None else []
        if not has_default_profile_role(roles):
            return None
        async with self._db.session() as session:
            profile = (
                await session.execute(
                    select(Profile).where(
                        Profile.user_id == user_id, Profile.name == self.DEFAULT_PROFILE_NAME
                    )
                )
            ).scalar_one_or_none()
            if profile is None:
                try:
                    profile = Profile(
                        name=self.DEFAULT_PROFILE_NAME,
                        user_id=user_id,
                        enabled=True,
                        is_active=True,
                        competencies="",
                    )
                    session.add(profile)
                    await session.commit()
                except IntegrityError:
                    # Гонка двух одновременных созданий default-профиля: констрейнт
                    # uq_profiles_user_name побеждает. Откатываем и перечитываем
                    # уже существующую строку (идемпотентно).
                    await session.rollback()
                    profile = (
                        await session.execute(
                            select(Profile).where(
                                Profile.user_id == user_id,
                                Profile.name == self.DEFAULT_PROFILE_NAME,
                            )
                        )
                    ).scalar_one_or_none()
        return profile

    async def delete_profiles_without_default_role(self) -> int:
        """Удаляет профили пользователей без ролей user/analyst (одноразовая чистка).

        Возвращает число удалённых профилей. Связанные строки (keywords/licenses/
        experience/evaluations) удаляются каскадом (ORM delete-orphan + FK CASCADE).
        """
        deleted = 0
        async with self._db.session() as session:
            users = (await session.execute(select(User.id, User.roles))).all()
            for user_id, roles in users:
                if has_default_profile_role(roles):
                    continue
                profiles = (
                    (await session.execute(select(Profile).where(Profile.user_id == user_id)))
                    .scalars()
                    .all()
                )
                for profile in profiles:
                    await session.delete(profile)
                    deleted += 1
            await session.commit()
        if deleted:
            logger.info("Удалены профили пользователей без ролей user/analyst: %d", deleted)
        return deleted

    async def _get_user(self, user_id: int) -> User | None:
        async with self._db.session() as session:
            return (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()

    async def seed_default_profile(self, user_id: int, seed: dict[str, Any]) -> Profile:
        """Создаёт/обновляет активный профиль ``default`` пользователя (R8).

        ``seed`` — как в ``upsert_profile`` (+ ``keywords``/``exclusion_words``,
        которые пишутся в таблицу ``keywords``).
        """
        profile = await self.upsert_profile({**seed, "name": "default"}, user_id)
        return profile

    async def upsert_profile(
        self, data: dict[str, Any], user_id: int, profile_id: int | None = None
    ) -> Profile:
        """Создаёт или обновляет профиль пользователя (одной транзакцией).

        По умолчанию ключ — ``user_id + name``: смена имени в ``data`` создаст
        новый профиль (используется для create). Если передан ``profile_id`` —
        обновляется конкретный профиль пользователя, в т.ч. переименование
        (PUT /api/clients/{id}).

        В одной транзакции сохраняется весь профиль: поля, ключевые слова/слова-
        исключения (таблица ``keywords``, канонический источник), лицензии и опыт
        (``profile_licenses``/``profile_experience``, полная замена — BR-03).
        При ``is_active=true`` остальные профили пользователя деактивируются
        (гарантия единственного активного профиля).
        """
        return await self._upsert_profile(data, user_id, profile_id, _retry_left=1)

    async def _upsert_profile(
        self,
        data: dict[str, Any],
        user_id: int,
        profile_id: int | None,
        _retry_left: int,
    ) -> Profile:
        name = data.get("name")
        if not name:
            raise ValueError("profiles.name обязателен")
        # Компетенции — всегда канонический JSON схемы Profile (BR-07). Обязательная
        # валидация на записи: пустой профиль (без компетенций/позиционирования)
        # не может участвовать в сборе закупок, поэтому его сохранение запрещено.
        if "competencies" in data:
            from zakupki_parser.storage.competencies import (
                CompetenciesError,
                normalize_competencies,
                parse_competencies,
            )
            from zakupki_parser.storage.competencies import (
                is_empty as _competencies_is_empty,
            )

            profile_model = parse_competencies(str(data.get("competencies") or ""))
            if _competencies_is_empty(profile_model):
                raise CompetenciesError(
                    "Профиль без компетенций нельзя сохранить: он не участвует в сборе закупок"
                )
            data = {**data, "competencies": normalize_competencies(data.get("competencies"))}
        wants_keywords = "keywords" in data or "exclusion_words" in data
        async with self._db.session() as session:
            stmt = (
                select(Profile).where(Profile.user_id == user_id, Profile.id == profile_id)
                if profile_id is not None
                else select(Profile).where(Profile.user_id == user_id, Profile.name == name)
            )
            profile = (await session.execute(stmt)).scalar_one_or_none()
            if profile is None:
                profile = Profile(name=name, user_id=user_id)
                session.add(profile)
            # Переименование: найденный по id профиль мог иметь другое имя.
            profile.name = name
            if "enabled" in data:
                profile.enabled = bool(data["enabled"])
            if "competencies" in data:
                profile.competencies = str(data["competencies"])
            if "questions" in data:
                # Защита от редактирования обязательных системных вопросов (sys:*):
                # они живут вне профиля (analysis_service) и не сохраняются в профиль.
                questions = list(data["questions"])
                profile.questions = [
                    q for q in questions if not str(q.get("id", "")).startswith("sys:")
                ]
            if "target_etp" in data:
                profile.target_etp = list(data["target_etp"])
            if "target_laws" in data:
                profile.target_laws = list(data["target_laws"])
            if "target_regions" in data:
                profile.target_regions = list(data["target_regions"])
            if "max_region_distance_km" in data:
                profile.max_region_distance_km = data["max_region_distance_km"]
            if "min_fit_threshold" in data:
                profile.min_fit_threshold = data["min_fit_threshold"]
            if "okpd_codes" in data:
                profile.okpd_codes = list(data["okpd_codes"])
            if "nmck_min" in data:
                profile.nmck_min = data["nmck_min"]
            if "nmck_max" in data:
                profile.nmck_max = data["nmck_max"]
            # Профиль становится активным: явно (is_active=true) или по умолчанию
            # для профиля «default» (per-user состояние, BR-07).
            wants_active = data.get("is_active")
            if profile.enabled is False:
                # Отключённый профиль не может быть активным.
                profile.is_active = False
                wants_active = False
            if wants_active or (wants_active is None and name == self.DEFAULT_PROFILE_NAME):
                await session.execute(
                    update(Profile).where(Profile.user_id == user_id).values(is_active=False)
                )
                profile.is_active = True
            # Нужен id профиля до записи дочерних таблиц (новая строка ещё не в БД).
            try:
                # flush() выполняет INSERT новой строки — гонка на (user_id, name)
                # всплывает здесь, а не на commit() (см. except ниже).
                await session.flush()
                # Ключевые слова — полная замена в той же транзакции.
                if wants_keywords:
                    await session.execute(delete(Keyword).where(Keyword.profile_id == profile.id))
                    rows = [
                        Keyword(profile_id=profile.id, word=word, type=kind)
                        for kind, words in (
                            ("keyword", data.get("keywords") or []),
                            ("exclusion", data.get("exclusion_words") or []),
                        )
                        for word in dict.fromkeys(words)
                    ]
                    session.add_all(rows)
                # Лицензии и опыт — часть профиля (BR-03): полная замена в той же
                # транзакции (веб-редактор держит их в форме до «Сохранить профиль»).
                if "licenses" in data:
                    await session.execute(
                        delete(ProfileLicense).where(ProfileLicense.profile_id == profile.id)
                    )
                    for entry in data.get("licenses") or []:
                        session.add(ProfileLicense(profile_id=profile.id, **entry))
                if "experience" in data:
                    await session.execute(
                        delete(ProfileExperience).where(ProfileExperience.profile_id == profile.id)
                    )
                    for entry in data.get("experience") or []:
                        session.add(ProfileExperience(profile_id=profile.id, **entry))
                await session.commit()
            except IntegrityError:
                if _retry_left <= 0:
                    raise
                # Гонка двух одновременных созданий профиля с одним (user_id, name):
                # констрейнт uq_profiles_user_name. Откатываем и пробуем ещё раз —
                # на повторном проходе строка уже существует, выполнится update.
                await session.rollback()
                return await self._upsert_profile(data, user_id, profile_id, _retry_left - 1)
            # updated_at (server onupdate) генерируется в БД: с expire_on_commit=False
            # SQLAlchemy не подставляет его в объект без refresh. После выхода из
            # сессии объект detached, и _profile_out упадёт с DetachedInstanceError.
            await session.refresh(profile)
        logger.info("Сохранён профиль %s (id=%s, user_id=%s)", name, profile.id, user_id)
        return profile
