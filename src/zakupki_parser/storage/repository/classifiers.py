"""Поиск по глобальным справочникам-классификаторам (ОКПД2 и т.п.)."""

from __future__ import annotations

from sqlalchemy import or_, select

from zakupki_parser.storage.db import Okpd2Code
from zakupki_parser.storage.repository.base import RepositoryMixin


class ClassifierMixin(RepositoryMixin):
    """Операции с ``okpd2_codes`` (справочник, ~20300 записей — только поиск,

    не полный список: см. ``routes/reference.py`` для маленьких CRUD-справочников."""

    async def search_okpd2_codes(self, query: str, limit: int = 20) -> list[Okpd2Code]:
        """Коды ОКПД2 по префиксу кода ИЛИ подстроке наименования.

        ``query`` — как есть, без нормализации (код «62.02» ищется префиксом,
        «принтер» — подстрокой в названии; регистр не учитывается). Пустой
        запрос — пусто (виджет не должен отдавать все 20300 строк). Приоритет
        точного/префиксного совпадения по коду над совпадением по названию —
        порядок ``ORDER BY`` (совпадение по коду первым), затем по коду.
        """
        q = query.strip()
        if not q:
            return []
        code_prefix = f"{q}%"
        name_substr = f"%{q}%"
        code_match = Okpd2Code.code.ilike(code_prefix)
        stmt = (
            select(Okpd2Code)
            .where(or_(code_match, Okpd2Code.name.ilike(name_substr)))
            .order_by(code_match.desc(), Okpd2Code.code.asc())
            .limit(limit)
        )
        async with self._db.session() as session:
            return list((await session.execute(stmt)).scalars().all())
