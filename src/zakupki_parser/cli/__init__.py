"""CLI-интерфейс парсера.

Команды:
- check-config   — проверить корректность YAML-конфигов
- run-once       — один проход по всем площадкам
- run-service    — периодический запуск по таймеру
- stop           — остановить запущенные процессы парсера
- seed-profile   — заполнить default-профиль пользователя словами/компетенциями из файла
- capture-fixture— сохранить HTML страниц (список/деталь) в tests/fixtures

Построение argparse вынесено в ``parser``, сводка конфигурации — в ``summary``,
исполнение команд — в ``commands``. Здесь — точка входа ``main`` и реэкспорт
для совместимости с прежним модулем ``cli.py``.
"""

from __future__ import annotations

import asyncio
import sys

from zakupki_parser.cli.commands import _run, _serve
from zakupki_parser.cli.parser import _build_parser
from zakupki_parser.cli.summary import _print_summary


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "stop":
        from zakupki_parser.stopper import stop_parser

        try:
            remaining = stop_parser(force=args.force)
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            sys.exit(1)
        if remaining:
            print(
                "Не удалось остановить процессы: " + ", ".join(str(pid) for pid in remaining),
                file=sys.stderr,
            )
            sys.exit(1)
        print("Парсер остановлен.")
        sys.exit(0)
    if args.command == "serve":
        code = _serve(args.configs, args.host, args.port)
        sys.exit(code)
    try:
        code = asyncio.run(_run(args.command, args.configs, args))
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        code = 1
    sys.exit(code)


__all__ = ["main", "_print_summary", "_build_parser"]


if __name__ == "__main__":
    main()
