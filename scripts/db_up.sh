#!/usr/bin/env bash
# Поднимает БД PostgreSQL для парсера.
#
# Логика:
#   - если контейнер <CONTAINER> (по умолчанию zakupki_db) уже существует —
#     просто запускает его (данные и миграции сохранены в volume);
#   - если контейнера нет — создаёт новый контейнер с PostgreSQL и применяет
#     миграции Liquibase.
#
# Использование:
#   scripts/db_up.sh              # поднять БД (существующую или новую)
#   scripts/db_up.sh <container>  # использовать другое имя контейнера
#   scripts/db_up.sh --status     # статус контейнера и таблиц (без запуска)

set -euo pipefail

CONTAINER="zakupki_db"
MODE="up"
for arg in "$@"; do
    case "$arg" in
        --status) MODE="status" ;;
        -h|--help)
            sed -n '2,8p' "$0"
            exit 0
            ;;
        *) CONTAINER="$arg" ;;
    esac
done

POSTGRES_IMAGE="postgres:16-alpine"
VOLUME_NAME="zakupki_pgdata"
RETRY_LIMIT=30
RETRY_SLEEP=2
DB_USER="postgres"
DB_PASS="postgres"
DB_NAME="zakupki"

container_exists() {
    docker ps -a --format '{{.Names}}' | grep -qx -- "$CONTAINER"
}

container_running() {
    docker ps --format '{{.Names}}' | grep -qx -- "$CONTAINER"
}

# Проверка, что порт 5432 опубликован контейнером И доступен с хоста.
# После сбоя «Bind ... port is already allocated» контейнер может стартовать без
# сети (docker start создаёт его без публикации порта): внутри `pg_isready` отвечает,
# а с хоста localhost:5432 недоступен — Liquibase/SQLAlchemy потом висят.
port_published_and_reachable() {
    docker port "$CONTAINER" 5432 >/dev/null 2>&1 || return 1
    if command -v nc >/dev/null 2>&1; then
        nc -z localhost 5432 >/dev/null 2>&1 || return 1
    elif command -v bash >/dev/null 2>&1 && [ -e /dev/tcp ]; then
        (echo > /dev/tcp/localhost/5432) >/dev/null 2>&1 || return 1
    else
        # Windows (Git Bash): проверяем, что сокет слушается на хосте.
        netstat -ano 2>/dev/null | grep -qE ":5432[[:space:]].*LISTENING" || return 1
    fi
    return 0
}

# Владелец порта 5432 (для диагностики при конфликте).
port_owner_5432() {
    local owner
    owner="$(docker ps -a --format '{{.Names}}|{{.Ports}}' 2>/dev/null | grep -E "\b5432->" | head -n1 | cut -d'|' -f1)"
    if [[ -n "$owner" ]]; then
        echo "контейнер $owner"
        return 0
    fi
    if command -v ss >/dev/null 2>&1; then
        ss -ltnp 2>/dev/null | grep -E ":5432\b" | head -n1 | sed -E 's/.*users:\(\("//; s/".*//' | grep -v '^$' && return 0 || true
    else
        local pid
        pid="$(netstat -ano 2>/dev/null | grep -E ":5432[[:space:]].*LISTENING" | head -n1 | awk '{print $NF}')"
        if [[ -n "$pid" ]]; then
            echo "PID $pid"
            return 0
        fi
    fi
    echo "неизвестный процесс"
}

wait_db() {
    echo "Ожидание готовности БД..."
    for i in $(seq 1 "$RETRY_LIMIT"); do
        if docker exec "$CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
            echo "БД готова (контейнер $CONTAINER)."
            return 0
        fi
        sleep "$RETRY_SLEEP"
    done
    echo "Ошибка: БД не поднялась за $((RETRY_LIMIT * RETRY_SLEEP)) с." >&2
    return 1
}

