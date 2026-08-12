"""CLI сервиса скоринга.

Subcommands:
  worker    — фоновый воркер: consume Redis-очереди, скорит, публикует результаты;
  score     — разовый скоринг по файлу карточки (JSON) + компетенциям;
  score-csv — отладка пайплайна на выгрузке БД (CSV): прогон по всем закупкам;
  evaluate  — прогон fit-пайплайна по тестовому набору, расчёт метрик;
  serve     — запуск FastAPI (uvicorn) с /health и /score.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

from scoring_service.settings import Settings, get_settings


def _logging_setup() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _cmd_worker(settings: Settings) -> int:
    from scoring_service.worker import run_worker

    await run_worker(settings)
    return 0


def _cmd_score(settings: Settings, card: Path, competencies: Path | None) -> int:
    from scoring_service.scoring import build_scorer

    record = json.loads(card.read_text(encoding="utf-8"))
    comp = competencies.read_text(encoding="utf-8") if competencies else settings.competencies()
    scorer = build_scorer(settings)
    result = scorer.score(record, comp, record.get("id"), run_id=uuid.uuid4().hex)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    return 0


def _cmd_evaluate(
    settings: Settings,
    dataset: Path,
    out: Path | None,
    competencies: Path | None,
    tolerance: float,
) -> int:
    from scoring_service.eval.evaluate import evaluate_cli

    comp = competencies.read_text(encoding="utf-8") if competencies else None
    metrics = evaluate_cli(settings, dataset, out, comp, tolerance)
    print(json.dumps(metrics.model_dump(), ensure_ascii=False, indent=2))
    return 0


def _cmd_score_csv(
    settings: Settings,
    csv_path: Path,
    competencies: Path | None,
    limit: int,
    stub: bool,
    out: Path | None,
) -> int:
    from scoring_service.debug_csv import render_table, run_debug, write_report

    comp = competencies.read_text(encoding="utf-8") if competencies else settings.competencies()
    results = run_debug(settings, csv_path, comp, limit=limit, stub=stub)
    if out is not None:
        write_report(out, results)
    print(render_table(results))
    return 0


def _cmd_serve(settings: Settings, host: str, port: int) -> int:
    import uvicorn

    from scoring_service.web.app import create_app

    app = create_app(settings)
    uvicorn.run(app, host=host, port=port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scoring-service")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("worker", help="запустить фоновый воркер Redis-очереди")

    p_score = sub.add_parser("score", help="разовый скоринг карточки")
    p_score.add_argument("card", type=Path, help="путь к JSON-карточке закупки")
    p_score.add_argument("--competencies", type=Path, default=None)

    p_eval = sub.add_parser("evaluate", help="оценка точности на тестовом наборе")
    p_eval.add_argument("--dataset", type=Path, required=True)
    p_eval.add_argument("--out", type=Path, default=None)
    p_eval.add_argument("--competencies", type=Path, default=None)
    p_eval.add_argument("--tolerance", type=float, default=1.0)

    p_csv = sub.add_parser("score-csv", help="отладка пайплайна на выгрузке БД (CSV)")
    p_csv.add_argument(
        "--csv",
        type=Path,
        default=Path("../../data/export/procurements.csv"),
        help="CSV-выгрузка закупок (по умолчанию — корень репозитория)",
    )
    p_csv.add_argument("--competencies", type=Path, default=None)
    p_csv.add_argument("--limit", type=int, default=0, help="0 = все записи")
    p_csv.add_argument("--stub", action="store_true", help="использовать заглушку")
    p_csv.add_argument("--out", type=Path, default=None, help="JSON-отчёт")

    p_serve = sub.add_parser("serve", help="запустить FastAPI")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8100)

    return parser


def main(argv: list[str] | None = None) -> int:
    _logging_setup()
    args = build_parser().parse_args(argv)
    settings = get_settings()

    if args.command == "worker":
        return asyncio.run(_cmd_worker(settings))
    if args.command == "score":
        return _cmd_score(settings, args.card, args.competencies)
    if args.command == "evaluate":
        return _cmd_evaluate(settings, args.dataset, args.out, args.competencies, args.tolerance)
    if args.command == "score-csv":
        return _cmd_score_csv(
            settings, args.csv, args.competencies, args.limit, args.stub, args.out
        )
    if args.command == "serve":
        return _cmd_serve(settings, args.host, args.port)
    parser = build_parser()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
