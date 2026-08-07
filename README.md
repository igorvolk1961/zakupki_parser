# zakupki-parser

Парсер площадок закупок (zakupki.mos.ru, ЕИС и др.) с веб-интерфейсом.

> 📖 **Руководство пользователя:** [docs/user-guide.md](docs/user-guide.md)

Парсер площадок закупок на **Playwright** с полной конфигурацией через YAML.
Собирает закупки (44-ФЗ / 223-ФЗ и коммерческие тендеры), сохраняет в **PostgreSQL**
и оповещает подписчиков. Поддерживаемые площадки — Портал поставщиков
Москвы (`zakupki.mos.ru`) и ЕИС (`zakupki.gov.ru`, 44-ФЗ/223-ФЗ), тематика — ИТ-услуги.

## Возможности
- Движок парсинга, настраиваемый через 5 YAML-конфигов (см. `configs/`).
- Сортировка по убыванию даты публикации (порядок фиксирован: `publication_date_desc`)
  + фильтрация: URL-фильтр (`config_dom.yaml -> search`) для zakupki.mos.ru и/или
  DOM-шаги (`filters`) для других площадок.
- Цикл по страницам и записям с остановкой по порогу даты / концу пагинации.
- Набор флагов-условий прекращения обработки заявки (`stop_conditions`).
- Антиблок-меры: полноценный Chromium, stealth, вежливые задержки (4–12 с), лимиты,
  персистентная сессия, ретраи с экспоненциальным backoff.
- Хранилище: SQLAlchemy 2.x (async) + PostgreSQL, миграции Liquibase.
- Файлы закупки (в т.ч. техническое задание) — в БД сохраняются только метаданные
  (имя и URL скачивания с ЭТП); парсер не скачивает файлы.
- **FastAPI-сервис**: `GET /health`, `GET /api/procurements` (список/фильтры),
  `GET /api/procurements/{id}` (карточка), `POST /{id}/score` (возврат результата скоринга
  из транспорта + пороговое уведомление), `POST /{id}/technical-spec` и
  `GET /{id}/technical-spec` (ТЗ).
- **Асинхронный внешний скоринг** (ADR-7): после сохранения закупки парсер автоматически
  передаёт задание в `scoring_transport`, тот ставит его в Redis-очередь по дефолтному
  скору, `scoring_service` (LLM) обрабатывает и возвращает результат через транспорт;
  уведомление подписчиков — только при `score ≥ notify_min_score`.
- Защита от повторной записи заявки с тем же номером.
- Circuit Breaker и вежливая деградация при отказе БД/сайта.
- Таймерный запуск по списку сайтов, уведомления подписчиков
  (Telegram / MAX / webhook), логирование.
- Линтеры (ruff, mypy), тесты, GitHub Actions CI, Docker.

## Структура
```
configs/                       # YAML-конфигурация парсера
src/zakupki_parser/
  cli.py                       # CLI (check-config, run-once, run-service, serve, capture-fixture)
  scheduler.py                 # таймерный цикл по сайтам
  api/                         # FastAPI-сервис (health, procurements, ТЗ)
  parser/                      # оркестратор, lister, extractor, detail, filters
  browser/                     # менеджер браузера, stealth, задержки
  storage/                     # SQLAlchemy (БД), customers
  circuit.py                   # circuit breaker
  notify.py                    # уведомления (telegram / max / webhook)
src/scoring_service/           # LLM-сервис скоринга (Fit → Judge → Score), Redis-воркер
src/scoring_transport/         # gateway скоринга: ingest, Redis-очередь, возврат результата
tests/                         # unit + integration тесты, HTML-фикстуры
docker/                        # Dockerfile, docker-compose, Liquibase
docs/c4/                       # C4-диаграммы (Mermaid)
```

