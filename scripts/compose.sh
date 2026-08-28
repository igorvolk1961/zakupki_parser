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
#   scripts/compose.sh up --no-langfuse   # поднять стек БЕЗ LangFuse (быстрый dev-стек)
#   scripts/compose.sh down              # остановить и удалить контейнеры (тома БД сохраняются)
#   scripts/compose.sh stop              # остановить и удалить контейнеры (освобождает порты; том БД сохраняется)
#   scripts/compose.sh start             # запустить остановленные контейнеры (если не удалялись)
#   scripts/compose.sh restart           # перезапустить
#   scripts/compose.sh ps                # статус контейнеров
#   scripts/compose.sh logs [svc]        # логи (можно по сервису, например: logs parser)
#   scripts/compose.sh build             # пересобрать образы
#   scripts/compose.sh config [args]     # проверить/вывести манифест (docker compose config), напр. config --quiet
#   scripts/compose.sh status            # алиас для ps
#   scripts/compose.sh free-port [порт]  # освободить порт (по умолчанию 5432), занятый контейнером
#   scripts/compose.sh free-port --force # без запроса подтверждения
#
# По умолчанию стек поднимается ВМЕСТЕ с LangFuse (профиль langfuse) — в продакшне
# трассировка LLM-вызовов нужна. Для быстрого dev-стека без LangFuse: `up --no-langfuse`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker/docker-compose.yml"
PROJECT="zakupki"

# Профили docker compose. По умолчанию включён профиль `langfuse` — в продакшне
# трассировка LLM нужна, поэтому стек поднимается вместе с LangFuse.
# Быстрый dev-стек без LangFuse: `up --no-langfuse` (обнуляет профиль).
PROFILE="langfuse"

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
        # LangFuse поднимается по умолчанию (продакшн). `--no-langfuse` — отключить.
        for a in "$@"; do
            case "$a" in
                --langfuse) PROFILE="langfuse" ;;
                --no-langfuse) PROFILE="" ;;
                *) ;;
            esac
        done

        # Внутренний токен конвейера обязателен: воркеры/транспорт шлют его в API
        # (X-Internal-Token), иначе api отвечает 401 на служебные эндпоинты.
        # Проверяем до up, чтобы не поднять заведомо сломанный стек. Значение
        # берётся из docker/.env (должно совпадать с корневым .env).
        env_file="$ROOT_DIR/docker/.env"
        token=""
        if [[ -f "$env_file" ]]; then
            token="$(grep -E '^[[:space:]]*ZAKUPKI_INTERNAL_TOKEN=' "$env_file" | tail -n1 | cut -d= -f2- | tr -d '[:space:]')" || true
        fi
        if [[ -z "$token" ]]; then
            echo "Ошибка: ZAKUPKI_INTERNAL_TOKEN не задан в docker/.env." >&2
            echo "Он обязателен и должен совпадать со значением в корневом .env." >&2
            exit 1
        fi

        # LangFuse-порты (при включённом профиле): 5433 (langfuse-db), 9000/9001
        # (minio), 3000 (langfuse-web). Порт 5432 проверяем чуть ниже.
        if [[ -n "$PROFILE" ]]; then
            for lf_port in 5433 9000 9001 3000; do
                if docker ps --filter "publish=$lf_port" --format '{{.Names}}' | grep -q .; then
                    echo "Внимание: порт $lf_port занят Docker-контейнером(ами):"
                    docker ps --filter "publish=$lf_port" --format '  - {{.Names}}'
                    read -r -p "Освободить порт (остановить их, данные сохранятся)? [y/N] " ans
                    if [[ "$ans" =~ ^[yYдД] ]]; then
                        free_port "$lf_port" 1
                    else
                        echo "Отменено: стек не поднят, пока порт $lf_port занят." >&2
                        exit 1
                    fi
                fi
            done
        fi

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
    config)
        cd "$ROOT_DIR"
        COMPOSE_PROFILES="$PROFILE" docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" config "$@"
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
        echo "Неизвестная команда: $CMD (используйте: up|down|stop|start|restart|build|config|ps|logs|free-port)" >&2
        exit 2
        ;;
esac
