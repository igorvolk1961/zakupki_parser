#!/usr/bin/env bash
# Управляет Docker-compose стеком проекта (docker/docker-compose.yml).
#
# Стек: db (PostgreSQL) + liquibase (миграции) + redis + scoring_service +
# scoring_transport + api (FastAPI :8000, веб-интерфейс + цикл мониторинга парсера).
# Простая альтернатива длинным командам `docker compose -f docker/docker-compose.yml ...`.
#
# Использование:
#   scripts/compose.sh                   # то же, что: up
#   scripts/compose.sh up                # собрать и поднять стек в фоне (up -d --build)
#   scripts/compose.sh up --no-langfuse   # поднять стек БЕЗ LangFuse (быстрый dev-стек)
#   scripts/compose.sh demo up [args]    # изолированный демо-стек: свой project и свои
#                                        # host-порты (не конфликтует с dev); demo down/ps/logs/config тоже работают
#   scripts/compose.sh demo up --ref [тег]  # демо из зафиксированного снапшота (--ref без значения
#                                        # => demo-fixed; эквивалент DEMO_REF=<тег>)
#   scripts/compose.sh down              # остановить и удалить контейнеры (тома БД сохраняются)
#   scripts/compose.sh stop              # остановить и удалить контейнеры (освобождает порты; том БД сохраняется)
#   scripts/compose.sh start             # запустить остановленные контейнеры (если не удалялись)
#   scripts/compose.sh restart           # перезапустить
#   scripts/compose.sh ps                # статус контейнеров
#   scripts/compose.sh logs [svc]        # логи (можно по сервису, например: logs api)
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

