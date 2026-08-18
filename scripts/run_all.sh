#!/usr/bin/env bash
# Запускает фоновый стек приложения одной командой (для работы вне контейнера):
#
#   1. инфраструктура — PostgreSQL (scripts/db_up.sh) + Redis;
#   2. LangFuse (docker, профиль langfuse) — трассировка LLM скоринга. По умолчанию
#      поднимается; отключить: SKIP_LANGFUSE=1 scripts/run_all.sh;
#   3. scoring_service — фоновый воркер Redis-очереди стадии Fit (LLM-пайплайн);
#   4. pwin_service — воркер стадии P(win) (в режиме заглушки: P(win) = константа,
#      пока модель коэффициентов не отлажена — PWIN_USE_STUB);
#   5. margin_service — воркер стадии Margin (Margin = НМЦК × margin_rate);
#   6. scoring_transport — gateway скоринга (ingest + возврат результата);
#
# Парсер запускается отдельной командой (не внутри этого скрипта):
#   uv run zp --configs configs serve --host 0.0.0.0 --port <PORT_PARSER>
#
# Фоновые сервисы остаются жить, пока работает скрипт; Ctrl+C — останавливает их.
# Порты можно переопределить: PORT_PARSER, PORT_TRANSPORT.
#
# Команды:
#   scripts/run_all.sh            # то же, что up (старт; предварительно чистит зависшие)
#   scripts/run_all.sh stop       # аккуратно остановить: скор.-сервисы + LangFuse + Redis + PostgreSQL
#   scripts/run_all.sh start      # то же, что up

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PORT_PARSER="${PORT_PARSER:-8000}"
PORT_TRANSPORT="${PORT_TRANSPORT:-8200}"

CMD="${1:-up}"
PID_FILE="$ROOT_DIR/.run_all.pids"
REDIS_CONTAINER="zakupki_redis"
DB_CONTAINER="zakupki_db"
COMPOSE_FILE="$ROOT_DIR/docker/docker-compose.yml"

# --- Остановка фоновых сервисов скоринга (transport + worker) ------------------
# Ловим живые процессы по командам и PID-файлу — это закрывает и осиротевшие
# процессы от ранее убитого/закрытого терминала (иначе порт 8200 остаётся занят).
scoring_process_pids() {
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -f "scoring_transport serve" 2>/dev/null
        pgrep -f "scoring_service worker" 2>/dev/null
        pgrep -f "pwin_service worker" 2>/dev/null
        pgrep -f "margin_service worker" 2>/dev/null
    else
        # Windows (Git Bash): pgrep отсутствует — ищем python-процессы по
        # командной строке через PowerShell (скоринг-транспорт/воркеры).
        powershell -NoProfile -Command \
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'scoring_(transport serve|service worker)|pwin_service worker|margin_service worker' } | ForEach-Object { \$_.ProcessId }" 2>/dev/null
    fi
}

port_in_use() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | grep -qE ":$port\b"
    else
        # Windows: ss нет — используем netstat.
        netstat -ano 2>/dev/null | grep -qE ":$port[[:space:]].*LISTENING"
    fi
}

stop_services() {
    local pids=""
    if [[ -f "$PID_FILE" ]]; then
        pids="$(cat "$PID_FILE")"
        rm -f "$PID_FILE"
    fi
    pids="$( { printf '%s\n' "$pids"; scoring_process_pids; } | tr ' ' '\n' | sort -u | grep -v '^$' )"
    if [[ -z "$pids" ]]; then
        echo "Фоновых сервисов скоринга не найдено."
        return 0
    fi
    echo "Останавливаю фоновые сервисы скоринга..."
    for pid in $pids; do
        kill "$pid" 2>/dev/null && echo "  остановлен PID $pid" || true
    done
    # Ждём освобождения портов.
    for port in "$PORT_PARSER" "$PORT_TRANSPORT"; do
        for _ in $(seq 1 40); do
            if ! port_in_use "$port"; then
                break
            fi
            sleep 0.5
        done
    done
    echo "Готово."
}

stop_all() {
    stop_services
    if [[ "${SKIP_LANGFUSE:-0}" != "1" ]]; then
        echo "Останавливаю LangFuse (docker)..."
        ( cd "$ROOT_DIR" && COMPOSE_PROFILES=langfuse docker compose -f "$COMPOSE_FILE" down ) || true
    fi
    echo "Останавливаю Redis ($REDIS_CONTAINER)..."
    docker stop "$REDIS_CONTAINER" 2>/dev/null || true
    echo "Останавливаю PostgreSQL ($DB_CONTAINER)..."
    docker stop "$DB_CONTAINER" 2>/dev/null || true
    echo "Стек dev остановлен."
}

