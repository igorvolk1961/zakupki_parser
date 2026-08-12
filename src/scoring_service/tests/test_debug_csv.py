"""Тесты маппинга CSV-выгрузки в record и отладочной таблицы."""

from __future__ import annotations

from pathlib import Path

from scoring_service.debug_csv import load_records, render_table, row_to_record, write_report
from scoring_service.schemas import ScoringOutput


def test_row_to_record_maps_known_fields() -> None:
    row = {
        "id": "178",
        "subject": "Настройка ИИ-ассистента",
        "customer": "Администрация",
        "law": "44-ФЗ",
        "nmck": "1000000.5",
        "okpd2_codes": "62.02.30.000",
        "kpgz_codes": "62.20",
        "deadline": "2026-06-02T10:50:00+00:00",
    }
    record = row_to_record(row)
    assert record["subject"] == "Настройка ИИ-ассистента"
    assert record["law"] == "44-ФЗ"
    assert record["okpd2_codes"] == "62.02.30.000"
    assert record["kpgz_codes"] == "62.20"
    assert record["nmck"] == 1000000.5


def test_row_to_record_ignores_empty() -> None:
    record = row_to_record({"id": "1", "subject": "x", "nmck": "", "law": ""})
    assert "nmck" not in record
    assert "law" not in record
    assert record["subject"] == "x"


def test_load_records_from_csv(tmp_path: Path) -> None:
    csv_file = tmp_path / "procurements.csv"
    csv_file.write_text(
        "id,subject,nmck,okpd2_codes\n"
        "178,Настройка ИИ-ассистента,150,62.02.30.000\n"
        "179,Сопровождение системы,,\n",
        encoding="utf-8",
    )
    records = load_records(csv_file)
    assert len(records) == 2
    pid, rec = records[0]
    assert pid == 178
    assert rec["subject"] == "Настройка ИИ-ассистента"
    assert rec["nmck"] == 150.0
    assert "okpd2_codes" in rec
    # строка без nmck — поле отсутствует
    assert "nmck" not in records[1][1]


def test_render_table_and_report(tmp_path: Path) -> None:
    from scoring_service.schemas import FitResult, JudgeResult, ReasoningSteps

    reasoning = ReasoningSteps(
        procurement_essence="a",
        competencies_essence="b",
        relevant_competencies="c",
        term_overlap_mismatch_check="d",
        synonym_semantic_bridge="e",
        uncovered_scope="f",
        fit_score_rationale="g",
    )
    fit = FitResult(reasoning=reasoning, fit_score=8.0)
    judge = JudgeResult(critics="ok", verdict="accept", final_fit_score=8.0)
    out = ScoringOutput(
        procurement_id=178,
        description="Настройка ИИ-ассистента Bquadro",
        fit=fit,
        judge=judge,
        final_fit_score=8.0,
        fit_multiplier=0.8,
        p_win=1.0,
        margin=150.0,
        score=120.0,
    )
    results = [(178, out)]

    table = render_table(results)
    assert "178" in table
    assert "accept" in table
    assert "8.00" in table

    report = tmp_path / "report.json"
    write_report(report, results)
    import json

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload[0]["procurement_id"] == 178
    assert payload[0]["score"] == 120.0
