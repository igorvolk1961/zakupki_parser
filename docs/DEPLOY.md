# Развёртывание стека на новом сервере

Последовательность подъёма контейнеров проекта (`docker/docker-compose.yml`) на чистом
удалённом сервере. Стек: `db` + `liquibase` + `redis` + `langfuse-*` + `scoring_service` +
`scoring_transport` + `api` (FastAPI :8000). Управление — `scripts/compose.sh`.
`api` — единый процесс парсера: веб-интерфейс (панель devops) и цикл мониторинга
(запускается вместе с сервисом по флагу `auto_start_monitoring` в `config_ops.yaml`).

> Все команды для узла, где живёт стек. Пользователь — `igor` (пример); root-действия —
> через штатный доступ (SSH-ключ root или панель провайдера), НЕ через `sudo`/`su`,
> если они не настроены.

---

## 0. Предварительные требования

- Linux x86_64 (тест: Ubuntu 24.04, Docker 29.x), 2+ vCPU, 8+ ГБ RAM (LangFuse + Postgres + ClickHouse + сервисы).
- Доступ по SSH.
- `git` (для клона и для `demo up --ref` — git worktree).
- HTTP-доступ к Docker Hub/registry (для `postgres`, `redis`, `langfuse`, `minio`, `liquibase`).

---

## 1. Установка Docker + Compose v2

Проверка, что есть:

```bash
docker version           # должен показать и Client, и Server (Engine)
docker compose version   # ВАЖНО: должен вывести версию Compose v2
```

Если `docker version` есть, а `docker compose version` падает (`unknown command: docker compose`)
и `docker-compose` тоже отсутствует — **Compose не установлен**. Для Ubuntu (`docker.io`):

```bash
apt-get update
apt-get install -y docker-compose-v2
docker compose version
```

Либо скачать плагин вручную:

```bash
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
```

> `scripts/compose.sh` использует именно `docker compose … --project-name` (Compose v2).

---

## 2. Доступ к Docker для деплой-пользователя

Сокет: `/var/run/docker.sock` (владелец `root:docker`, права `0660`). Деплой-пользователь
должен состоять в группе `docker`.

```bash
# от root (SSH-ключом root / панелью):  <- sudo/su может не работать на Ubuntu!
usermod -aG docker igor
```

Применить группу — **перелогиниться** (или `newgrp docker`):

```bash
newgrp docker      # в текущей сессии; надёжнее — выход и вход заново
id                 # в groups должно появиться docker
docker version     # Server (Engine ...) должен показаться без root
```

Проверка сокета:

```bash
ls -l /var/run/docker.sock   # srw-rw---- root docker
id                          # группы: igor ... docker
```

---

## 3. Код репозитория

```bash
cd /home/igor/projects
git clone <адрес репозитория> zakupki_parser
cd zakupki_parser
```

Права/владелец — деплой-пользователь (чтобы git и compose работали без root):

```bash
chown -R igor:igor /home/igor/projects/zakupki_parser
# если git ругается "dubious ownership":
git config --global --add safe.directory /home/igor/projects/zakupki_parser
```

> `demo up --ref` создаёт git worktree (`.demo-src`) — тогда тоже нужен `safe.directory`
> и корректный владелец, иначе "detected dubious ownership".

---

## 4. Конфигурация окружения — критично до `up`

### 4.1 Обязательный внутренний токен

`compose.sh up` откажется поднимать стек, если в `docker/.env` не задан `ZAKUPKI_INTERNAL_TOKEN`
(иначе `api` отвечает 401 на служебные вызовы конвейера):

```bash
cp docker/.env.example docker/.env
# в docker/.env задать:
#   ZAKUPKI_INTERNAL_TOKEN=<то же значение, что в корневом .env>
```

Также убедись, что корневой `.env` (или `docker/.env`) содержит тот же `ZAKUPKI_INTERNAL_TOKEN`.

### 4.2 LLM-ключи

```bash
SCORE_LLM_BASE_URL=https://api.deepseek.com/v1
SCORE_LLM_API_KEY=<ключ>
SCORE_LLM_MODEL=deepseek-chat

ANALYSIS_LLM_BASE_URL=https://api.deepseek.com/v1
ANALYSIS_LLM_API_KEY=<ключ>          # обычно тот же
ANALYSIS_LLM_MODEL=deepseek-chat
```

### 4.3 Секреты LangFuse (генерируем)

```bash
openssl rand -hex 16   # -> LANG_SALT
openssl rand -hex 32   # -> LANG_ENCRYPTION_KEY
openssl rand -hex 32   # -> LANG_NEXTAUTH_SECRET
```

Записать в `docker/.env`:

```bash
LANG_SALT=...
LANG_ENCRYPTION_KEY=...
LANG_NEXTAUTH_SECRET=...
LANG_INIT_PUBLIC_KEY=pk-lf-...
LANG_INIT_SECRET_KEY=sk-lf-...
LANG_ADMIN_PASSWORD=<пароль админа LangFuse>
```

### 4.4 MinIO и LangFuse-порты

```bash
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin      # смени на свой
# Host-порты LangFuse (в дефолте 5433/9000/9001/3000; в .env.example подняты, чтобы
# не конфликтовать с демо-стеком):
LANGFUSE_DB_PORT=5434
LANGFUSE_S3_PORT=9002
LANGFUSE_S3_CONSOLE_PORT=9003
LANGFUSE_UI_PORT=3001
```

> Файл `docker/.env` игнорируется git — секреты не коммитятся.

---

## 5. Подъём стека

```bash
./scripts/compose.sh up --build
```

По умолчанию поднимается ВМЕСТЕ с LangFuse (профиль `langfuse`). Быстрый dev-стек без
LangFuse:

