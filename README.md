# zakupki-parser

Парсер площадок закупок (zakupki.mos.ru, ЕИС и др.) с веб-интерфейсом.

> 📖 **Руководство пользователя:** [docs/user-guide.md](docs/user-guide.md)

## Дорожная карта: перестройка в TenderSearch

Проект развивается в мультитенантный SaaS «TenderSearch» (по `docs/system_analisys/`):
per-user аккаунты и профили фильтрации, изоляция данных по `user_id` (BR-07),
глубокая проверка ТЗ с маркерами, экспорт XLSX, compliance и наблюдаемость.
Текущее состояние — v0.3.0; целевая модель — в разделе
[14. Целевая архитектура (TenderSearch)](specification.md#14-целевая-архитектура-tendersearch)
`specification.md`. Трекер этапов — [plans/plan.md](plans/plan.md); детальные планы —
`plans/NN_plan.md` (этап 0 — [plans/00_plan.md](plans/00_plan.md)).
Порядок этапов 0–10 и зафиксированные решения (R1–R9) — в мастер-плане
`.kilo/plans/1787250023996-architecture-multitenancy-master-plan.md` (локальный).

Парсер площадок закупок на **Playwright** с полной конфигурацией через YAML.
Собирает закупки (44-ФЗ / 223-ФЗ и коммерческие тендеры), сохраняет в **PostgreSQL**
и оповещает подписчиков. Поддерживаемые площадки — Портал поставщиков
Москвы (`zakupki.mos.ru`), ЕИС (`zakupki.gov.ru`, 44-ФЗ/223-ФЗ) и коммерческие ЭТП
(Фабрикант, B2B-Center, ЭТП ГПБ, lot-online/РАД, Росэлторг — статус верификации
и `enabled` каждой площадки см. в [docs/platforms.md](docs/platforms.md)),
тематика — ИТ-услуги.

## Возможности
- Движок парсинга, настраиваемый через 6 YAML-конфигов (см. `configs/`: parser,
  service, ops, score, log + `dom/`).
- Сортировка по убыванию даты публикации (порядок фиксирован: `publication_date_desc`)
  или по релевантности (`sort.by_relevance=true` — без стоп-порога по дате);
  глобальный режим `config_service.yaml -> sort_by_date_only` сортирует ВСЕ площадки
  по дате (по дате обновления, если площадка её поддерживает, иначе по дате
  публикации) со стоп-порогом MAX даты из БД площадки;
   фильтрация: URL-фильтр (`configs/dom/<platform_id>.yaml -> search`) и DOM-шаги (`filters`).
- Цикл по страницам и записям с остановкой по порогу даты / концу пагинации;
  пагинация — кликом по кнопке или через query-параметр `page=N` (`page_param`);
  потолок страниц за проход (`parser.max_list_pages`) — защита от вечного цикла.
- Оптимизация повторного прохода (relevance-режим): пропуск уже сохранённых закупок
  (детальные страницы не открываются) и ранний пропуск прохода, когда в БД записей
  площадки не меньше, чем нашёл поиск (`total_results_selector`/`total_results_regex`).
- Набор флагов-условий прекращения обработки закупки (`stop_conditions`).
- Антиблок-меры: полноценный Chromium, stealth, вежливые задержки (4–12 с), лимиты,
  персистентная сессия, ретраи с экспоненциальным backoff.
- Хранилище: SQLAlchemy 2.x (async) + PostgreSQL, миграции Liquibase.
- Файлы закупки (в т.ч. техническое задание) — в БД сохраняются только метаданные
  (имя и URL скачивания с ЭТП) в `files_json`; парсер не скачивает файлы.
- **FastAPI-сервис**: `GET /health`, `GET /api/procurements` (список/фильтры,
  включая `active`/`min_fit_score`, серверная сортировка и пагинация),
  `GET /api/procurements/{id}` (карточка),
  `POST /{id}/score` (возврат результата стадии каскада скоринга из транспорта +
  постадийное пороговое уведомление),
  управление парсером
  (`/api/parser/start|stop|status`), очистка БД (`/api/db/clear` — всё,
  `/api/db/clear-inactive` — неактивные, `/api/db/clear-irrelevant` — нерелевантные
  по fit-порогу), выгрузка CSV (`/api/procurements/export`), конфиг
  (`/api/config`, `/api/config/threshold`), WebSocket `/ws`.
- **Асинхронный внешний скоринг каскадом Fit → P(win) → Margin** (ADR-7/ADR-9):
  после сохранения закупки парсер автоматически передаёт задание в `scoring_transport`,
  тот ставит его в Redis-очередь по дефолтному скору, `scoring_service` считает **Fit**
  по **LLM-пайплайну** (Fit → Judge → refine → уточнение по ТЗ → ветка Giga-эмбеддингов);
  если `fit_score ≥ pwin_fit_threshold` — закупка ставится в очередь **P(win)**
  (`pwin_service`), если `p_win ≥ margin_pwin_threshold` — в очередь **Margin**
  (`margin_service`); результат каждой стадии возвращается через транспорт.
  Уведомление подписчиков — **после каждой стадии** (fit/pwin/margin) с порогом по
  возвращаемому значению стадии (`notify_min_fit_score` / `notify_min_pwin` /
  `notify_min_margin`, флаги `notify_*_enabled` в `config_ops.yaml`).
- Защита от повторной записи закупки с тем же номером.
- Circuit Breaker и вежливая деградация при отказе БД/сайта.
- Таймерный запуск по списку сайтов, уведомления подписчиков
  (Telegram / MAX / webhook), логирование.
- Линтеры (ruff, mypy), тесты, GitHub Actions CI, Docker.

## Структура
```
configs/                       # YAML-конфигурация парсера
src/zakupki_parser/
  cli.py                       # CLI (check-config, run-once, run-service, serve, capture-fixture, seed-profile)
  scheduler.py                 # таймерный цикл по сайтам
  api/                         # FastAPI-сервис (health, procurements, ТЗ)
  parser/                      # оркестратор, lister, extractor, detail, filters
  browser/                     # менеджер браузера, stealth, задержки
  storage/                     # SQLAlchemy (БД), customers
  circuit.py                   # circuit breaker
  notify.py                    # уведомления (telegram / max / webhook)
src/scoring_service/           # стадия Fit каскада: LLM-скоринг (Fit → Judge → refine → ТЗ → Giga), Redis-воркер
src/scoring_transport/         # gateway скоринга: ingest (POST /api/scoring/jobs), Redis-очереди, возврат результата
src/pwin_service/              # стадия P(win) каскада: вероятность победы (Redis-воркер)
src/margin_service/            # стадия Margin каскада: маржа (НМЦК × margin_rate, Redis-воркер)
src/scoring_common/            # общий код стадий: очередь, клиент API парсера, формула P(win)
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

## Запуск

Команда CLI — `zp` (сокращение от `zakupki-parser`; длинное имя доступно как алиас).
Сначала поднимите фоновый стек (БД + Redis + воркеры каскада
`scoring_service`/`pwin_service`/`margin_service` +
`scoring_transport`), затем запустите парсер:

```bash
./scripts/run_all.sh                     # фоновый стек (работает в этом терминале)
# в другом терминале:
uv run zp --configs configs serve --host 0.0.0.0 --port 8000
# открыть http://localhost:8000/
```

`run_all.sh` держит фоновые сервисы живыми (Ctrl+C — останавливает). Перед стартом он
сам закрывает зависшие сервисы от прошлой сессии (чтобы порт `scoring_transport` 8200
не оставался занят). Аккуратно остановить всё — `./scripts/run_all.sh stop`
(останавливает сервисы скоринга + LangFuse + Redis + PostgreSQL).
Транспорт скоринга поднимается и ожидает готовности до того, как вы запустите парсер, — так
авто-пуш заданий на внешний скоринг не теряется и уведомления доходят до подписчиков.

Парсер запускается из web-демо кнопкой «▶ Запустить» — это **постоянный мониторинг**
(периодические проходы по площадкам, эндпоинт `POST /api/parser/start`); остановка —
кнопка «■ Остановить» или CLI `stop`. Отдельные CLI-команды `run-once` / `run-service`
нужны только для запуска без API — см. раздел «Утилиты».

### Web-демо (MVP)

`zp serve` отдаёт простое web-приложение по адресу `http://localhost:8000/`:

- **Закупки / Заказчики** — просмотр данных из БД (карточки, детали, справочник
  заказчиков с ИНН/рейтингом). Приложение **не зависит от источника данных** — ему
  безразлично, откуда приходят закупки: живой парсер, имитатор ЭТП или иное наполнение.
- **Выгрузка CSV** (режим администратора) — кнопка «Выгрузить CSV» пишет закупки
  из БД в CSV-файл на сервере в каталог `config_ops.yaml -> export_dir`
  (по умолчанию `data/export/procurements.csv`, кодировка UTF-8 с BOM — открывается в Excel).
- **Параметры** — удобный просмотр и редактирование **аналитических** параметров
  `config_service.yaml` (JSON-редактор + сохранение). Секреты и эксплуатационные
  параметры (БД, уведомления, таймер — `config_ops.yaml`) через API не редактируются —
  они берутся из env. Изменения применяются при следующем запуске парсера.

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

## Инфраструктура (PostgreSQL + Redis + LangFuse)

В локальном запуске (вне контейнера) контейнерами Docker являются БД, Redis и LangFuse;
`scoring_service`, `pwin_service` и `margin_service` поднимаются как локальные `uv`-процессы
(`scripts/run_all.sh`, см. «Запуск»). Контейнеры:

- `zakupki_db` — PostgreSQL: данные и миграции (Liquibase) применяются автоматически
  (через `scripts/db_up.sh`).
- `zakupki_redis` — Redis: нужен конвейеру внешнего скоринга (`scoring_transport` +
  стадии `scoring_service`/`pwin_service`/`margin_service`, очереди
  `scoring:jobs`/`scoring:results`, `pwin:jobs`/`pwin:results`, `margin:jobs`/`margin:results`).
- LangFuse (compose-профиль `langfuse`, UI `http://localhost:3000`) — трассировка
  LLM-вызовов `scoring_service`; поднимается `run_all.sh` по умолчанию, отключается
  `SKIP_LANGFUSE=1 scripts/run_all.sh`. Останавливается `scripts/run_all.sh stop`.

Данные контейнеров хранятся в volume и сохраняются между сессиями. Если контейнера
нет — он создаётся и ждёт готовности; если есть — просто запускается (идемпотентно).

В Docker-варианте всё, включая парсер, `scoring_transport` и все стадии каскада
(`scoring_service`/`pwin_service`/`margin_service`), —
также контейнеры; весь стек описан одним манифестом `docker/docker-compose.yml`
(см. раздел «Docker»).

## Утилиты (разработка и тесты)

Запуск парсера без API — альтернативы:

```bash
# проверить конфигурацию
uv run zp --configs configs check-config

# запуск парсера (headless, достаточно одной):
uv run zp --configs configs run-once        # один проход по всем площадкам
uv run zp --configs configs run-service     # периодически по таймеру (timeout_seconds)
```

Заполнить default-профиль пользователя ключевыми словами/компетенциями из файла
(по умолчанию — `data/profile.md`, пользователь `admin`):

```bash
uv run zp --configs configs seed-profile --user admin --file data/profile.md
```

Файл `data/profile.md` содержит секции `**name**`, `**keywords**`, `**exclussion_words**`,
`**competencies**` (компетенции могут быть ссылкой на файл, например
`docs/references/bbk-it-site.md`). Ключевые слова записываются в таблицу `keywords`
(канонический источник; синтаксис `слов*` / `(фраза* фраза*)~N`).

Пересоздать HTML-фикстуры для тестов:

```bash
uv run zp --configs configs capture-fixture --platform zakupki_mos
```

Дополнительные скрипты:
- `scripts/run_all.sh` — фоновый стек (БД + Redis + воркеры каскада `scoring_service`/
  `pwin_service`/`margin_service` + `scoring_transport`);
- `scripts/db_up.sh` — только PostgreSQL (данные и миграции), если нужно поднять
  БД без остального стека:
  ```bash
  ./scripts/db_up.sh          # поднять БД (существующую или создать новую с миграциями)
  ./scripts/db_up.sh --status # статус контейнера и таблиц
  ```
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
- `dom/` — конфигурация площадок, по одному YAML на площадку
  (`configs/dom/<platform_id>.yaml`): URL, переменные, селекторы контейнеров и значений,
  а также селекторы сортировки и фильтров (блоки `sort`/`filters`) и URL-фильтр `search`
  (в т.ч. `okpd_codes` + маппинг `okpd_tree_file`).
- `config_service.yaml` — **аналитические** настройки: список сайтов, порог дат,
  критерии поиска, stop-условия (редактируется через web-интерфейс).
- `config_ops.yaml` — **эксплуатационные** настройки (devops): таймер, БД, уведомления
  (telegram/max/webhook, постадийные пороги и флаги `notify_min_fit_score`/
  `notify_min_pwin`/`notify_min_margin`, `notify_{fit,pwin,margin}_enabled`), каталог
  выгрузки CSV, circuit breaker.
- `config_score.yaml` — скоринг: fit-таблица ОКПД2, параметры каскада внешнего скоринга
  (`scoring_transport` + `scoring_service` + `pwin_service` + `margin_service` + Redis, ADR-7/ADR-9):
  пороги переходов `pwin_fit_threshold`/`margin_pwin_threshold`, флаги `pwin_enabled`/
  `margin_enabled`; приоритет очереди = дефолтный score парсера.
- `config_log.yaml` — логирование.

Переменные окружения (для Docker/CI):
- `ZAKUPKI_DB_DSN` — DSN БД (переопределяет `config_service.yaml -> db.dsn`);
- `ZAKUPKI_SCORING_TRANSPORT_URL` — адрес `scoring_transport` (в Docker — имя сервиса
  `http://scoring-transport:8200`, в локальном запуске — `http://localhost:8200`);
- `ZAKUPKI_NOTIFY_BACKEND` — бэкенд уведомлений; `none` полностью отключает
  оповещения (в `docker/docker-compose.yml` задано `none`);
- секреты уведомлений — берутся из файла `.env` в корне проекта (см. `env_file: ../.env` в `docker/docker-compose.yml`):
  `ZAKUPKI_TELEGRAM_TOKEN`, `ZAKUPKI_MAX_TOKEN`, `ZAKUPKI_MAX_CHAT_ID`.

## Docker
```bash
docker compose -f docker/docker-compose.yml up --build
```
Запустит единый стек одной командой: PostgreSQL + Liquibase-миграции + Redis +
`scoring_service` (воркер стадии Fit) + `scoring_transport` + `pwin_service` +
`margin_service` + `parser` (периодический обход) +
`api` (FastAPI на `http://localhost:8000/`). Сервисы связаны по имени (api ↔
`scoring-transport` ↔ redis), поэтому конвейер каскада скоринга (Fit → P(win) →
Margin) и возврат результата в `POST /score` работают из коробки. Команду запускать
из корня репозитория —
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
Сводка по торговым площадкам (статус верификации, фильтрация/сортировка) — в
[docs/platforms.md](docs/platforms.md).
Текущие незавершённые работы — в [TODO.md](TODO.md). Диаграммы — в [docs/c4](docs/c4/).
Настройка Telegram-подписчика — в [docs/telegram-subscriber.md](docs/telegram-subscriber.md).
Настройка MAX-подписчика — в [docs/max-subscriber.md](docs/max-subscriber.md).