run_migrations() {
    echo "Применение миграций Liquibase..."
    # Git Bash (MSYS) на Windows превращает POSIX-пути в пути Windows, ломая и
    # host-маунт, и контейнерные пути вида /liquibase/... (MSYS_NO_PATHCONV).
    # Host-путь передаём в Windows-формате через cygpath -w.
    local mount_src="$PWD/docker/liquibase/changelog"
    if command -v cygpath >/dev/null 2>&1; then
        mount_src="$(cygpath -w "$mount_src")"
    fi
    MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
    docker run --rm --network host \
        -v "$mount_src:/liquibase/changelog" \
        liquibase/liquibase:4.30 \
        --search-path=/liquibase/changelog \
        --changelog-file=db.changelog-master.yaml \
        --url="jdbc:postgresql://localhost:5432/$DB_NAME" \
        --username="$DB_USER" --password="$DB_PASS" \
        update
    echo "Миграции применены."
}

migrations_applied() {
    docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c \
        "select to_regclass('public.databasechangelog')" 2>/dev/null | grep -q databasechangelog
}

show_status() {
    docker ps -a --filter "name=^/${CONTAINER}$" --format '{{.Names}}: {{.Status}}'
    if container_running; then
        echo "--- таблицы ---"
        docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c '\dt'
    fi
}

if ! command -v docker >/dev/null 2>&1; then
    echo "Ошибка: docker не установлен." >&2
    exit 1
fi

if [[ "$MODE" == "status" ]]; then
    if ! container_exists; then
        echo "Контейнер $CONTAINER не найден."
        exit 0
    fi
    show_status
    exit 0
fi

if container_exists; then
    echo "Контейнер $CONTAINER уже существует."
    if ! container_running; then
        echo "Запуск контейнера $CONTAINER..."
        docker start "$CONTAINER"
    fi
    # После старта проверяем реальную доступность порта с хоста. Если контейнер
    # остался без сети (следствие прошлого «Bind ... port is already allocated») —
    # пересоздаём его с корректной публикацией порта (volume zakupki_pgdata
    # сохраняется, миграции идемпотентны).
    if ! port_published_and_reachable; then
        echo "Контейнер $CONTAINER не публикует/не отдаёт порт 5432 с хоста — пересоздаю (volume сохранится)."
        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
        docker run -d --name "$CONTAINER" \
            -p 5432:5432 \
            -e POSTGRES_USER="$DB_USER" \
            -e POSTGRES_PASSWORD="$DB_PASS" \
            -e POSTGRES_DB="$DB_NAME" \
            -v "$VOLUME_NAME:/var/lib/postgresql/data" \
            "$POSTGRES_IMAGE"
    fi
    wait_db
    if migrations_applied; then
        echo "БД готова к использованию (миграции уже применены)."
    else
        echo "В контейнере нет миграций — применяю."
        run_migrations
    fi
else
    echo "Контейнера $CONTAINER нет — создаю новый..."
    if ! docker ps -a --format '{{.Names}}|{{.Ports}}' 2>/dev/null | grep -qE "\b5432->"; then
        if command -v nc >/dev/null 2>&1; then
            if nc -z localhost 5432 >/dev/null 2>&1; then
                echo "Ошибка: порт 5432 уже занят ($(port_owner_5432)) — БД не может стартовать." >&2
                echo "Остановите владельца порта (например: docker compose -f <путь> down) и повторите." >&2
                exit 1
            fi
        elif netstat -ano 2>/dev/null | grep -qE ":5432[[:space:]].*LISTENING"; then
            echo "Ошибка: порт 5432 уже занят ($(port_owner_5432)) — БД не может стартовать." >&2
            echo "Остановите владельца порта (например: docker compose -f <путь> down) и повторите." >&2
            exit 1
        fi
    fi
    docker run -d --name "$CONTAINER" \
        -p 5432:5432 \
        -e POSTGRES_USER="$DB_USER" \
        -e POSTGRES_PASSWORD="$DB_PASS" \
        -e POSTGRES_DB="$DB_NAME" \
        -v "$VOLUME_NAME:/var/lib/postgresql/data" \
        "$POSTGRES_IMAGE"
    wait_db
    run_migrations
fi

show_status
