"""Поисковый индекс закупок (описание+документы, фоновая индексация по ОКПД2).

Результат воркера ``indexing_service`` (стадия ``index``, вне каскада Fit/P(win)/
Margin) возвращается через ``scoring_transport`` -> ``POST
/api/procurements/{id}/index-result`` -> ``save_index_result`` ниже.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
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
    ) -> bool:
        """Upsert результата фоновой индексации закупки (find-or-create по procurement_id).

        ``okpd2_normalized`` вычисляется здесь же из актуального ``procurements.
        okpd2_codes`` (не принимается от indexing_service — сервис не имеет доступа
        к БД, но не должен и держать копию логики нормализации). Повторный вызов
        со ``status="error"`` (транзиентный сбой скачивания) НЕ затирает
        ``document_text``/``content_hash`` предыдущего успешного результата —
        обновляются только явно переданные поля. Возвращает False, если закупка
        с таким id не найдена (сервис не ставит задание индексации несуществующей
        закупке, но результат мог прийти после её удаления).
        """
        async with self._db.session() as session:
            procurement = await session.get(Procurement, procurement_id)
            if procurement is None:
                return False
            okpd2_normalized = normalize_okpd2_field(procurement.okpd2_codes)

            insert_values: dict[str, Any] = {
                "procurement_id": procurement_id,
                "okpd2_normalized": okpd2_normalized,
                "status": status,
                "document_text": document_text,
                "content_hash": content_hash,
                "error_message": error_message,
            }
            update_values: dict[str, Any] = {
                "okpd2_normalized": okpd2_normalized,
                "status": status,
                "error_message": error_message,
            }
            if document_text is not None:
                update_values["document_text"] = document_text
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
        logger.info("Результат индексации закупки %s сохранён (status=%s)", procurement_id, status)
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
