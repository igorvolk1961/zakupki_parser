# zakupki-parser

Парсер площадок закупок на **Playwright** с полной конфигурацией через YAML.
Собирает закупки (44-ФЗ / 223-ФЗ и коммерческие тендеры), сохраняет в **PostgreSQL**
и оповещает подписчиков. Эталонная площадка первого конфига — Портал поставщиков
Москвы (`zakupki.mos.ru`), тематика — ИТ-услуги.

## Возможности
- Движок парсинга, настраиваемый через 5 YAML-конфигов (см. `configs/`).
- Сортировка по убыванию даты обновления + применение фильтров (`config_filters.yaml`).
- Цикл по страницам и записям с остановкой по порогу даты / концу пагинации.
- Набор флагов-условий прекращения обработки заявки (`stop_conditions`).
- Антиблок-меры: полноценный Chromium, stealth, вежливые задержки (4–12 с), лимиты,
  персистентная сессия.
- Хранилище: SQLAlchemy 2.x (async) + PostgreSQL, миграции Liquibase.
- Защита от повторной записи заявки с тем же номером.
- Circuit Breaker и вежливая деградация при отказе БД/сайта.
- Таймерный запуск по списку сайтов, webhook (заглушка), логирование.
- Линтеры (ruff, mypy), тесты, GitHub Actions CI, Docker.

## Структура
```
configs/                       # YAML-конфигурация парсера
src/zakupki_parser/
  cli.py                       # CLI (check-config, run-once, run-service, capture-fixture)
  scheduler.py                 # таймерный цикл по сайтам
  parser/                      # оркестратор, lister, extractor, detail, filters
  browser/                     # менеджер браузера, stealth, задержки
  storage/                     # SQLAlchemy (БД), last_seen
  circuit.py                   # circuit breaker
  notify.py                    # webhook (заглушка)
  file_processor.py            # обработка файлов (заглушка)
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
```bash
# проверить конфигурацию
uv run zakupki-parser --configs configs check-config

# один проход по всем площадкам
uv run zakupki-parser --configs configs run-once

# периодический запуск по таймеру (timeout_seconds из config_service.yaml)
uv run zakupki-parser --configs configs run-service

# пересоздать HTML-фикстуры для тестов
uv run zakupki-parser --configs configs capture-fixture --platform zakupki_mos
```

## Конфигурация
- `config_parser.yaml` — браузер и антиблок-меры.
- `config_dom.yaml` — URL, переменные, селекторы контейнеров и значений.
- `config_filters.yaml` — фильтры и порядок их применения, поле сортировки.
- `config_service.yaml` — таймер, список сайтов, пороги дат, флаги, БД, webhook,
  stop-условия, circuit breaker.
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
