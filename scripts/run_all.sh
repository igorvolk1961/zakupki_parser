#!/usr/bin/env bash
# Запускает фоновый стек приложения одной командой (для работы вне контейнера):
#
#   1. инфраструктура — PostgreSQL (scripts/db_up.sh) + Redis;
#   2. LangFuse (docker, профиль langfuse) — трассировка LLM скоринга. По умолчанию
#      поднимается; отключить: SKIP_LANGFUSE=1 scripts/run_all.sh;
#   3. scoring_service — фоновый воркер Redis-очереди (в режиме заглушки,
#      пока LLM-пайплайн не отлажен: SCORE_USE_STUB/score_use_stub);
#   4. scoring_transport — gateway скоринга (ingest + возврат результата);
#
# Парсер запускается отдельной командой (не внутри этого скрипта):
#   uv run zp --configs configs serve --host 0.0.0.0 --port <PORT_PARSER>
#
# Фоновые сервисы остаются жить, пока работает скрипт; Ctrl+C — останавливает их.
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

# --- Инфраструктура ---------------------------------------------------------
# PostgreSQL — через db_up.sh (создаёт контейнер + применяет миграции Liquibase).
"$SCRIPT_DIR/db_up.sh"

# Redis — отдельный контейнер (идемпотентно: создаёт или запускает существующий).
REDIS_CONTAINER="zakupki_redis"
REDIS_IMAGE="redis:7-alpine"
REDIS_PORT="6379"
REDIS_VOLUME="zakupki_redis_data"

if docker ps -a --format '{{.Names}}' | grep -qx -- "$REDIS_CONTAINER"; then
    echo "Контейнер $REDIS_CONTAINER уже существует."
    if ! docker ps --format '{{.Names}}' | grep -qx -- "$REDIS_CONTAINER"; then
        echo "Запуск $REDIS_CONTAINER..."
        docker start "$REDIS_CONTAINER"
    fi
else
    echo "Создаю контейнер $REDIS_CONTAINER..."
    docker run -d --name "$REDIS_CONTAINER" \
        -p "$REDIS_PORT:6379" \
        -v "$REDIS_VOLUME:/data" \
        "$REDIS_IMAGE"
fi
echo "Ожидание готовности Redis..."
for i in $(seq 1 30); do
    if docker exec "$REDIS_CONTAINER" redis-cli ping 2>/dev/null | grep -q PONG; then
        echo "Redis готов ($REDIS_CONTAINER)."
        break
    fi
    sleep 2
done

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

# --- LangFuse (docker, трассировка LLM скоринга) ------------------------------
# По умолчанию поднимается; отключить: SKIP_LANGFUSE=1 scripts/run_all.sh
if [[ "${SKIP_LANGFUSE:-0}" != "1" ]]; then
    echo "Запуск LangFuse (docker, профиль langfuse)..."
    ( cd "$ROOT_DIR" && COMPOSE_PROFILES=langfuse \
        docker compose -f docker/docker-compose.yml up -d langfuse-web ) || \
        echo "Внимание: LangFuse не поднялся — проверьте docker/.env." >&2
    echo "LangFuse UI: http://localhost:3000"
else
    echo "LangFuse пропущен (SKIP_LANGFUSE=1)."
fi

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
ready=0
for i in $(seq 1 30); do
    if curl -sf -m 1 "http://127.0.0.1:$PORT_TRANSPORT/health" >/dev/null 2>&1; then
        echo "scoring_transport готов."
        ready=1
        break
    fi
    if ! kill -0 "${BGPIDS[1]}" 2>/dev/null; then
        echo "scoring_transport завершился с ошибкой — прерываю." >&2
        exit 1
    fi
    sleep 1
done
if [[ "$ready" != 1 ]]; then
    echo "Внимание: scoring_transport не ответил на /health за 30 с — проверьте его лог." >&2
fi

echo "Фоновый стек поднят. Запустите парсер отдельно:"
echo "  uv run zp --configs configs serve --host 0.0.0.0 --port $PORT_PARSER"
echo "Ctrl+C останавливает фоновые сервисы."
wait
