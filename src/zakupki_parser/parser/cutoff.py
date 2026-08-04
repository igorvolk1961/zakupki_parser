"""Логика стоп-порога по дате публикации."""

from __future__ import annotations

from datetime import datetime


def is_older_than_cutoff(upd: str | datetime | None, cutoff: datetime) -> bool | None:
    """Проверяет, должна ли запись остановить цикл по дате публикации.

    На площадке доступна только дата (без времени), поэтому сравнение ведётся по
    календарному дню: запись «старее» порога — когда её день строго меньше дня
    порога. Возвращает True — стоп, False — обрабатывать далее, None — некорректная
    дата (обрабатывать).
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
    return upd_dt.date() < cutoff.date()
