#!/usr/bin/env bash
# Управляет Docker-compose стеком проекта (docker/docker-compose.yml).
#
# Стек: db (PostgreSQL) + liquibase (миграции) + redis + scoring_service +
# scoring_transport + parser + api (FastAPI :8000). Единый стек одной командой.
# Простая альтернатива длинным командам `docker compose -f docker/docker-compose.yml ...`.
#
# Использование:
#   scripts/compose.sh                   # то же, что: up
#   scripts/compose.sh up                # собрать и поднять стек в фоне (up -d --build)
#   scripts/compose.sh up --langfuse     # то же + поднять LangFuse (профиль langfuse)
#   scripts/compose.sh down              # остановить и удалить контейнеры (тома БД сохраняются)
#   scripts/compose.sh stop              # остановить и удалить контейнеры (освобождает порты; том БД сохраняется)
#   scripts/compose.sh start             # запустить остановленные контейнеры (если не удалялись)
#   scripts/compose.sh restart           # перезапустить
#   scripts/compose.sh ps                # статус контейнеров
#   scripts/compose.sh logs [svc]        # логи (можно по сервису, например: logs parser)
#   scripts/compose.sh build             # пересобрать образы
#   scripts/compose.sh status            # алиас для ps
#   scripts/compose.sh free-port [порт]  # освободить порт (по умолчанию 5432), занятый контейнером
#   scripts/compose.sh free-port --force # без запроса подтверждения
#
# По умолчанию (боевой) LangFuse НЕ поднимается (профиль отключён), даже если
# COMPOSE_PROFILES=langfuse в docker/.env. Для LangFuse — `up --langfuse`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker/docker-compose.yml"
PROJECT="zakupki"

# Профили docker compose. По умолчанию пусто — боевой стек поднимается БЕЗ LangFuse
# (даже если в docker/.env задан COMPOSE_PROFILES=langfuse, shell-переменная перекрывает).
# `up --langfuse` явно включает профиль langfuse.
PROFILE=""

CMD="${1:-up}"
shift || true

if ! command -v docker >/dev/null 2>&1; then
    echo "Ошибка: docker не установлен." >&2
    exit 1
fi

# --- free-port: освободить порт, занятый Docker-контейнером -----------------
free_port() {
    local port="${1:-5432}"
    local force="${2:-0}"
    if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
        echo "Ошибка: невалидный порт: $port" >&2
        return 2
    fi
    mapfile -t CONTAINERS < <(docker ps --filter "publish=$port" --format '{{.Names}}')
    if (( ${#CONTAINERS[@]} == 0 )); then
        echo "Порт $port свободен (нет Docker-контейнера, его пробросившего)."
        return 0
    fi
    echo "Порт $port занимает Docker-контейнер(ы):"
    for name in "${CONTAINERS[@]}"; do
        echo "  - $name"
    done
    if (( force != 1 )); then
        read -r -p "Остановить их (docker stop, данные сохранятся)? [y/N] " ans
        if [[ ! "$ans" =~ ^[yYдД] ]]; then
            echo "Отменено." >&2
            return 0
        fi
    fi
    for name in "${CONTAINERS[@]}"; do
        echo "Останавливаю $name..."
        docker stop "$name"
    done
    echo "Порт $port освобождён."
}

# Разбор аргументов free-port: <порт> и --force в любом порядке.
FP_PORT="5432"
FP_FORCE=0
if [[ "$CMD" == "free-port" ]]; then
    for a in "$@"; do
        case "$a" in
            --force) FP_FORCE=1 ;;
            *) FP_PORT="$a" ;;
        esac
    done
fi

case "$CMD" in
    -h|--help|help)
        sed -n '2,19p' "$0"
        exit 0
        ;;
    up)
        # Опционально: up --langfuse — поднять и LangFuse (профиль).
        for a in "$@"; do
            case "$a" in
                --langfuse) PROFILE="langfuse" ;;
                *) ;;
            esac
        done
        # Порт 5432 нужен сервису db; если его занимает локальный контейнер
        # (например zakupki_db из db_up.sh) — спросить и освободить.
        if docker ps --filter "publish=5432" --format '{{.Names}}' | grep -q .; then
            echo "Внимание: порт 5432 занят Docker-контейнером(ами):"
            docker ps --filter "publish=5432" --format '  - {{.Names}}'
            read -r -p "Освободить порт (остановить их, данные сохранятся)? [y/N] " ans
            if [[ "$ans" =~ ^[yYдД] ]]; then
                free_port 5432 1
            else
                echo "Отменено: стек не поднят, пока порт 5432 занят." >&2
                exit 1
            fi
        fi
        cd "$ROOT_DIR"
        COMPOSE_PROFILES="$PROFILE" docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" up -d --build
        echo "Стек поднят. API: http://localhost:8000/  (лог: scripts/compose.sh logs)"
        ;;
    down)
        cd "$ROOT_DIR"
        COMPOSE_PROFILES="$PROFILE" docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" down
        echo "Стек остановлен и удалён (том БД pgdata сохранён)."
        ;;
    stop)
        cd "$ROOT_DIR"
        COMPOSE_PROFILES="$PROFILE" docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" down
        echo "Стек остановлен, контейнеры удалены (том БД pgdata сохранён)."
        ;;
    start)
        cd "$ROOT_DIR"
        COMPOSE_PROFILES="$PROFILE" docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" start
        echo "Контейнеры запущены."
        ;;
    restart)
        cd "$ROOT_DIR"
        COMPOSE_PROFILES="$PROFILE" docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" restart
        ;;
    build)
        cd "$ROOT_DIR"
        COMPOSE_PROFILES="$PROFILE" docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" build
        ;;
    ps|status)
        cd "$ROOT_DIR"
        COMPOSE_PROFILES="$PROFILE" docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" ps
        ;;
    logs)
        cd "$ROOT_DIR"
        COMPOSE_PROFILES="$PROFILE" docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" logs -f "$@"
        ;;
    free-port)
        free_port "$FP_PORT" "$FP_FORCE"
        ;;
    *)
        echo "Неизвестная команда: $CMD (используйте: up|down|stop|start|restart|build|ps|logs|free-port)" >&2
        exit 2
        ;;
esac
