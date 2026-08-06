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
  `GET /api/procurements/{id}` (карточка), `POST /{id}/score` (внешний скоринг),
  `POST /{id}/technical-spec` и `GET /{id}/technical-spec` (ТЗ).
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

## База данных (PostgreSQL)

Поднять БД одной командой:

```bash
./scripts/db_up.sh          # поднять БД (существующую или создать новую с миграциями)
./scripts/db_up.sh --status # статус контейнера и таблиц
```

Скрипт использует контейнер `zakupki_db` (данные хранятся в volume и сохраняются
между сессиями). Если контейнера ещё нет — создаёт его и автоматически применяет
миграции Liquibase.

## Запуск

Команда CLI — `zp` (сокращение от `zakupki-parser`; длинное имя доступно как алиас).

Сначала поднимите БД (см. выше): `./scripts/db_up.sh`. Затем:

```bash
# проверить конфигурацию
uv run zp --configs configs check-config

# один проход по всем площадкам
uv run zp --configs configs run-once

# периодический запуск по таймеру (timeout_seconds из config_service.yaml)
uv run zp --configs configs run-service

# разовый запуск воркера внешнего скоринга (score_method=default -> external)
uv run zp --configs configs score-worker

# FastAPI-сервис (health, списки закупок, скачивание ТЗ, web-демо)
uv run zp --configs configs serve --host 0.0.0.0 --port 8000

# пересоздать HTML-фикстуры для тестов
uv run zp --configs configs capture-fixture --platform zakupki_mos
```

### Web-демо (MVP)

`zp serve` отдаёт простое web-приложение по адресу `http://localhost:8000/`:

- **Закупки / Заказчики** — просмотр данных из БД (карточки, детали, справочник
  заказчиков с ИНН/рейтингом). Приложение **не зависит от источника данных** — ему
  безразлично, откуда приходят закупки: живой парсер, имитатор ЭТП или иное наполнение.
- **Конфиг-сервис** — удобный просмотр и редактирование параметров
  `config_service.yaml` (JSON-редактор + сохранение). Секреты (токены ботов) через
  API не редактируются — они берутся из env. Изменения применяются при следующем
  запуске парсера.

```bash
uv run zp --configs configs serve --host 0.0.0.0 --port 8000
# открыть http://localhost:8000/
```


## Остановка

Остановить запущенные процессы парсера (`run-once`, `run-service`, `score-worker`,
`serve`) и их браузерные процессы (Playwright/Chromium):

```bash
# мягкая остановка (SIGINT — корректное закрытие браузера)
uv run zp --configs configs stop

# принудительная остановка (SIGKILL), если мягкая не сработала
uv run zp --configs configs stop --force
```

Требуется `pgrep` (пакет `procps`). Для одного процесса на переднем плане также
работает `Ctrl+C` в терминале.

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
  уведомления (telegram/max/webhook), stop-условия, circuit breaker.
- `config_score.yaml` — скоринг: метод (default/external), fit-таблица ОКПД2,
  адрес внешнего сервиса и способ его вызова (before_save/worker).
- `config_log.yaml` — логирование.

Переменные окружения (для Docker/CI):
- `ZAKUPKI_CONFIGS` — каталог конфигов;
- `ZAKUPKI_DB_DSN` — DSN БД (переопределяет `config_service.yaml -> db.dsn`).

## Docker
```bash
docker compose -f docker/docker-compose.yml up --build
```
Запустит PostgreSQL, применит Liquibase-миграции и поднимет сервис парсера.

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
