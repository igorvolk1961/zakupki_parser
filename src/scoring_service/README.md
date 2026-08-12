# scoring_service — LLM-скоринг закупок

Независимый подпроект: LLM-сервис скоринга закупок на **Langchain** с интеграцией
**LangFuse**. Вычисляет `Fit` (0–10) по описанию закупки и компетенциям поставщика,
затем `Score = Fit × P(win) × Margin`.

**Границы.** Все файлы — только внутри `src/scoring_service/`. Сервис НЕ использует
БД парсера: вся информация приходит через REST API (парсер) и Redis-очередь.

## Поток
```
card + competencies
  ├─ extract description   (subject + detail_json; без текстов документов)
  ├─ fit-chain:  reasoning + fit_score (0–10)   [few-shot + negative-example]
  ├─ judge-chain: critics / verdict / final_fit_score
  └─ Score = final_fit_score × P(win) × Margin
```
Вход в асинхронном режиме — Redis ZSET `scoring:jobs` (приоритет = дефолтный score,
`ZPOPMAX`), выход — `scoring:results` (LIST). Транспорт (см. `../scoring_transport/`)
обеспечивает постановку задач и возврат результата в парсер.

## Промпты
`pipeline/prompts.py` реализует:
- **few-shot** — позитивные примеры «описание ↔ компетенции → эталон reasoning + score»;
- **negative-example** — примеры false-friend (термины совпадают, смысл разный:
  «аппаратура связи» vs «аппаратура звукозаписи») и синонимичной близости.

Обязательные этапы рассуждений (`ReasoningSteps`): `procurement_essence`,
`competencies_essence`, `relevant_competencies`, `term_overlap_mismatch_check`,
`synonym_semantic_bridge`, `uncovered_scope`, `fit_score_rationale`.

## LangFuse
`llm_factory.langfuse_handler` строит `langfuse.CallbackHandler` из env. Каждый вызов
имеет `run_name` (`fit_scoring`/`judge_scoring`) и `metadata.procurement_id`. Если
LangFuse не настроен — вызовы идут без трассировки (dev-режим).

## Запуск (из каталога подпроекта)
```bash
uv sync --group dev

# фоновый воркер Redis-очереди
uv run python -m scoring_service worker

# разовый скоринг карточки (JSON) + компетенции
uv run python -m scoring_service score card.json --competencies data/competencies.md

# отладка пайплайна на выгрузке БД (CSV): таблица + JSON-отчёт; LLM по умолчанию
uv run python -m scoring_service score-csv --csv ../../data/export/procurements.csv --limit 5
uv run python -m scoring_service score-csv --stub          # заглушка для сверки

# оценка точности на тестовом наборе (пары: описание — скор)
uv run python -m scoring_service evaluate --dataset data/dataset.example.json

# FastAPI: GET /health, POST /score
uv run python -m scoring_service serve --port 8100
```

## Переменные окружения (`SCORE_*`, `LANGFUSE_*`)

Сервис читает конфигурацию из **YAML-файла `config.yaml`** (в корне подпроекта), значения
которого можно переопределить переменными окружения. Приоритет (от высшего к низшему):
`SCORE_*` env → `.env` → `config.yaml` → значения по умолчанию. Путь к файлу задаётся
env `SCORE_CONFIG_FILE` (по умолчанию `config.yaml`).

| Переменная | Назначение |
|---|---|
| `SCORE_LLM_BASE_URL` / `SCORE_LLM_API_KEY` / `SCORE_LLM_MODEL` | OpenAI-совместимая LLM |
| `SCORE_PARSER_API_URL` | адрес REST API парсера (по умолчанию `http://localhost:8000`) |
| `SCORE_REDIS_URL` | адрес Redis (по умолчанию `redis://localhost:6379/0`) |
| `SCORE_P_WIN` / `SCORE_MARGIN_RATE` | стубы P(win)/Margin (дефолтный подход парсера) |
| `SCORE_COMPETENCIES_FILE` | файл с компетенциями поставщика |
| `SCORE_NUM_REFINE_ROUNDS` | число итераций refine при `verdict=reject` |
| `SCORE_USE_STUB` | заглушка: возвращать score, уже присутствующий в данных закупки, без LLM-пайплайна (по умолчанию `false`) |
| `SCORE_NORMALIZE_FIT_FOR_SCORE` | приводить Fit (0–10) к шкале 0–1 при расчёте Score (по умолчанию `true`) |
| `SCORE_AUTH_TOKEN` | опциональный Bearer-токен для `POST /score` (пусто = открыто) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | LangFuse |

Надёжность очереди: Redis даёт at-most-once; воркер при старте/в цикле возвращает
в `scoring:jobs` «зависшие» задачи из `scoring:processing` (аренда истекла,
`SCORE_PROCESSING_TTL_SECONDS`, восстановление с приоритетом
`SCORE_PROCESSING_RECOVERY_PRIORITY`). Скоринг идемпотентен через `POST /score`.

## Тесты / линтеры
```bash
uv run pytest
uv run ruff check .
uv run mypy
```

## Известные особенности
- Шкала Fit — 0–10; для совместимости с дефолтной шкалой парсера (Fit 0–1) при расчёте
  `Score` Fit приводится к 0–1 (`SCORE_NORMALIZE_FIT_FOR_SCORE=true` по умолчанию).
- Redis не гарантирует доставку; компенсируется идемпотентностью `POST /score` и
  восстановлением «зависших» задач из `scoring:processing`. При росте числа
  потребителей — переход на RabbitMQ.
