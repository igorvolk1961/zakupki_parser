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
import sys
import uuid
from pathlib import Path

from scoring_common.logging import setup_logging
from scoring_service.profile import ProfileTexts
from scoring_service.settings import Settings, get_settings


def _profile_texts(path: Path | None, settings: Settings) -> ProfileTexts:
    """Рендер профиля (llm/embedding): из файла (YAML/JSON/markdown) или из настроек."""
    from scoring_service.profile import (
        load_profile,
        render_profile,
        render_profile_embedding,
    )

    if path is None:
        return settings.profile_texts()
    profile = load_profile(path)
    return ProfileTexts(
        llm=render_profile(profile),
        embedding=render_profile_embedding(profile),
    )


async def _cmd_worker(settings: Settings) -> int:
    from scoring_service.worker import run_worker

    await run_worker(settings)
    return 0


def _cmd_score(settings: Settings, card: Path, competencies: Path | None) -> int:
    from scoring_service.llm_factory import flush_langfuse
    from scoring_service.scoring import build_scorer

    record = json.loads(card.read_text(encoding="utf-8"))
    comp = _profile_texts(competencies, settings)
    scorer = build_scorer(settings)
    result = scorer.score(record, comp, record.get("id"), run_id=uuid.uuid4().hex)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    flush_langfuse()
    return 0


def _cmd_evaluate(
    settings: Settings,
    dataset: Path,
    out: Path | None,
    competencies: Path | None,
    tolerance: float,
    accept_threshold: float,
    precision_k: int | None,
    repeat: int,
    compare: Path | None,
    max_mae: float,
    max_rmse: float,
    max_acc: float,
    min_spearman: float,
) -> int:
    from scoring_service.eval.evaluate import (
        _dump_comparison,
        _dump_report,
        evaluate_cli,
    )
    from scoring_service.llm_factory import flush_langfuse

    comp = _profile_texts(competencies, settings)
    report, comparison = evaluate_cli(
        settings,
        dataset,
        out,
        comp,
        tolerance,
        accept_threshold=accept_threshold,
        precision_k=precision_k,
        repeat=repeat,
        compare=compare,
        max_mae=max_mae,
        max_rmse=max_rmse,
        max_acc=max_acc,
        min_spearman=min_spearman,
    )
    print(_dump_report(report))
    flush_langfuse()
    if comparison is not None:
        print("--- сравнение с baseline ---")
        print(_dump_comparison(comparison))
        return 0 if comparison.passed else 1
    return 0


def _cmd_score_csv(
    settings: Settings,
    csv_path: Path,
    competencies: Path | None,
    limit: int,
    out: Path | None,
) -> int:
    from scoring_service.debug_csv import render_table, run_debug, write_report
    from scoring_service.llm_factory import flush_langfuse

    comp = _profile_texts(competencies, settings)
    results = run_debug(settings, csv_path, comp, limit=limit)
    if out is not None:
        write_report(out, results)
    print(render_table(results))
    flush_langfuse()
    return 0


def _cmd_serve(settings: Settings, host: str, port: int) -> int:
    import uvicorn

    from scoring_service.web.app import create_app

    # Авторизация обязательна: сервис не стартует без токена (SCORE_AUTH_TOKEN).
    if not settings.auth_token:
        raise SystemExit(
            "Ошибка: авторизация обязательна — задайте SCORE_AUTH_TOKEN "
            "(иначе web-эндпоинт /score не защищён)"
        )
    app = create_app(settings)
    uvicorn.run(app, host=host, port=port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scoring-service")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("worker", help="запустить фоновый воркер Redis-очереди")

    p_score = sub.add_parser("score", help="разовый скоринг карточки")
    p_score.add_argument("card", type=Path, help="путь к JSON-карточке закупки")
    p_score.add_argument(
        "--competencies",
        type=Path,
        default=None,
        help="профиль поставщика: YAML/JSON (структурированный) или markdown (legacy)",
    )

    p_eval = sub.add_parser("evaluate", help="оценка точности на тестовом наборе")
    p_eval.add_argument("--dataset", type=Path, required=True)
    p_eval.add_argument("--out", type=Path, default=None)
    p_eval.add_argument(
        "--competencies",
        type=Path,
        default=None,
        help="профиль поставщика: YAML/JSON (структурированный) или markdown (legacy)",
    )
    p_eval.add_argument("--tolerance", type=float, default=1.0)
    p_eval.add_argument(
        "--accept-threshold", type=float, default=5.0, help="порог для бинарной метки"
    )
    p_eval.add_argument(
        "--precision-k", type=int, default=None, help="K для precision@K (по умолчанию не считаем)"
    )
    p_eval.add_argument(
        "--repeat",
        type=int,
        default=1,
        help=(
            "прогонов каждого примера: при >1 метрики усредняются по повторам (mean±std), "
            "дорого, но устойчиво к стохастичности модели"
        ),
    )
    p_eval.add_argument(
        "--compare",
        type=Path,
        default=None,
        help="JSON-отчёт baseline; сравнить и вернуть ненулевой код при деградации",
    )
    p_eval.add_argument("--max-mae-reg", type=float, default=0.3)
    p_eval.add_argument("--max-rmse-reg", type=float, default=0.4)
    p_eval.add_argument("--max-acc-reg", type=float, default=0.03)
    p_eval.add_argument("--min-spearman-reg", type=float, default=0.02)

    p_csv = sub.add_parser("score-csv", help="отладка пайплайна на выгрузке БД (CSV)")
    p_csv.add_argument(
        "--csv",
        type=Path,
        default=Path("../../data/export/procurements.csv"),
        help="CSV-выгрузка закупок (по умолчанию — корень репозитория)",
    )
    p_csv.add_argument(
        "--competencies",
        type=Path,
        default=None,
        help="профиль поставщика: YAML/JSON (структурированный) или markdown (legacy)",
    )
    p_csv.add_argument("--limit", type=int, default=0, help="0 = все записи")
    p_csv.add_argument("--out", type=Path, default=None, help="JSON-отчёт")

    p_serve = sub.add_parser("serve", help="запустить FastAPI")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8100)

    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    setup_logging(settings.logging)
    args = build_parser().parse_args(argv)

    if args.command == "worker":
        return asyncio.run(_cmd_worker(settings))
    if args.command == "score":
        return _cmd_score(settings, args.card, args.competencies)
    if args.command == "evaluate":
        return _cmd_evaluate(
            settings,
            args.dataset,
            args.out,
            args.competencies,
            args.tolerance,
            args.accept_threshold,
            args.precision_k,
            args.repeat,
            args.compare,
            args.max_mae_reg,
            args.max_rmse_reg,
            args.max_acc_reg,
            args.min_spearman_reg,
        )
    if args.command == "score-csv":
        return _cmd_score_csv(settings, args.csv, args.competencies, args.limit, args.out)
    if args.command == "serve":
        return _cmd_serve(settings, args.host, args.port)
    parser = build_parser()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
