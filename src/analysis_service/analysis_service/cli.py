"""CLI analysis_service.

Subcommands:
  worker — фоновый воркер Redis-очереди (analysis:jobs → analysis:results);
  analyze — разовый RAG-анализ по карточке (JSON) для отладки.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from analysis_service.settings import Settings, get_settings

_SERVICE_DIR = Path(__file__).resolve().parents[1]


def _logging_setup() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _cmd_worker(settings: Settings) -> int:
    from analysis_service.worker import run_worker

    await run_worker(settings)
    return 0


async def _cmd_analyze(settings: Settings, card_path: Path) -> int:
    from analysis_service.llm import LlmClient
    from analysis_service.pipeline.rag import RagAnalyzer
    from scoring_common.embeddings import EmbeddingClient

    record = json.loads(card_path.read_text(encoding="utf-8"))
    # Пользовательские вопросы не передаём: обязательные проверки (опыт 2571,
    # реестр Минпромторга, лицензии/СРО) выполняются автоматически.
    questions: list[dict[str, str]] = []
    profile_facts: dict[str, list[str]] = {"license_codes": [], "experience_codes": []}
    embedder = EmbeddingClient(
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        timeout=settings.embedding_timeout,
    )
    llm = LlmClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        timeout=settings.llm_request_timeout,
    )
    report = await RagAnalyzer(settings, embedder, llm).analyze(record, questions, profile_facts)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    # Загружаем .env сервиса в окружение: pydantic-settings читает свои поля сам,
    # а os.environ-читатели (scoring_common.langfuse — LANGFUSE_*) получают ключи
    # и при прямом запуске (вне run_all.sh).
    load_dotenv(_SERVICE_DIR / ".env")
    parser = argparse.ArgumentParser(prog="analysis_service")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("worker", help="фоновый воркер очереди")
    analyze = sub.add_parser("analyze", help="разовый анализ по карточке (JSON)")
    analyze.add_argument("card", type=Path, help="путь к JSON-карточке закупки")
    args = parser.parse_args(argv)

    _logging_setup()
    settings = get_settings()
    if args.command == "worker":
        return asyncio.run(_cmd_worker(settings))
    return asyncio.run(_cmd_analyze(settings, args.card))


if __name__ == "__main__":
    sys.exit(main())
