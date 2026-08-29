#!/usr/bin/env bash
# Полный прогон перед пушем: линтеры/типы корня + тесты корня с покрытием +
# проверка всех подпроектов с их тестами.
#
# Покрытие считается для корневого пакета zakupki_parser (источник —
# [tool.coverage.run] в pyproject.toml). Для интеграционных тестов (БД)
# задайте ZAKUPKI_TEST_DSN (без него они будут пропущены).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "===== Корень: ruff check ====="
uv run ruff check src/zakupki_parser tests

echo "===== Корень: ruff format --check ====="
uv run ruff format --check src/zakupki_parser tests

echo "===== Корень: mypy ====="
uv run mypy

echo "===== Корень: pytest с покрытием (zakupki_parser) ====="
uv run pytest tests \
  --cov=zakupki_parser \
  --cov-report=term-missing

echo "===== Подпроекты: проверка + тесты ====="
"$REPO_ROOT/scripts/check_subprojects.sh" test

echo "OK: полный прогон завершён; покрытие — в отчёте выше"
