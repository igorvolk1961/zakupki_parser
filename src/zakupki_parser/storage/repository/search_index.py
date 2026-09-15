"""Поисковый индекс закупок (описание+документы, фоновая индексация по ОКПД2).

Результат воркера ``indexing_service`` (стадия ``index``, вне каскада Fit/P(win)/
Margin) возвращается через ``scoring_transport`` -> ``POST
/api/procurements/{id}/index-result`` -> ``save_index_result`` ниже.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zakupki_parser.okpd import normalize_okpd2_field
from zakupki_parser.storage.db import Procurement, ProcurementSearchIndex
from zakupki_parser.storage.repository.base import RepositoryMixin

logger = logging.getLogger(__name__)


class SearchIndexMixin(RepositoryMixin):
    """Операции с ``procurement_search_index`` (IndexingConfig, indexing_service)."""

    async def save_index_result(
        self,
        procurement_id: int,
        status: str,
        *,
        document_text: str | None = None,
        content_hash: str | None = None,
        error_message: str | None = None,
        max_attempts: int = 5,
    ) -> bool:
        """Upsert результата фоновой индексации закупки (find-or-create по procurement_id).

        ``okpd2_normalized`` вычисляется здесь же из актуального ``procurements.
        okpd2_codes`` (не принимается от indexing_service — сервис не имеет доступа
        к БД, но не должен и держать копию логики нормализации). ``document_text``
        приходит от indexing_service по HTTP как и раньше (полный извлечённый
        текст документов), но НЕ сохраняется в БД — только используется здесь,
        в момент вызова, чтобы построить ``search_tsv`` через
        ``func.to_tsvector('simple', ...)`` (та же postgres-функция, что раньше
        стояла в ``GENERATED``-выражении колонки — токенизация не меняется, только
        точка вычисления). Повторный вызов со ``status="error"`` (транзиентный сбой
        скачивания) НЕ затирает ``search_tsv``/``subject_snapshot``/``content_hash``
        предыдущего успешного результата — обновляются только явно переданные поля.

        ``attempts`` — счётчик подряд идущих ``error`` (сбрасывается в 0 при
        ``indexed``). Индексирующий воркер сам НЕ знает о политике retry/DLQ (простой
        контракт status/error_message) — решение «ещё раз в очередь через recovery
        vs Dead Letter Queue» принимается здесь: при ``status="error"`` очередной
        ``attempts`` сравнивается с ``max_attempts`` (``IndexingConfig.max_attempts``,
        передаётся вызывающим), и при достижении — persisted-статус переопределяется
        на ``"dead_letter"`` (recovery-проход её больше не трогает, видна на вкладке
        «Мониторинг» аналитику/devops).

        Возвращает False, если закупка с таким id не найдена (сервис не ставит
        задание индексации несуществующей закупке, но результат мог прийти после
        её удаления).
        """
        async with self._db.session() as session:
            procurement = await session.get(Procurement, procurement_id)
            if procurement is None:
                return False
            okpd2_normalized = normalize_okpd2_field(procurement.okpd2_codes)
            existing = await session.scalar(
                select(ProcurementSearchIndex.attempts).where(
                    ProcurementSearchIndex.procurement_id == procurement_id
                )
            )
            attempts = int(existing or 0)
            persisted_status = status
            if status == "error":
                attempts += 1
                if attempts >= max_attempts:
                    persisted_status = "dead_letter"
            else:
                attempts = 0

            insert_values: dict[str, Any] = {
                "procurement_id": procurement_id,
                "okpd2_normalized": okpd2_normalized,
                "status": persisted_status,
                "attempts": attempts,
                "content_hash": content_hash,
                "error_message": error_message,
            }
            update_values: dict[str, Any] = {
                "okpd2_normalized": okpd2_normalized,
                "status": persisted_status,
                "attempts": attempts,
                "error_message": error_message,
            }
            if document_text is not None:
                subject_snapshot = procurement.subject
                search_tsv_expr = func.to_tsvector(
                    "simple", f"{subject_snapshot or ''} {document_text}"
                )
                insert_values["subject_snapshot"] = subject_snapshot
                insert_values["search_tsv"] = search_tsv_expr
                update_values["subject_snapshot"] = subject_snapshot
                update_values["search_tsv"] = search_tsv_expr
            if content_hash is not None:
                update_values["content_hash"] = content_hash
            if status == "indexed":
                now = datetime.now(UTC)
                insert_values["indexed_at"] = now
                update_values["indexed_at"] = now

            stmt = (
                pg_insert(ProcurementSearchIndex)
                .values(**insert_values)
                .on_conflict_do_update(
                    index_elements=["procurement_id"],
                    set_=update_values,
                )
            )
            await session.execute(stmt)
            await session.commit()
        logger.info(
            "Результат индексации закупки %s сохранён (status=%s, attempts=%d)",
            procurement_id,
            persisted_status,
            attempts,
        )
        return True

    async def index_status_counts(self) -> dict[str, int]:
        """Наполнение фонового индекса (devops-мониторинг, вкладка «Мониторинг»).

        Точный breakdown только по ``procurement_search_index.status`` (то, что
        реально обработано воркером indexing_service) плюс общий счётчик
        ``procurements`` для контекста. «В очереди»/«ещё не поставлено» здесь
        намеренно не пересчитывается отдельным SQL по ОКПД2-префиксам — это
        дублировало бы уже решённый риск подстрокового совпадения (BR-10,
        ``any_okpd_code_covered_by_prefixes``) в сыром SQL; прокси для «сколько
        сейчас в работе» — глубина очереди ``index`` (``ScoringTransportClient.
        queue_status``).
        """
        async with self._db.session() as session:
            status_rows = (
                await session.execute(
                    select(ProcurementSearchIndex.status, func.count())
                    .select_from(ProcurementSearchIndex)
                    .group_by(ProcurementSearchIndex.status)
                )
            ).all()
            total_procurements = await session.scalar(select(func.count()).select_from(Procurement))
        counts: dict[str, int] = {str(status): int(count) for status, count in status_rows}
        counts["total_procurements"] = int(total_procurements or 0)
        return counts

    async def recent_index_errors(self, limit: int = 20) -> list[dict[str, Any]]:
        """Последние ошибки фоновой индексации (devops-мониторинг, вкладка «Мониторинг»).

        Только строки ``status='error'``, самые свежие по ``updated_at`` первыми —
        чтобы не лазить в БД/лог вручную ради текста конкретной ошибки.
        """
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(
                        ProcurementSearchIndex.procurement_id,
                        Procurement.number,
                        ProcurementSearchIndex.error_message,
                        ProcurementSearchIndex.updated_at,
                    )
                    .join(Procurement, Procurement.id == ProcurementSearchIndex.procurement_id)
                    .where(ProcurementSearchIndex.status == "error")
                    .order_by(ProcurementSearchIndex.updated_at.desc())
                    .limit(limit)
                )
            ).all()
        return [
            {
                "procurement_id": pid,
                "number": number,
                "error_message": error_message,
                "updated_at": updated_at.isoformat() if updated_at else None,
            }
            for pid, number, error_message, updated_at in rows
        ]

    async def retryable_index_errors(
        self, *, limit: int, updated_before: datetime
    ) -> list[dict[str, Any]]:
        """Сбойные записи индекса (``status='error'``), готовые к повтору (recovery-проход).

        ``updated_before`` — TTL-гейт (``IndexingConfig.retry_ttl_seconds`` от текущего
        момента): запись, обновлённая позже этой границы, уже недавно пыталась —
        рано ставить её в очередь ещё раз. ``dead_letter`` сюда не попадают —
        для них retry прекращён (см. ``save_index_result``).
        """
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(
                        ProcurementSearchIndex.procurement_id,
                        Procurement.update_date,
                        Procurement.publication_date,
                    )
                    .join(Procurement, Procurement.id == ProcurementSearchIndex.procurement_id)
                    .where(
                        ProcurementSearchIndex.status == "error",
                        ProcurementSearchIndex.updated_at < updated_before,
                    )
                    .order_by(ProcurementSearchIndex.updated_at.asc())
                    .limit(limit)
                )
            ).all()
        return [
            {
                "procurement_id": pid,
                "update_date": update_date,
                "publication_date": publication_date,
            }
            for pid, update_date, publication_date in rows
        ]

    async def mark_index_retry_queued(self, procurement_id: int, now: datetime) -> None:
        """Отмечает момент повторной постановки сбойной записи в очередь (recovery-проход).

        Без этой отметки ``retryable_index_errors`` увидела бы ту же запись снова
        уже на следующем цикле (``updated_at`` меняется только когда воркер реально
        обработает задание) и поставила бы её в очередь повторно ДО того, как
        первая попытка вообще успела выполниться — тот же приём, что
        ``mark_scoring_queued`` для стадии fit.
        """
        async with self._db.session() as session:
            await session.execute(
                update(ProcurementSearchIndex)
                .where(ProcurementSearchIndex.procurement_id == procurement_id)
                .values(updated_at=now)
            )
            await session.commit()

    async def dead_letter_index_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        """Dead Letter Queue фоновой индексации (devops-мониторинг, вкладка «Мониторинг»).

        ``status='dead_letter'`` — повторы исчерпаны (``IndexingConfig.max_attempts``),
        запись требует ручного вмешательства аналитика/devops (см. ``require_analyst_or_
        devops``): выяснить причину (обычно битый/недоступный файл площадки) и либо
        поставить вручную на повтор (``reset_index_entry_for_retry``), либо оставить
        как есть (закупка останется без индекса документов, но не блокирует ничего
        другого — ``status='dead_letter'`` не мешает основному сбору/скорингу).
        """
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(
                        ProcurementSearchIndex.procurement_id,
                        Procurement.number,
                        Procurement.subject,
                        ProcurementSearchIndex.attempts,
                        ProcurementSearchIndex.error_message,
                        ProcurementSearchIndex.updated_at,
                    )
                    .join(Procurement, Procurement.id == ProcurementSearchIndex.procurement_id)
                    .where(ProcurementSearchIndex.status == "dead_letter")
                    .order_by(ProcurementSearchIndex.updated_at.desc())
                    .limit(limit)
                )
            ).all()
        return [
            {
                "procurement_id": pid,
                "number": number,
                "subject": subject,
                "attempts": attempts,
                "error_message": error_message,
                "updated_at": updated_at.isoformat() if updated_at else None,
            }
            for pid, number, subject, attempts, error_message, updated_at in rows
        ]

    async def reset_index_entry_for_retry(self, procurement_id: int) -> bool:
        """Сбрасывает ``attempts``/``status`` записи индекса в ``pending`` (без enqueue).

        Только состояние в БД — снимает блокировку ``dead_letter``, чтобы запись
        не мешала статистике/фильтрам. Само задание индексации ставит В ОЧЕРЕДЬ
        вызывающий (``POST /api/devops/index-dead-letter/{id}/retry``, монитор.py)
        отдельным ``enqueue`` — ни обычный индексный обход (ставит задание один
        раз, при первом сохранении закупки), ни recovery-проход (смотрит только
        ``status='error'``) сами по себе повторно её не подхватят. Возвращает
        False, если записи с таким ``procurement_id`` нет вовсе (не обязательно в
        ``dead_letter`` — сброс безопасен из любого статуса, вызывающий сам
        проверяет уместность).
        """
        async with self._db.session() as session:
            cursor = cast(
                CursorResult[Any],
                await session.execute(
                    update(ProcurementSearchIndex)
                    .where(ProcurementSearchIndex.procurement_id == procurement_id)
                    .values(status="pending", attempts=0, error_message=None)
                ),
            )
            await session.commit()
        return (cursor.rowcount or 0) > 0