case "$CMD" in
    stop)
        stop_all
        exit 0
        ;;
    up|start)
        : # продолжаем ниже (перед стартом чистим зависшие сервисы)
        ;;
    -h|--help|help)
        sed -n '2,20p' "$0"
        exit 0
        ;;
    *)
        echo "Неизвестная команда: $CMD (используйте: up | start | stop)" >&2
        exit 2
        ;;
esac

if ! command -v docker >/dev/null 2>&1; then
    echo "Ошибка: docker не установлен." >&2
    exit 1
fi

# Перед стартом закрываем зависшие сервисы от прошлого запуска (иначе порты заняты).
stop_services

# --- Инфраструктура ---------------------------------------------------------
# PostgreSQL — через db_up.sh (создаёт контейнер + применяет миграции Liquibase).
"$SCRIPT_DIR/db_up.sh"

# Redis — отдельный контейнер (идемпотентно: создаёт или запускает существующий).
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
    rm -f "$PID_FILE"
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
    # Compose ждёт только healthcheck зависимостей; langfuse-db может долго
    # восстанавливаться после нештатной остановки — дожидаемся реальной готовности UI.
    echo "Ожидание готовности LangFuse..."
    ready=0
    for i in $(seq 1 60); do
        if curl -sf -m 2 "http://localhost:3000/api/public/health" >/dev/null 2>&1; then
            echo "LangFuse готов."
            ready=1
            break
        fi
        sleep 2
    done
    if [[ "$ready" != 1 ]]; then
        echo "Внимание: LangFuse не ответил на /api/public/health за 2 мин — проверьте лог langfuse-web." >&2
    fi
    echo "LangFuse UI: http://localhost:3000"
else
    echo "LangFuse пропущен (SKIP_LANGFUSE=1)."
fi

# Общие компоненты каскада (scoring_common) — локально не установлены как пакет,
# поэтому добавляем их в PYTHONPATH (как это делают Dockerfile подпроектов).
COMMON_PATH="$ROOT_DIR/src/scoring_common"
SCORING_PYTHONPATH="$COMMON_PATH${PYTHONPATH:+:$PYTHONPATH}"

# --- scoring_service (воркер стадии Fit, LLM-пайплайн) ---------------------
echo "Запуск scoring_service (воркер Fit)..."
( cd "$ROOT_DIR/src/scoring_service" && PYTHONPATH="$SCORING_PYTHONPATH" \
    SCORE_PARSER_API_URL="http://127.0.0.1:$PORT_PARSER" \
    uv run python -m scoring_service worker ) &
BGPIDS+=($!)

# --- pwin_service (воркер стадии P(win), заглушка) -------------------------
echo "Запуск pwin_service (воркер P(win), заглушка)..."
( cd "$ROOT_DIR/src/pwin_service" && PYTHONPATH="$SCORING_PYTHONPATH" \
    PWIN_PARSER_API_URL="http://127.0.0.1:$PORT_PARSER" PWIN_USE_STUB=true \
    uv run python -m pwin_service worker ) &
BGPIDS+=($!)

# --- margin_service (воркер стадии Margin) ---------------------------------
echo "Запуск margin_service (воркер Margin)..."
( cd "$ROOT_DIR/src/margin_service" && PYTHONPATH="$SCORING_PYTHONPATH" \
    MARGIN_PARSER_API_URL="http://127.0.0.1:$PORT_PARSER" \
    uv run python -m margin_service worker ) &
BGPIDS+=($!)

# --- scoring_transport ------------------------------------------------------
echo "Запуск scoring_transport на :$PORT_TRANSPORT..."
( cd "$ROOT_DIR/src/scoring_transport" \
    && PYTHONPATH="$SCORING_PYTHONPATH" \
    TRANSPORT_PARSER_API_URL="http://127.0.0.1:$PORT_PARSER" \
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
# Сохраняем PID-файл, чтобы `run_all.sh stop` мог остановить сервисы отдельно.
printf '%s\n' "${BGPIDS[@]}" > "$PID_FILE"
if [[ "$ready" != 1 ]]; then
    echo "Внимание: scoring_transport не ответил на /health за 30 с — проверьте его лог." >&2
fi

echo "Фоновый стек поднят. Запустите парсер отдельно:"
echo "  uv run zp --configs configs serve --host 0.0.0.0 --port $PORT_PARSER"
echo "Ctrl+C останавливает фоновые сервисы."
wait
