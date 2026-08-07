#!/usr/bin/env bash
# Поднимает все контейнеры инфраструктуры, необходимые для работы приложения
# вне контейнера (запуск через `uv run`): PostgreSQL + Redis.
#
# Redis нужен конвейеру внешнего скоринга (scoring_transport + scoring_service),
# PostgreSQL — парсеру (миграции Liquibase применяет db_up.sh).
#
# Логика (для каждого контейнера): если контейнер уже существует — просто
# запускает его (данные сохраняются в volume); если нет — создаёт новый и ждёт
# готовности. Идемпотентно.
#
# Использование:
#   scripts/services_up.sh              # поднять PostgreSQL + Redis
#   scripts/services_up.sh --status     # статус всех контейнеров (без запуска)
#   scripts/services_up.sh --redis      # только Redis
#   scripts/services_up.sh --db         # только PostgreSQL (как db_up.sh)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REDIS_CONTAINER="zakupki_redis"
REDIS_IMAGE="redis:7-alpine"
REDIS_PORT="6379"
REDIS_VOLUME="zakupki_redis_data"
RETRY_LIMIT=30
RETRY_SLEEP=2

MODE="all"
for arg in "$@"; do
    case "$arg" in
        --status) MODE="status" ;;
        --redis) MODE="redis" ;;
        --db) MODE="db" ;;
        -h|--help)
            sed -n '2,9p' "$0"
            exit 0
            ;;
        *)
            echo "Неизвестный аргумент: $arg" >&2
            exit 2
            ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "Ошибка: docker не установлен." >&2
    exit 1
fi

redis_exists() {
    docker ps -a --format '{{.Names}}' | grep -qx -- "$REDIS_CONTAINER"
}

redis_running() {
    docker ps --format '{{.Names}}' | grep -qx -- "$REDIS_CONTAINER"
}

redis_up() {
    if redis_exists; then
        echo "Контейнер $REDIS_CONTAINER уже существует."
        if ! redis_running; then
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
    for i in $(seq 1 "$RETRY_LIMIT"); do
        if docker exec "$REDIS_CONTAINER" redis-cli ping 2>/dev/null | grep -q PONG; then
            echo "Redis готов ($REDIS_CONTAINER)."
            return 0
        fi
        sleep "$RETRY_SLEEP"
    done
    echo "Ошибка: Redis не поднялся за $((RETRY_LIMIT * RETRY_SLEEP)) с." >&2
    return 1
}

redis_status() {
    docker ps -a --filter "name=^/${REDIS_CONTAINER}$" --format '{{.Names}}: {{.Status}}'
}

case "$MODE" in
    status)
        "$SCRIPT_DIR/db_up.sh" --status || true
        redis_status
        ;;
    redis)
        redis_up
        ;;
    db)
        "$SCRIPT_DIR/db_up.sh"
        ;;
    all)
        redis_up
        "$SCRIPT_DIR/db_up.sh"
        ;;
esac
