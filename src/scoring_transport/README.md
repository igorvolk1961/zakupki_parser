# scoring_transport — транспорт скоринга

Независимый подпроект: gateway между **парсером закупок** и **сервисом скоринга**
(LLM). Принимает запросы на скоринг через REST, ставит их в **приоритетную очередь
на Redis (sorted set)** по дефолтному score (сначала наибольший), получает результаты
и возвращает их в парсер через его REST API.

**Границы.** Все файлы — только внутри `src/scoring_transport/`. Транспорт НЕ
использует БД парсера: вся информация — через REST API парсера и Redis.

## Поток
```
[Парсер outbox / CLI / имитатор]
   │  POST /api/scoring/jobs {procurement_id}
   ▼
[scoring_transport]  FastAPI
   │  1) GET  /api/procurements/{id}   (полная карточка)
   │  2) приоритет = card.score (или пересчёт по okpd2/nmck/p_win)
   │  3) ZADD scoring:jobs {priority} proc:{id}
   ▼
[Redis]  scoring:jobs (ZSET)  →  [scoring_service] worker  →  scoring:results (LIST)
   ▼
[scoring_transport]  BRPOP scoring:results → POST /api/procurements/{id}/score
```

Приоритет = **дефолтный score закупки**; потребитель (scoring_service) берёт задачи
`ZPOPMAX` — в первую очередь обрабатываются закупки с наибольшим дефолтным score.

## Запуск (из каталога подпроекта)
```bash
uv sync --group dev

# FastAPI: POST /api/scoring/jobs, GET /health (фоновый consumer результатов внутри)
uv run python -m scoring_transport serve --port 8200

# фоновый consumer результатов отдельным процессом
uv run python -m scoring_transport consumer

# ручная постановка задачи
uv run python -m scoring_transport enqueue 42 --priority 250
```

Полный стек (redis + scoring_service + scoring_transport):
```bash
docker compose -f docker/docker-compose.yml up --build
```

## Переменные окружения (`TRANSPORT_*`)
| Переменная | Назначение |
|---|---|
| `TRANSPORT_REDIS_URL` | адрес Redis (общий с scoring_service) |
| `TRANSPORT_PARSER_API_URL` | адрес REST API парсера |
| `TRANSPORT_COMPETENCIES_FILE` | файл компетенций поставщика (профиль) |
| `TRANSPORT_PRIORITY_DEFAULT` | приоритет по умолчанию, если score неизвестен |
| `TRANSPORT_FIT_TABLE` / `TRANSPORT_DEFAULT_FIT` / `TRANSPORT_P_WIN` | пересчёт приоритета (зеркало config_score.yaml) |
| `TRANSPORT_RETRY_MAX` / `TRANSPORT_RETRY_BACKOFF_SECONDS` | ретраи возврата результата в парсер |
| `TRANSPORT_AUTH_TOKEN` | опциональный Bearer-токен для `POST /api/scoring/jobs` (пусто = открыто) |

## Тесты / линтеры
```bash
uv run pytest
uv run ruff check .
uv run mypy
```