```bash
./scripts/compose.sh up --build --no-langfuse
```

`down`/`stop`/`start`/`restart`/`build`/`config`/`ps`/`logs`/`free-port` — см. `./scripts/compose.sh --help`.

---

## 6. Проверка

```bash
./scripts/compose.sh ps          # все сервисы Up
./scripts/compose.sh logs api    # ошибки api
curl -s http://localhost:8000/   # API отвечает (health/redirect)
```

Порты по умолчанию: **API :8000**, БД :5432, **scoring-transport :8200**, LangFuse UI :3001,
Минио :9002/:9003.

---

## 7. Демо-стек (изолированный, опционально)

```bash
./scripts/compose.sh demo ps
./scripts/compose.sh demo up              # из текущего рабочего дерева
./scripts/compose.sh demo up --ref        # из снапшота demo-fixed (через git worktree)
./scripts/compose.sh demo down
```

Демо-стек живёт на своих портах (БД :15432, API :18000, transport :18200 и т.д.) и не
конфликтует с dev-стеком на том же хосте.

---

## 8. Доступ с удалённой машины и публикация (reverse-proxy / HTTPS)

Что наружу отдаёт сервер (по умолчанию):

| Сервис | Host-порт | Назначение | Наружу |
|---|---|---|---|
| `api` | `8000` (`API_PORT`) | FastAPI + веб-интерфейс | ✅ точка входа |
| LangFuse UI | `3001` (`LANGFUSE_UI_PORT`) | панель трассировки LLM | ⚠️ по желанию |
| PostgreSQL | `5432` (`ZAKUPKI_DB_PORT`) | БД | ❌ только внутренний |
| scoring-transport | `8200` (`TRANSPORT_PORT`) | служебный (`X-Internal-Token`) | ❌ внутренний |
| MinIO | `9002`/`9003` | объектное хранилище | ❌ внутренний |

Публично открываем только **`api` :8000** (и, при необходимости, LangFuse UI). Остальное —
по SSH-туннелю либо только внутри compose-сети.

### 8.1 SSH-туннель (безопасно, рекомендую)

С локальной машины (порт 22 открыт в security group; пользователь — `igor`, пароль/ключ):

```bash
ssh -N -L 8000:localhost:8000 igor@pyfdbjrwen      # API + веб
ssh -N -L 3001:localhost:3001 igor@pyfdbjrwen      # LangFuse UI
```

Открывать локально: `http://localhost:8000/`, `http://localhost:3001/`.

### 8.2 Публичный порт

Открыть в облачном фаерволе **inbound TCP 8000** → `http://<IP_СЕРВЕРА>:8000/`.
Проверка с самого сервера: `curl -s http://localhost:8000/ | head`.

### 8.3 Авторизация

API защищён `require_user_or_internal`:

- логин/пароль админа — из `.env` (`ZAKUPKI_ADMIN_USERNAME` / `ZAKUPKI_ADMIN_PASSWORD`);
- служебные вызовы конвейера — заголовок `X-Internal-Token: <ZAKUPKI_INTERNAL_TOKEN>`
  (только внутренние сервисы; наружу его не отдавать).

### 8.4 Reverse-proxy + HTTPS

Проще всего Caddy (автосертификат Let's Encrypt). На хосте — `/etc/caddy/Caddyfile`:

```
example.com {
    reverse_proxy localhost:8000
}
langfuse.example.com {
    reverse_proxy localhost:3001
}
```

```bash
docker run -d --name caddy -p 80:80 -p 443:443 \
  -v /etc/caddy/Caddyfile:/etc/caddy/Caddyfile \
  caddy
```

- Домен → A-запись на IP сервера (DNS нужен для выпуска сертификата).
- Наружу пробрасываются только 80/443; `api` остаётся на внутреннем :8000.
- Для nginx/traefik делается аналогично: upstream `127.0.0.1:8000`.

Проверка: `https://example.com/`, `https://example.com/docs` (Swagger). Сертификат держит
reverse-proxy; контейнеры остаются на HTTP внутри.

> Не открывайте наружу БД/`scoring-transport`/MinIO — это внутренние сервисы.

---

## 9. Частые ошибки и решение

| Симптом | Причина | Решение |
|---|---|---|
| `unknown flag: --project-name` / `docker: unknown command: docker compose` | нет Compose v2 | установить Compose v2 (§1) |
| `permission denied … /var/run/docker.sock` | юзер вне группы `docker` | `usermod -aG docker <user>` + перелогин (§2) |
| `sudo: I'm sorry <user>…` / `su: Authentication failure` | Ubuntu блокирует пароль root, sudo не назначен | заходить root-ом по SSH-ключу / панели (§2) |
| `fatal: detected dubious ownership in repository` | git-репозиторий не с владельцем пользователя | `git config --global --add safe.directory <path>` (§3) |
| `Ошибка: ZAKUPKI_INTERNAL_TOKEN не задан в docker/.env` | не заполнен обязательный токен | заполнить §4.1 |
| `Ошибка: порт 5432 занят Docker-контейнером(ами)` | локальный контейнер занимает порт | `./scripts/compose.sh free-port 5432` или поменять `ZAKUPKI_DB_PORT` |
| `docker pull`/`доступ к registry` не проходит | нет доступа к Docker Hub | проверить сеть/прокси; задать registry-mirror |

---

## 10. Отключение / перезапуск

```bash
./scripts/compose.sh down                # остановить и удалить контейнеры (том БД сохранён)
./scripts/compose.sh start               # поднять остановленные (если не удалялись)
./scripts/compose.sh restart             # перезапуск
./scripts/compose.sh logs -f api         # следить за логами
```
