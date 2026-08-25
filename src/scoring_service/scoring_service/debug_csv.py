"""Отладка пайплайна скоринга на выгрузке БД (CSV).

Команда ``score-csv`` читает CSV-выгрузку закупок (по умолчанию
``data/export/procurements.csv`` в корне репозитория), сопоставляет строки
с record-словарями и прогоняет полный LLM-пайплайн (fit/judge/refine) по каждой
записи. По умолчанию работает реальный LLM-пайплайн (stub выключен); флаг ``--stub``
включает заглушку для сверки. LangFuse подключается автоматически через
``build_scorer`` при заданных ``LANGFUSE_*``.
"""

from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path
from typing import Any

from scoring_service.profile import ProfileTexts
from scoring_service.schemas import ScoringOutput
from scoring_service.scoring import build_scorer
from scoring_service.settings import Settings

# Поля карточки, попадающие в описание (должны совпадать с
# pipeline.description._DETAIL_FIELDS).
_RECORD_FIELDS = (
    "subject",
    "customer",
    "okpd2_codes",
    "kpgz_codes",
    "nmck",
    "law",
    "deadline",
    "execution_term",
)


def row_to_record(row: dict[str, str]) -> dict[str, Any]:
    """Сопоставить строку CSV-выгрузки с record-словарём для пайплайна."""
    record: dict[str, Any] = {}
    for field in _RECORD_FIELDS:
        value = (row.get(field) or "").strip()
        if not value:
            continue
        if field == "nmck":
            try:
                record[field] = float(value)
            except ValueError:
                record[field] = value
        else:
            record[field] = value
    return record


def load_records(csv_path: Path) -> list[tuple[int | None, dict[str, Any]]]:
    """Загрузить закупки из CSV: список ``(procurement_id, record)``."""
    records: list[tuple[int | None, dict[str, Any]]] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rec = row_to_record(row)
            raw_id = (row.get("id") or "").strip()
            procurement_id = int(raw_id) if raw_id.isdigit() else None
            records.append((procurement_id, rec))
    return records


def run_debug(
    settings: Settings,
    csv_path: Path,
    competencies: str | ProfileTexts,
    limit: int = 0,
    stub: bool = False,
) -> list[tuple[int | None, ScoringOutput]]:
    """Прогнать пайплайн по закупкам из CSV."""
    effective = settings.model_copy(update={"score_use_stub": stub})
    scorer = build_scorer(effective)
    records = load_records(csv_path)
    if limit > 0:
        records = records[:limit]
    # Один run_id на весь прогон CSV: все закупки — в одной LangFuse-сессии.
    run_id = uuid.uuid4().hex
    results: list[tuple[int | None, ScoringOutput]] = []
    for procurement_id, record in records:
        result = scorer.score(record, competencies, procurement_id, run_id=run_id)
        results.append((procurement_id, result))
    return results


def render_table(results: list[tuple[int | None, ScoringOutput]]) -> str:
    """Читаемая таблица результатов отладки."""
    header = ("id", "verdict", "fit", "final_fit", "score", "subject")
    rows: list[tuple[str, ...]] = []
    for procurement_id, r in results:
        subject = r.description.splitlines()[0] if r.description else ""
        rows.append(
            (
                str(procurement_id),
                r.judge.verdict,
                f"{r.fit.fit_score:.2f}",
                f"{r.final_fit_score:.2f}",
                f"{r.score:.2f}",
                subject[:60],
            )
        )
    widths = [max(len(header[i]), *(len(row[i]) for row in rows)) for i in range(len(header))]
    lines = [" | ".join(h.ljust(widths[i]) for i, h in enumerate(header))]
    lines.append("-+-".join("-" * w for w in widths))
    for row in rows:
        lines.append(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def write_report(path: Path, results: list[tuple[int | None, ScoringOutput]]) -> None:
    """Записать полный JSON-отчёт (все поля ScoringOutput) по каждой закупке."""
    payload = [{"procurement_id": pid, **r.model_dump()} for pid, r in results]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
