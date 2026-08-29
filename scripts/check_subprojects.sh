#!/usr/bin/env bash
# Проверка подпроектов (src/*/): ruff, ruff-format, mypy (+ pytest в режиме test).
#
# Режимы:
#   lint — без тестов (для pre-commit);
#   test — с тестами (для CI / перед пушем).
#
# Возвращает ненулевой код при первой же ошибке в любом подпроекте.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-test}"

SUBPROJECTS=(
  src/scoring_common
  src/scoring_service
  src/scoring_transport
  src/pwin_service
  src/margin_service
  src/analysis_service
)

if [[ "$MODE" != "lint" && "$MODE" != "test" ]]; then
  echo "Использование: $0 [lint|test]" >&2
  exit 2
fi

for dir in "${SUBPROJECTS[@]}"; do
  if [[ ! -d "$REPO_ROOT/$dir" ]]; then
    echo "Пропуск: нет $dir" >&2
    continue
  fi
  echo "===== $dir ($MODE) ====="
  (
    cd "$REPO_ROOT/$dir"
    uv sync --frozen
    echo "--- ruff check ---"
    uv run ruff check .
    echo "--- ruff format --check ---"
    uv run ruff format --check .
    echo "--- mypy ---"
    uv run mypy .
    if [[ "$MODE" = "test" ]]; then
      echo "--- pytest ---"
      uv run pytest
    fi
  )
done

echo "OK: все подпроекты проверены (mode=$MODE)"
