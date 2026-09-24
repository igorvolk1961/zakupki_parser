"""Операции репозитория с аккаунтами пользователей (наборы опций, триал).

Аккаунт — именованный набор переключателей платных опций (``user_accounts``).
У пользователя может быть несколько аккаунтов, активен один (per-user состояние,
как активный профиль). Пользователь сам управляет аккаунтами в личном кабинете,
администратор — через /api/users/{id}/accounts.

``ensure_default_account`` создаёт аккаунт «По умолчанию», если его нет:
- ``paid_default=False`` — только бесплатные опции (саморегистрация, #6);
- ``paid_default=True`` — все платные включены (миграция существующих
  пользователей и создание пользователя администратором: чтобы не сломать
  текущее поведение, пока администратор не ограничил аккаунт).

Триал-режим живёт на пользователе (``users.trial_end_at``). Материализуется
ОДИН РАЗ, в момент создания пользователя: стартовый аккаунт саморегистрации
создаётся со ВСЕМИ платными опциями включёнными (``create_user_with_setup``,
``account_paid_default=True`` для саморегистрации — было ``False`` с runtime-
переопределением «в триале всё включено независимо от account.options»: тогда
пользователь физически не мог выключить отдельную опцию во время триала —
чекбокс в кабинете снимался, но на доступ не влиял). Дальше пользователь сам
управляет аккаунтом как обычно — в т.ч. может выключить любую опцию, пока
триал идёт. По истечении ``trial_end_at`` — лениво, при первом запросе после
истечения (``require_user`` в ``api/app/deps.py``) — платные опции активного
аккаунта СБРАСЫВАЮТСЯ в выключено и ``trial_end_at`` очищается
(``downgrade_expired_trial`` ниже): реальное ограничение доступа через 14
дней, а не просто runtime-флаг.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from zakupki_parser.options import (
    enabled_paid_options,
    implemented_paid_keys,
    option_by_key,
    paid_default_options,
)
from zakupki_parser.storage.db import User, UserAccount
from zakupki_parser.storage.repository.base import RepositoryMixin

logger = logging.getLogger(__name__)


class AccountMixin(RepositoryMixin):
    """Аккаунты пользователей (``user_accounts``) и триал-режим (``users.trial_end_at``)."""

    DEFAULT_ACCOUNT_NAME = "По умолчанию"

    # --- Чтение ------------------------------------------------------------

    async def list_accounts(self, user_id: int) -> list[UserAccount]:
        stmt = (
            select(UserAccount).where(UserAccount.user_id == user_id).order_by(UserAccount.id.asc())
        )
        async with self._db.session() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def accounts_by_users(self, user_ids: list[int]) -> dict[int, list[UserAccount]]:
        """Аккаунты группы пользователей одним запросом (для планировщика/админки)."""
        if not user_ids:
            return {}
        stmt = (
            select(UserAccount)
            .where(UserAccount.user_id.in_(user_ids))
            .order_by(UserAccount.user_id.asc(), UserAccount.id.asc())
        )
        async with self._db.session() as session:
            rows = list((await session.execute(stmt)).scalars().all())
        result: dict[int, list[UserAccount]] = {}
        for row in rows:
            result.setdefault(row.user_id, []).append(row)
        return result

    # --- Изменение ---------------------------------------------------------

    async def ensure_default_account(
        self, user_id: int, *, paid_default: bool = False
    ) -> UserAccount:
        """Возвращает аккаунт «По умолчанию», создавая его при отсутствии.

        Идемпотентно. Новый аккаунт становится активным, только если у
        пользователя ещё нет ни одного активного аккаунта.
        """
        stmt = select(UserAccount).where(
            UserAccount.user_id == user_id, UserAccount.name == self.DEFAULT_ACCOUNT_NAME
        )
        async with self._db.session() as session:
            account = (await session.execute(stmt)).scalar_one_or_none()
            if account is None:
                try:
                    has_active = (
                        await session.execute(
                            select(UserAccount.id)
                            .where(
                                UserAccount.user_id == user_id,
                                UserAccount.is_active.is_(True),
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    account = UserAccount(
                        user_id=user_id,
                        name=self.DEFAULT_ACCOUNT_NAME,
                        options=paid_default_options(enabled=paid_default),
                        is_active=has_active is None,
                    )
                    session.add(account)
                    await session.commit()
                except IntegrityError:
                    # Гонка двух одновременных созданий: uq_user_accounts_user_name.
                    await session.rollback()
                    account = (await session.execute(stmt)).scalar_one_or_none()
            return account  # type: ignore[return-value]

    async def create_account(
        self, user_id: int, name: str, options: dict[str, bool] | None = None
    ) -> UserAccount:
        """Создаёт аккаунт; становится активным, если активного ещё нет."""
        async with self._db.session() as session:
            has_active = (
                await session.execute(
                    select(UserAccount.id)
                    .where(UserAccount.user_id == user_id, UserAccount.is_active.is_(True))
                    .limit(1)
                )
            ).scalar_one_or_none()
            account = UserAccount(
                user_id=user_id,
                name=name,
                options=dict(options or {}),
                is_active=has_active is None,
            )
            session.add(account)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise ValueError(f"Аккаунт с именем «{name}» уже есть") from None
            await session.refresh(account)
        logger.info("Создан аккаунт %s (id=%s, user_id=%s)", name, account.id, user_id)
        return account

    async def update_account(
        self,
        user_id: int,
        account_id: int,
        *,
        name: str | None = None,
        options: dict[str, bool] | None = None,
    ) -> UserAccount | None:
        """Переименовывает аккаунт и/или обновляет переключатели платных опций."""
        async with self._db.session() as session:
            account = (
                await session.execute(
                    select(UserAccount).where(
                        UserAccount.id == account_id, UserAccount.user_id == user_id
                    )
                )
            ).scalar_one_or_none()
            if account is None:
                return None
            new_name = name.strip() if name is not None and name.strip() else None
            if new_name is not None and new_name != account.name:
                try:
                    account.name = new_name
                    await session.flush()
                except IntegrityError:
                    await session.rollback()
                    raise ValueError(f"Аккаунт с именем «{new_name}» уже есть") from None
            if options is not None:
                account.options = dict(options)
            await session.commit()
            await session.refresh(account)
        logger.info("Обновлён аккаунт %s (id=%s, user_id=%s)", account.name, account_id, user_id)
        return account

    async def set_active_account(self, user_id: int, account_id: int) -> UserAccount:
        """Делает аккаунт активным (остальные у пользователя деактивируются)."""
        async with self._db.session() as session:
            account = (
                await session.execute(
                    select(UserAccount).where(
                        UserAccount.user_id == user_id, UserAccount.id == account_id
                    )
                )
            ).scalar_one_or_none()
            if account is None:
                raise ValueError("Аккаунт не найден у пользователя")
            await session.execute(
                update(UserAccount).where(UserAccount.user_id == user_id).values(is_active=False)
            )
            account.is_active = True
            await session.commit()
            await session.refresh(account)
        logger.info(
            "Активным стал аккаунт %s (id=%s, user_id=%s)", account.name, account_id, user_id
        )
        return account

    async def delete_account(self, user_id: int, account_id: int) -> None:
        """Удаляет аккаунт (нельзя удалить последний или активный)."""
        async with self._db.session() as session:
            account = (
                await session.execute(
                    select(UserAccount).where(
                        UserAccount.user_id == user_id, UserAccount.id == account_id
                    )
                )
            ).scalar_one_or_none()
            if account is None:
                raise ValueError("Аккаунт не найден у пользователя")
            total = await session.scalar(
                select(func.count()).select_from(UserAccount).where(UserAccount.user_id == user_id)
            )
            if total is not None and total <= 1:
                raise ValueError("Нельзя удалить последний аккаунт пользователя")
            if account.is_active:
                raise ValueError("Нельзя удалить активный аккаунт — сначала активируйте другой")
            await session.delete(account)
            await session.commit()
            logger.info("Удалён аккаунт %s (id=%s, user_id=%s)", account.name, account_id, user_id)

    # --- Триал-режим --------------------------------------------------------

    async def set_user_trial_end(self, user_id: int, trial_end_at: datetime | None) -> User | None:
        """Задаёт окончание триал-режима пользователя (NULL — без триала)."""
        stmt = select(User).where(User.id == user_id)
        async with self._db.session() as session:
            user = (await session.execute(stmt)).scalar_one_or_none()
            if user is None:
                return None
            user.trial_end_at = trial_end_at
            await session.commit()
            await session.refresh(user)
        logger.info("Триал пользователя %s до %s", user_id, trial_end_at)
        return user

    async def downgrade_expired_trial(self, user_id: int, *, now: datetime | None = None) -> bool:
        """Если триал пользователя истёк — сбрасывает платные опции его АКТИВНОГО
        аккаунта в выключено и очищает ``trial_end_at``. Реальное ограничение
        доступа через 14 дней (а не просто runtime-флаг, см. докстринг модуля):
        пока триал шёл, account.options уже отражал фактический доступ (создан
        со всеми включёнными опциями и мог быть частично выключен пользователем
        вручную) — по истечении он просто обнуляется целиком.

        Вызывается лениво, один раз, при первом запросе ПОСЛЕ истечения
        (``require_user`` в ``api/app/deps.py``, только когда уже известно по
        свежепрочитанному ``User.trial_end_at``, что срок прошёл — не на КАЖДЫЙ
        запрос активного триала). Идемпотентно: после очистки ``trial_end_at``
        условие срабатывания (``trial_end_at is not None and <= now``) больше не
        выполняется — повторных сбросов не будет. Возвращает ``True``, если
        сброс фактически выполнен.
        """
        moment = now or datetime.now(UTC)
        async with self._db.session() as session:
            user = await session.get(User, user_id)
            if user is None or user.trial_end_at is None or user.trial_end_at > moment:
                return False
            user.trial_end_at = None
            stmt = select(UserAccount).where(
                UserAccount.user_id == user_id, UserAccount.is_active.is_(True)
            )
            active = (await session.execute(stmt)).scalar_one_or_none()
            if active is not None:
                active.options = paid_default_options(enabled=False)
            await session.commit()
        logger.info("Триал пользователя %s истёк — платные опции аккаунта сброшены", user_id)
        return True

    async def get_users_with_trial(self, user_ids: list[int]) -> dict[int, datetime | None]:
        """trial_end_at пользователей (для вычисления доступных опций)."""
        if not user_ids:
            return {}
        stmt = select(User.id, User.trial_end_at).where(User.id.in_(user_ids))
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).all()
        return {int(user_id): trial_end_at for user_id, trial_end_at in rows}


def in_trial_now(trial_end_at: datetime | None, now: datetime | None = None) -> bool:
    """Действует ли триал-режим в момент ``now`` (None — триала нет)."""
    if trial_end_at is None:
        return False
    return trial_end_at > (now or datetime.now(UTC))


@dataclass
class EffectiveOptions:
    """Что фактически доступно пользователю в данный момент.

    Платные опции — всегда из АКТИВНОГО аккаунта (``account.options``); триал
    больше НЕ переопределяет их во время действия (материализуется один раз в
    момент создания пользователя — см. докстринг модуля). ``in_trial``/
    ``trial_end_at`` — информационные поля (личный кабинет: «дней осталось»),
    на ``paid_enabled`` не влияют. Если аккаунтов у пользователя нет вовсе
    (легаси/сервис-пользователь до бэкфилла) — платные опции считаются
    доступными: текущее поведение не меняется.
    """

    in_trial: bool
    trial_end_at: datetime | None
    active_account: UserAccount | None
    # Ключи платных опций, доступных пользователю (с учётом триала/аккаунта).
    paid_enabled: set[str]

    def has_option(self, key: str) -> bool:
        """Доступна ли опция (бесплатные — всегда)."""
        option = option_by_key(key)
        if option is None:
            return False
        if option.group == "free":
            return True
        return key in self.paid_enabled


def effective_options(
    accounts: list[UserAccount],
    trial_end_at: datetime | None,
    *,
    now: datetime | None = None,
) -> EffectiveOptions:
    """Вычисляет эффективный доступ к платным опциям пользователя.

    Аккаунты могут быть пустым списком (легаси до миграции аккаунтов): тогда
    платные опции доступны все — иначе мы сломали бы существующих пользователей,
    у которых аккаунтов ещё нет. Триал на этот расчёт не влияет (см. докстринг
    ``EffectiveOptions``/модуля) — ``paid_enabled`` всегда из ``active.options``.
    """
    active: UserAccount | None = None
    for account in accounts:
        if account.is_active:
            active = account
            break
    trial_active = in_trial_now(trial_end_at, now)
    if not accounts or active is None:
        # Легаси-пользователь без аккаунтов или нарушенный инвариант «нет
        # активного»: доступны все реализованные платные опции (текущее
        # поведение не меняем).
        paid_enabled = implemented_paid_keys()
    else:
        paid_enabled = enabled_paid_options(active.options or {})
    return EffectiveOptions(
        in_trial=trial_active,
        trial_end_at=trial_end_at,
        active_account=active,
        paid_enabled=paid_enabled,
    )
