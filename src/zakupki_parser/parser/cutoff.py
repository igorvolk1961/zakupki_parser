"""Логика стоп-порога по дате публикации."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Часовой пояс площадок — МСК (UTC+3): даты карточек приходят и хранятся в нём.
# Порог же из БД приходит в UTC (таймзона сессии PostgreSQL) — см. ниже.
MSK = timezone(timedelta(hours=3))


def is_older_than_cutoff(upd: str | datetime | None, cutoff: datetime) -> bool | None:
    """Проверяет, должна ли запись остановить цикл по дате публикации.

    На площадке доступна только дата (без времени), поэтому сравнение ведётся по
    календарному дню: запись «старее» порога — когда её день строго меньше дня
    порога. Возвращает True — стоп, False — обрабатывать далее, None — некорректная
    дата (обрабатывать).

    Порог из БД приходит в UTC (таймзона сессии PostgreSQL), а даты записей — в
    МСК (площадочный пояс). Сравнение календарных дней в разных поясах сдвигает
    границу на сутки, поэтому порог приводится к поясу даты записи (naive-дата
    считается площадочной, МСК).
    """
    if upd is None:
        return None
    if isinstance(upd, str):
        try:
            upd_dt = datetime.fromisoformat(upd)
        except ValueError:
            return None
    else:
        upd_dt = upd
    cutoff_dt = cutoff
    if cutoff_dt.tzinfo is not None:
        tz = upd_dt.tzinfo if upd_dt.tzinfo is not None else MSK
        cutoff_dt = cutoff_dt.astimezone(tz)
    return upd_dt.date() < cutoff_dt.date()
