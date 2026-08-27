"""Построение argparse-парсера CLI-команд парсера."""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_CONFIGS_DIR = Path("configs")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zp")
    parser.add_argument(
        "--configs",
        default=str(DEFAULT_CONFIGS_DIR),
        help="Каталог с YAML-конфигами (по умолчанию: configs)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config", help="Проверить конфигурацию")
    sub.add_parser("run-once", help="Один проход по всем площадкам")
    sub.add_parser("run-service", help="Периодический запуск по таймеру")

    stop = sub.add_parser("stop", help="Остановить запущенные процессы парсера")
    stop.add_argument(
        "--force", action="store_true", help="убить сразу (SIGKILL) без мягкого закрытия"
    )

    serve = sub.add_parser("serve", help="Запустить FastAPI-сервис (API)")
    serve.add_argument("--host", default="0.0.0.0", help="адрес (по умолчанию 0.0.0.0)")
    serve.add_argument("--port", default=8000, type=int, help="порт (по умолчанию 8000)")

    cap = sub.add_parser("capture-fixture", help="Сохранение HTML-фикстур")
    cap.add_argument("--platform", default="zakupki_mos", help="platform_id из config_dom")
    cap.add_argument("--out", default="tests/fixtures", help="каталог вывода")

    seed = sub.add_parser("seed-profile", help="Заполнить default-профиль пользователя из файла")
    seed.add_argument("--user", default="admin", help="логин пользователя (по умолчанию: admin)")
    seed.add_argument(
        "--file",
        default="docs/references/bbk-it-profile.md",
        help="файл с секциями **keywords**/**exclussion_words**/**competencies**",
    )
    return parser