## Требования
- Python 3.12
- [uv](https://docs.astral.sh/uv/) (менеджер зависимостей)
- PostgreSQL (для записи; при отсутствии — сервис работает без БД)

## Установка
```bash
uv sync
uv run playwright install chromium --with-deps
```

## Инфраструктура (PostgreSQL + Redis)

Все контейнеры инфраструктуры (PostgreSQL + Redis) поднимаются одной командой:

```bash
./scripts/services_up.sh          # поднять PostgreSQL + Redis
./scripts/services_up.sh --status # статус всех контейнеров
./scripts/services_up.sh --redis  # только Redis
./scripts/services_up.sh --db     # только PostgreSQL
```

- `zakupki_db` — PostgreSQL: данные и миграции (Liquibase) применяются автоматически.
- `zakupki_redis` — Redis: нужен конвейеру внешнего скоринга (`scoring_transport` +
  `scoring_service`, очередь `scoring:jobs`/`scoring:results`).

Для только БД можно использовать `./scripts/db_up.sh`:

```bash
./scripts/db_up.sh          # поднять БД (существующую или создать новую с миграциями)
./scripts/db_up.sh --status # статус контейнера и таблиц
```

Скрипты используют контейнеры `zakupki_db` и `zakupki_redis` (данные хранятся в
volume и сохраняются между сессиями). Если контейнера нет — создают его и ждут
готовности; если есть — просто запускают (идемпотентно).

## Запуск

Команда CLI — `zp` (сокращение от `zakupki-parser`; длинное имя доступно как алиас).
Поднимите инфраструктуру и запустите сервис:

```bash
./scripts/services_up.sh
uv run zp --configs configs serve --host 0.0.0.0 --port 8000
# открыть http://localhost:8000/
```

Парсер запускается из web-демо кнопкой «▶ Запустить» — это **постоянный мониторинг**
(периодические проходы по площадкам, эндпоинт `POST /api/parser/start`); остановка —
кнопка «■ Остановить» или CLI `stop`. Отдельные CLI-команды `run-once` / `run-service`
нужны только для запуска без API — см. раздел «Утилиты».

### Web-демо (MVP)

`zp serve` отдаёт простое web-приложение по адресу `http://localhost:8000/`:

- **Закупки / Заказчики** — просмотр данных из БД (карточки, детали, справочник
  заказчиков с ИНН/рейтингом). Приложение **не зависит от источника данных** — ему
  безразлично, откуда приходят закупки: живой парсер, имитатор ЭТП или иное наполнение.
- **Параметры** — удобный просмотр и редактирование параметров
  `config_service.yaml` (JSON-редактор + сохранение). Секреты (токены ботов) через
  API не редактируются — они берутся из env. Изменения применяются при следующем
  запуске парсера.

## Остановка

Остановить запущенные процессы парсера (`run-once`, `run-service`,
`serve`) и их браузерные процессы (Playwright/Chromium):

```bash
# мягкая остановка (SIGINT — корректное закрытие браузера)
uv run zp --configs configs stop

# принудительная остановка (SIGKILL), если мягкая не сработала
uv run zp --configs configs stop --force
```

Требуется `pgrep` (пакет `procps`). Для одного процесса на переднем плане также
работает `Ctrl+C` в терминале.

## Утилиты (разработка и тесты)

Запуск парсера без API — альтернативы:

```bash
# проверить конфигурацию
uv run zp --configs configs check-config

# запуск парсера (headless, достаточно одной):
uv run zp --configs configs run-once        # один проход по всем площадкам
uv run zp --configs configs run-service     # периодически по таймеру (timeout_seconds)
```

Пересоздать HTML-фикстуры для тестов:

```bash
uv run zp --configs configs capture-fixture --platform zakupki_mos
```

Дополнительные скрипты:
- `scripts/services_up.sh` — поднять инфраструктуру (PostgreSQL + Redis) и их статус;
- `scripts/db_up.sh` — только PostgreSQL (данные и миграции);
- `scripts/get_max_chat_id.py` / `scripts/test_max_chat.py` — вспомогательные
  утилиты для настройки MAX-уведомлений.

## Уведомления

Доставка новых закупок подписчикам настраивается в `config_service.yaml ->
notifications` (`backend: telegram | max | webhook`). Подробности — в
[docs/max-subscriber.md](docs/max-subscriber.md) и
[docs/telegram-subscriber.md](docs/telegram-subscriber.md).

- **MAX** — работает из РФ без прокси: рекомендован как основной способ.
- **Telegram** — требует доступа к `api.telegram.org` (VPN/прокси). Важно:
  при включённом VPN ЕИС (`zakupki.gov.ru`) может быть недоступен, поэтому для
  одновременной работы Telegram + парсинга ЕИС нужна более сложная конфигурация
  с проксированием обращений к ЕИС.
- **Webhook** — POST JSON-карточки на произвольный URL (при заданном `token` — как
  Bearer-заголовок).

Токены ботов не хранятся в конфиге и задаются через env:
`ZAKUPKI_TELEGRAM_TOKEN` (Telegram), `ZAKUPKI_MAX_TOKEN` (MAX).

## Конфигурация
- `config_parser.yaml` — браузер и антиблок-меры.
- `config_dom.yaml` — URL, переменные, селекторы контейнеров и значений, а также
  селекторы сортировки и фильтров (блоки `sort`/`filters`) и URL-фильтр `search`
  (в т.ч. `okpd_codes` + маппинг `okpd_tree_file`).
- `config_service.yaml` — таймер, список сайтов, пороги дат, флаги, БД,
  уведомления (telegram/max/webhook, порог `notify_min_score`), stop-условия, circuit breaker.
- `config_score.yaml` — скоринг: метод (default/external), fit-таблица ОКПД2, параметры
  конвейера внешнего скоринга (`scoring_transport` + `scoring_service` + Redis, ADR-7);
  приоритет очереди = дефолтный score парсера.
- `config_log.yaml` — логирование.

Переменные окружения (для Docker/CI):
- `ZAKUPKI_DB_DSN` — DSN БД (переопределяет `config_service.yaml -> db.dsn`);
- `ZAKUPKI_NOTIFY_BACKEND` — бэкенд уведомлений; `none` полностью отключает
  оповещения (в `docker/docker-compose.yml` задано `none`);
- секреты уведомлений — берутся из файла `.env` в корне проекта (см. `env_file: ../.env` в `docker/docker-compose.yml`):
  `ZAKUPKI_TELEGRAM_TOKEN`, `ZAKUPKI_MAX_TOKEN`, `ZAKUPKI_MAX_CHAT_ID`.

## Docker
```bash
docker compose -f docker/docker-compose.yml up --build
```
Запустит PostgreSQL, применит Liquibase-миграции и поднимет сервисы `parser` (периодический обход)
и `api` (FastAPI на `http://localhost:8000/`). Команду запускать из корня репозитория —
контекст сборки и файл `.env` резолвятся относительно `docker/docker-compose.yml`.

Для удобства есть скрипт-обёртка над compose-стеком — `scripts/compose.sh`:
```bash
scripts/compose.sh                     # up (собрать + поднять в фоне, --build)
scripts/compose.sh up                  # то же
scripts/compose.sh down                # остановить и удалить контейнеры (том БД сохраняется)
scripts/compose.sh stop                # то же, что down: останавливает и освобождает порты (том БД сохраняется)
scripts/compose.sh start               # запустить остановленные контейнеры (если не удалялись)
scripts/compose.sh restart             # перезапустить
scripts/compose.sh ps                  # статус контейнеров
scripts/compose.sh logs [svc]          # логи (-f), например: logs parser
scripts/compose.sh build               # пересобрать образы
scripts/compose.sh free-port [порт]    # освободить порт (по умолчанию 5432), занятый контейнером
scripts/compose.sh free-port --force   # то же без запроса подтверждения
```
`free-port` пригодится, если порт 5432 занят локальным контейнером БД из `scripts/db_up.sh`
(ошибка `Bind for 0.0.0.0:5432 failed: port is already allocated`) — он остановит контейнер,
данные в volume сохранятся. Перед `up` скрипт сам заметит занятый порт 5432 и спросит,
освободить ли его (при отказе — прервёт запуск).

## Тесты
```bash
uv run pytest                          # все тесты (БД-тесты пропустятся без DSN)
ZAKUPKI_TEST_DSN='postgresql+asyncpg://postgres:postgres@localhost:5433/zakupki_test' uv run pytest
```

## Линтеры
```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
```

Подробности алгоритма и конфигурации — в [specification.md](specification.md).
Текущие незавершённые работы — в [TODO.md](TODO.md). Диаграммы — в [docs/c4](docs/c4/).
Настройка Telegram-подписчика — в [docs/telegram-subscriber.md](docs/telegram-subscriber.md).
Настройка MAX-подписчика — в [docs/max-subscriber.md](docs/max-subscriber.md).
