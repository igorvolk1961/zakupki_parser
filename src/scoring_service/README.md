# scoring_service — LLM-скоринг закупок

Независимый подпроект: LLM-сервис скоринга закупок на **Langchain** с интеграцией
**LangFuse**. Вычисляет `Fit` (0–10) по описанию закупки и компетенциям поставщика,
затем `Score = Fit × P(win) × Margin`.

**Границы.** Все файлы — только внутри `src/scoring_service/`. Сервис НЕ использует
БД парсера: вся информация приходит через REST API (парсер) и Redis-очередь.

## Поток
```
card + competencies
  ├─ extract description   (subject + detail_json; при requires_tz_review — текст ТЗ)
  ├─ fit-chain:  reasoning + fit_score (0–10)   [few-shot + negative-example]
  ├─ (опц.) tz_review: уточнение по тексту ТЗ, повторный fit/judge
  ├─ judge-chain: critics / verdict / final_fit_score
  ├─ (опц.) embedding branch: косинусная близость Giga Embedder, смешивание через alpha
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

## Уточнение по ТЗ (`tz_review`)
Если fit запросил (`requires_tz_review`), `TzReviewer` ищет файл ТЗ в карточке закупки,
извлекает его текст (скачивание) и выполняет повторный fit/judge по расширенному описанию.
Флаги `requires_tz_review` / `requires_tz_body` фиксируют неполноту описания; включается
целиком флагом `tz_review_enabled` (`SCORE_TZ_REVIEW_ENABLED`, по умолчанию `true`).

## Параллельная ветка Giga Embedder
`modules/giga_embedder.py`: `GigaTokenProvider` (OAuth 2.0 `client_credentials`, обязательный
заголовок `RqUID`, автообновление токена) и `GigaEmbedder` (модель `EmbeddingsGigaR`, окно
4096 токенов; длинные тексты режутся на чанки и усредняются). Ветка считает косинусную
близость эмбеддингов текста компетенций и описания закупки в отдельном потоке параллельно
с LLM-пайплайном. Влияние на score — через `giga_embedding_alpha` (`SCORE_GIGA_EMBEDDING_ALPHA`,
0.0 = только диагностика); результат пишется в БД парсера как `embedding_similarity` и в
метаданные LangFuse-трейса. Без ключа доступа ветка не выполняется (факт пропуска
фиксируется, падения нет).

## LangFuse
`llm_factory.langfuse_handler` строит `langfuse.CallbackHandler` из env. Каждый вызов
имеет `run_name` (`fit_scoring`/`judge_scoring`) и `metadata` с гиперпараметрами
(`llm_model`, `llm_temperature`, `llm_structured_method`, `num_refine_rounds`,
`normalize_fit_for_score`, `p_win`, `margin_rate`) и идентификаторами
(`procurement_id`, `run_id`). Все задания одного запуска объединяются в **одну
LangFuse-сессию** (`session_id = run_id`): воркер создаёт один `run_id` на время
жизни процесса, `score-csv`/`evaluate` — один на весь прогон, CLI `score` и HTTP
`POST /score` — один на вызов (либо из запроса). Если `run_id` не задан, сессией
служит `procurement_id`. Из-за особенностей langfuse 4.x LangChain-callback
`session_id` передаётся через зарезервированный ключ `metadata["langfuse_session_id"]`
(`config["session_id"]` игнорируется). Если LangFuse не настроен — вызовы идут
без трассировки (dev-режим).

### Self-hosted в Docker (dev)
В `docker/docker-compose.yml` стек LangFuse v4 (+ ClickHouse) за compose-профилем `langfuse`:
postgres, clickhouse, MinIO, web, worker. Профиль **включён по умолчанию** для локального
`docker compose up` (переменная `COMPOSE_PROFILES=langfuse` в `docker/.env`), поэтому при
разработке LangFuse поднимается автоматически; отключить — `COMPOSE_PROFILES= docker compose up ...`.
Боевой `scripts/compose.sh up` профиль отключает (LangFuse не поднимается); включить —
`scripts/compose.sh up --langfuse`.

Dev-стек `scripts/run_all.sh` поднимает LangFuse по умолчанию (`SKIP_LANGFUSE=1` — пропустить).
UI: http://localhost:3000 (логин/пароль из `LANG_ADMIN_PASSWORD` в `docker/.env`). Проект `zakupki`
создаётся при первом старте с ключами из `LANGFUSE_INIT_*`. Для локальной трассировки в
`src/scoring_service/.env` заданы `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST=http://localhost:3000`;
чтобы шли реальные вызовы, заглушка должна быть выключена (`SCORE_USE_STUB=false`).

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
# сохранить baseline (для regression-гейта)
uv run python -m scoring_service evaluate --dataset data/dataset.example.json --out baseline.json
# сравнить текущий прогон с baseline; ненулевой код выхода при деградации метрик
uv run python -m scoring_service evaluate --dataset data/dataset.example.json --compare baseline.json

# FastAPI: GET /health, POST /score
uv run python -m scoring_service serve --port 8100
```

`evaluate` считает непрерывные метрики (MAE, RMSE, accuracy@tol, Pearson, Spearman, bias, WAPE),
бинарные метрики решения судьи accept/reject (precision/recall/F1/confusion, precision@K через
`--precision-k`) и, при `--repeat N`, консистентность пайплайна (std скора и долю нестабильных
verdict — дорого, используйте на ограниченном наборе). Бинарная метка выводится из `expected_fit`
порогом `--accept-threshold` (по умолчанию 5.0) либо берётся из явного поля `expected_verdict`.
`--compare baseline.json` печатает дельты к сохранённому отчёту и возвращает код 1 при деградации
сильнее порогов `--max-mae-reg`/`--max-rmse-reg`/`--max-acc-reg`/`--min-spearman-reg`.

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
