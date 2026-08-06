"""CLI имитатора zakupki.mos.ru.

Запуск (пакет не добавлен в pyproject, чтобы не конфликтовать с другими агентами):

    PYTHONPATH=src uv run python -m zakupki_mos_simulator generate ...
    PYTHONPATH=src uv run python -m zakupki_mos_simulator serve --port 8010
    PYTHONPATH=src uv run python -m zakupki_mos_simulator validate ...
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import uvicorn

from zakupki_mos_simulator.data.dataset import (
    balance_report,
    load_dataset,
    save_dataset,
)
from zakupki_mos_simulator.llm.client import LLMClient
from zakupki_mos_simulator.llm.generate import generate_dataset
from zakupki_mos_simulator.llm.validate import validate_cli
from zakupki_mos_simulator.settings import Settings, load_settings

logger = logging.getLogger("zakupki_mos_simulator")


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zakupki_mos_simulator")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="сгенерировать тестовую выборку закупок")
    gen.add_argument("--competencies", type=Path, help="файл с компетенциями поставщика")
    gen.add_argument("--okpd2", type=str, default=None, help="коды ОКПД2 через запятую (62,63)")
    gen.add_argument("--per-category", type=int, default=None, help="закупок на категорию")
    gen.add_argument("--out", type=Path, default=None, help="путь к выходному JSON")
    gen.add_argument("--no-llm", action="store_true", help="детерминированный генератор без LLM")

    serve = sub.add_parser("serve", help="запустить веб-имитатор")
    serve.add_argument("--host", type=str, default=None)
    serve.add_argument("--port", type=int, default=None)

    val = sub.add_parser("validate", help="проверить точность скоринга по датасету")
    val.add_argument("--dataset", type=Path, default=None, help="путь к dataset.json")
    val.add_argument("--scores", type=Path, required=True, help="файл оценок (CSV/JSON)")
    val.add_argument("--threshold", type=float, default=0.0, help="порог «высоко-привлекательной»")

    return parser


def _cmd_generate(args: argparse.Namespace, settings: Settings) -> int:
    okpd2 = settings.default_okpd2_sections
    if args.okpd2:
        okpd2 = [c.strip() for c in args.okpd2.split(",") if c.strip()]
    competencies = args.competencies or settings.default_competencies_path
    per_category = args.per_category or settings.per_category
    out = args.out or settings.default_dataset_path

    client = None if args.no_llm else LLMClient(settings)
    dataset = generate_dataset(
        competencies_path=competencies,
        okpd2_sections=okpd2,
        per_category=per_category,
        use_llm=not args.no_llm,
        client=client,
    )
    path = save_dataset(dataset, out)
    report = balance_report(dataset)
    print(f"Датасет сохранён: {path}")
    print("Доли категорий:", json.dumps(report, ensure_ascii=False, indent=2))
    if report["violations"]:
        print("ВНИМАНИЕ (дисбаланс):")
        for v in report["violations"]:
            print(" -", v)
    return 0


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    host = args.host or settings.host
    port = args.port or settings.port
    from zakupki_mos_simulator.web.app import create_app

    app = create_app()
    print(f"Имитатор zakupki.mos.ru на http://{host}:{port} (для парсера: config_dom.url)")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def _cmd_validate(args: argparse.Namespace, settings: Settings) -> int:
    dataset_path = args.dataset or settings.default_dataset_path
    dataset = load_dataset(dataset_path)
    metrics = validate_cli(dataset, args.scores, args.threshold)
    print(json.dumps(metrics.to_dict(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = _arg_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.command == "generate":
        return _cmd_generate(args, settings)
    if args.command == "serve":
        return _cmd_serve(args, settings)
    if args.command == "validate":
        return _cmd_validate(args, settings)
    parser.error(f"неизвестная команда: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