# Демо-режим: `scripts/compose.sh demo up` — изолированный стек для демонстрации.
# Отдельный --project-name (свои контейнеры/сети/тома) и свой набор host-портов для
# всех сервисов, поэтому не конфликтует с dev и не зависит от него.
DEMO=0
if [[ "$CMD" == "demo" ]]; then
    DEMO=1
    CMD="${1:-up}"
    shift || true
    PROJECT="zakupki-demo"
    export ZAKUPKI_DB_PORT=15432 API_PORT=18000 TRANSPORT_PORT=18200 \
        LANGFUSE_DB_PORT=15434 LANGFUSE_S3_PORT=19002 \
        LANGFUSE_S3_CONSOLE_PORT=19003 LANGFUSE_UI_PORT=13001

    # Короткий флаг снапшота: `demo up --ref [тег]` / `demo up --ref=тег` / `-r <тег>`.
    # Без значения — тег demo-fixed. Перекрывает DEMO_REF из окружения.
    _demo_ref="${DEMO_REF:-}"
    _demo_args=()
    while (( $# )); do
        case "$1" in
            --ref|-r)
                if (( $# >= 2 )) && [[ "${2:-}" != -* ]]; then
                    _demo_ref="$2"; shift 2
                else
                    _demo_ref="demo-fixed"; shift
                fi
                ;;
            --ref=*|-r=*)
                _demo_ref="${1#*=}"; shift
                ;;
            *)
                _demo_args+=("$1"); shift
                ;;
        esac
    done
    set -- "${_demo_args[@]}"
    DEMO_REF="$_demo_ref"

    # Демо из зафиксированного снапшота: `scripts/compose.sh demo up --ref` (или DEMO_REF=<тег>)
    # разворачивает демо из кода этого рефа через git worktree, изолированно от dev.
    # Без --ref/DEMO_REF — демо собирается из текущего рабочего дерева.
    if [[ -n "${DEMO_REF:-}" ]]; then
        DEMO_SRC="$ROOT_DIR/.demo-src"
        if [[ -e "$DEMO_SRC/.git" ]]; then
            # Снапшот уже существует (linked worktree) — переставляем на нужный ref.
            git -C "$DEMO_SRC" fetch --tags --quiet 2>/dev/null || true
            git -C "$DEMO_SRC" checkout --detach "$DEMO_REF" >/dev/null
        else
            echo "Подготавливаю снапшот демо из '$DEMO_REF' ..." >&2
            git worktree add --detach "$DEMO_SRC" "$DEMO_REF" >/dev/null
        fi
        # Секреты/окружение не входят в git-снапшот — берём из актуального репозитория.
        if [[ -f "$ROOT_DIR/docker/.env" ]]; then cp "$ROOT_DIR/docker/.env" "$DEMO_SRC/docker/.env"; fi
        if [[ -f "$ROOT_DIR/.env" ]]; then cp "$ROOT_DIR/.env" "$DEMO_SRC/.env"; fi
        COMPOSE_FILE="$DEMO_SRC/docker/docker-compose.yml"
        echo "Снапшот демо: $DEMO_SRC (ref=$DEMO_REF)" >&2
    fi
fi

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

        # Окружение/профиль читаем из docker/.env (если есть), чтобы проверки ниже
        # учитывали переопределения портов и токен конвейера.
        env_file="$ROOT_DIR/docker/.env"
        if [[ -f "$env_file" ]]; then
            set -a
            # shellcheck disable=SC1091
            source "$env_file"
            set +a
        fi

        # Демо-порты: переопределяют host-порты всех сервисов, чтобы стек демонстрации
        # жил на отдельном наборе портов и не пересекался с dev.
        if (( DEMO == 1 )); then
            export ZAKUPKI_DB_PORT=15432 API_PORT=18000 TRANSPORT_PORT=18200 \
                LANGFUSE_DB_PORT=15434 LANGFUSE_S3_PORT=19002 \
                LANGFUSE_S3_CONSOLE_PORT=19003 LANGFUSE_UI_PORT=13001
        fi

        # Внутренний токен конвейера обязателен: воркеры/транспорт шлют его в API
        # (X-Internal-Token), иначе api отвечает 401 на служебные эндпоинты.
        # Проверяем до up, чтобы не поднять заведомо сломанный стек. Значение
        # берётся из docker/.env (должно совпадать с корневым .env).
        if [[ -z "${ZAKUPKI_INTERNAL_TOKEN:-}" ]]; then
            echo "Ошибка: ZAKUPKI_INTERNAL_TOKEN не задан в docker/.env." >&2
            echo "Он обязателен и должен совпадать со значением в корневом .env." >&2
            exit 1
        fi

        # Host-порты LangFuse (переопределяются в docker/.env: LANGFUSE_*_PORT;
        # дефолтные — 5433/9000/9001/3000). Порт 5432 проверяем чуть ниже.
        if [[ -n "$PROFILE" ]]; then
            for lf_port in "${LANGFUSE_DB_PORT:-5433}" "${LANGFUSE_S3_PORT:-9000}" "${LANGFUSE_S3_CONSOLE_PORT:-9001}" "${LANGFUSE_UI_PORT:-3000}"; do
                if docker ps --filter "publish=$lf_port" --filter "label=com.docker.compose.project!=$PROJECT" --format '{{.Names}}' | grep -q .; then
                    echo "Внимание: порт $lf_port занят Docker-контейнером(ами):"
                    docker ps --filter "publish=$lf_port" --filter "label=com.docker.compose.project!=$PROJECT" --format '  - {{.Names}}'
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

        # Порт БД (ZAKUPKI_DB_PORT, по умолчанию 5432) нужен сервису db; если его
        # занимает локальный контейнер (например zakupki_db из db_up.sh) — спросить.
        db_port="${ZAKUPKI_DB_PORT:-5432}"
        if docker ps --filter "publish=$db_port" --filter "label=com.docker.compose.project!=$PROJECT" --format '{{.Names}}' | grep -q .; then
            echo "Внимание: порт $db_port занят Docker-контейнером(ами):"
            docker ps --filter "publish=$db_port" --filter "label=com.docker.compose.project!=$PROJECT" --format '  - {{.Names}}'
            read -r -p "Освободить порт (остановить их, данные сохранятся)? [y/N] " ans
            if [[ "$ans" =~ ^[yYдД] ]]; then
                free_port "$db_port" 1
            else
                echo "Отменено: стек не поднят, пока порт $db_port занят." >&2
                exit 1
            fi
        fi
        cd "$ROOT_DIR"
        COMPOSE_PROFILES="$PROFILE" docker compose --project-name "$PROJECT" -f "$COMPOSE_FILE" up -d --build
        echo "Стек поднят. API: http://localhost:${API_PORT:-8000}/  (лог: scripts/compose.sh logs)"
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
        echo "Неизвестная команда: $CMD (используйте: up|down|stop|start|restart|build|config|ps|logs|free-port, или префикс demo <команда>)" >&2
        exit 2
        ;;
esac
