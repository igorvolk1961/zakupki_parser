#!/usr/bin/env bash
# Запускает всё приложение одной командой (для работы вне контейнера):
#
#   1. инфраструктура — PostgreSQL + Redis (scripts/services_up.sh);
#   2. scoring_service — фоновый воркер Redis-очереди (в режиме заглушки,
#      пока LLM-пайплайн не отлажен: SCORE_USE_STUB/score_use_stub);
#   3. scoring_transport — gateway скоринга (ingest + возврат результата);
#   4. парсер — FastAPI (serve) на переднем плане.
#
# После запуска открыть http://localhost:8000/ и нажать «▶ Запустить».
# Остановка: Ctrl+C — завершает все фоновые сервисы.
#
# Порты можно переопределить: PORT_PARSER, PORT_TRANSPORT.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PORT_PARSER="${PORT_PARSER:-8000}"
PORT_TRANSPORT="${PORT_TRANSPORT:-8200}"

if ! command -v docker >/dev/null 2>&1; then
    echo "Ошибка: docker не установлен." >&2
    exit 1
fi

# --- Инфраструктура (PostgreSQL + Redis) ---------------------------------
"$SCRIPT_DIR/services_up.sh"

BGPIDS=()

cleanup() {
    echo
    echo "Останавливаю фоновые сервисы..."
    for pid in "${BGPIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait "${BGPIDS[@]}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- scoring_service (воркер, заглушка) ------------------------------------
echo "Запуск scoring_service (воркер, заглушка)..."
( cd "$ROOT_DIR/src/scoring_service" && SCORE_PARSER_API_URL="http://127.0.0.1:$PORT_PARSER" \
    uv run python -m scoring_service worker ) &
BGPIDS+=($!)

# --- scoring_transport ------------------------------------------------------
echo "Запуск scoring_transport на :$PORT_TRANSPORT..."
( cd "$ROOT_DIR/src/scoring_transport" \
    && TRANSPORT_PARSER_API_URL="http://127.0.0.1:$PORT_PARSER" \
    uv run python -m scoring_transport serve --host 127.0.0.1 --port "$PORT_TRANSPORT" ) &
BGPIDS+=($!)

# Ждём готовности транспорта, чтобы парсер при авто-пуше не терял задания.
echo "Ожидание готовности scoring_transport..."
for i in $(seq 1 30); do
    if curl -sf -m 1 "http://127.0.0.1:$PORT_TRANSPORT/health" >/dev/null 2>&1; then
        echo "scoring_transport готов."
        break
    fi
    if ! kill -0 "${BGPIDS[1]}" 2>/dev/null; then
        echo "scoring_transport завершился с ошибкой — прерываю." >&2
        exit 1
    fi
    sleep 1
done

# --- парсер (serve) на переднем плане --------------------------------------
parser_running() {
    curl -sf -m 1 "http://127.0.0.1:$PORT_PARSER/health" >/dev/null 2>&1
}

if parser_running; then
    echo "Парсер уже запущен на :$PORT_PARSER — конвейер скоринга будет работать с ним."
    wait
else
    echo "Запуск парсера (serve) на :$PORT_PARSER — откройте http://localhost:$PORT_PARSER/"
    cd "$ROOT_DIR"
    uv run zp --configs configs serve --host 0.0.0.0 --port "$PORT_PARSER"
fi
