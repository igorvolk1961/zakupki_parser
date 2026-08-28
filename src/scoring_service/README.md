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
  ├─ (опц.) embedding branch: косинусная близость Giga Embedder
  │     └─ если близость < embedding_filter_threshold — pre-filter: fit_score=0,
  │        score_method=sim, LLM НЕ выполняется
  ├─ fit-chain:  reasoning + fit_score (0–10)   [few-shot + negative-example]
  ├─ (опц.) tz_review: уточнение по тексту ТЗ, повторный fit/judge
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

## Профиль поставщика (строгое разделение слоёв)

Системный промпт **не зависит от пользовательских профилей**: в нём живут только
механика и политика скоринга. Профиль — это отдельный структурированный слой
(`profile.py`): **факты** о поставщике (позиционирование, компетенции, исключения)
плюс два необязательных числовых параметра политики с дефолтами
(`scoring_policy.uncovered_penalty`, `scoring_policy.ambiguous_range`). Заполняется
через форму/UI, а не свободным текстом инструкций; «промпт-инжиниринг» выполняет
`render_profile` — код превращает факты в канонический блок «КОМПЕТЕНЦИИ ПОСТАВЩИКА».

Профиль рендерится в **два разных текста** (`ProfileTexts` / `profile_to_texts`):
- `llm` — полный блок для fit/judge-цепочек: позиционирование, охват, компетенции,
  исключения и не-дефолтные параметры политики;
- `embedding` — текст **только** для ветки векторной близости: позиционирование
  и компетенции, БЕЗ раздела «НЕ входят в компетенции» и политики. Исключения
  описывают, чего компания НЕ делает, — их включение в эмбеддинг смещает вектор
  профиля к нерелевантным темам и даёт ложную близость/удалённость закупок
  (риск отсечь релевантные pre-filter по вектору).

Штатный профиль — `data/profile.yaml` (формат ниже). `SCORE_COMPETENCIES_FILE`
может указывать на любой профиль; CLI-ключ `--competencies` принимает YAML/JSON
(структурированный) либо legacy-markdown (обратная совместимость; legacy-разбор
не теряет текст — нераспознанные секции сохраняются в позиционировании). Значения,
приходящие из парсера (воркер) или REST (`POST /score`), нормализуются
`profile_to_texts`: структурированный профиль рендерится кодом, свободный текст
проходит как есть (в этом случае llm- и embedding-тексты совпадают).

Порог `embedding_filter_threshold` калибруется под embedding-текст профиля
(позитивные факты без исключений). При изменении состава этого текста (например,
правке `render_profile_embedding`) перекалибруйте порог на исторической выборке
и прогоните `evaluate --compare baseline.json` до раскатки.

```yaml
name: "Поставщик"
positioning: "чем занимается, одним-двумя предложениями"
breadth: broad                # broad | narrow — как трактовать неперечисленные кейсы
competencies:
  - area: "Направление"
    description: "что делает компания в этой области"
    examples: ["пример работ"]
exclusions:                   # «НЕ входят в компетенции» — закупки по ним отсеиваются
  - "чего компания НЕ делает"
# scoring_policy:             # необязательно, дефолты = значения системного промпта
#   uncovered_penalty: 1.5
#   ambiguous_range: [4.0, 6.0]
```

## Уточнение по ТЗ (`tz_review`)
Если fit запросил (`requires_tz_review`), `TzReviewer` ищет файл ТЗ в карточке закупки,
извлекает его текст (скачивание) и выполняет повторный fit/judge по расширенному описанию.
Флаг `requires_tz_review` фиксирует неполноту/неоднозначность описания; включается
целиком флагом `tz_review_enabled` (`SCORE_TZ_REVIEW_ENABLED`, по умолчанию `true`).

## Параллельная ветка Giga Embedder
`modules/giga_embedder.py`: `GigaTokenProvider` (OAuth 2.0 `client_credentials`, обязательный
заголовок `RqUID`, автообновление токена) и `GigaEmbedder` (модель `EmbeddingsGigaR`, окно
4096 токенов; длинные тексты режутся на чанки и усредняются). Ветка считает косинусную
близость эмбеддингов текста компетенций и описания закупки **до** LLM-пайплайна. Влияние на
score — через `giga_embedding_alpha` (`SCORE_GIGA_EMBEDDING_ALPHA`, 0.0 = только диагностика);
результат пишется в БД парсера как `embedding_similarity` и в метаданные LangFuse-трейса.
Без ключа доступа ветка не выполняется (факт пропуска фиксируется, падения нет).

### Предварительная фильтрация по векторной близости
Если `embedding_similarity < embedding_filter_threshold`
(`SCORE_EMBEDDING_FILTER_THRESHOLD`, по умолчанию `0.66`), закупка отсекается **без вызова
LLM**: возвращается `fit_score=0`, `score=0` и `score_method=sim` (фиксируется в БД парсера).
Порог `<= 0` отключает фильтрацию. Результат фильтрации терминален для каскада скоринга —
переходы Fit → P(win) → Margin не запускаются.

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

# разовый скоринг карточки (JSON) + профиль поставщика
uv run python -m scoring_service score card.json --competencies data/profile.yaml

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

**Аналитические** правила оценки (порог векторной близости, вес ветки, refine-итерации,
шкала Fit, таймаут уточнения по ТЗ) задаются в парсере — `config_service.yaml -> scoring`
(вкладка «Параметры мониторинга», роль аналитик) и применяются воркером в runtime через
`GET /api/config/scoring` (без рестарта). Значения в этом `config.yaml` — лишь фоллбэк
на случай недоступности парсера. Обязательная нормализация Fit в score и уточнение
описания закупки по ТЗ (если модель попросила) — всегда включены и аналитиком
не переключаются. **Инфраструктурные** параметры (LLM-провайдер, Giga, очереди,
`score_use_stub`) — `config.yaml` этого сервиса (и `.env` для секретов/переопределений),
вкладка «Сервисы» → «Скоринг» (devops).

| Переменная | Назначение |
|---|---|
| `SCORE_LLM_BASE_URL` / `SCORE_LLM_API_KEY` / `SCORE_LLM_MODEL` | OpenAI-совместимая LLM |
| `SCORE_PARSER_API_URL` | адрес REST API парсера (по умолчанию `http://localhost:8000`) |
| `SCORE_REDIS_URL` | адрес Redis (по умолчанию `redis://localhost:6379/0`) |
| `SCORE_P_WIN` / `SCORE_MARGIN_RATE` | стубы P(win)/Margin (дефолтный подход парсера) |
| `SCORE_COMPETENCIES_FILE` | файл профиля поставщика: структурированный YAML/JSON (`data/profile.yaml`); legacy-markdown тоже читается |
| `SCORE_NUM_REFINE_ROUNDS` | число итераций refine при `verdict=reject` |
| `SCORE_USE_STUB` | заглушка: возвращать score, уже присутствующий в данных закупки, без LLM-пайплайна (по умолчанию `false`) |
| `SCORE_NORMALIZE_FIT_FOR_SCORE` | приводить Fit (0–10) к шкале 0–1 при расчёте Score (по умолчанию `true`) |
| `SCORE_EMBEDDING_FILTER_THRESHOLD` | фоллбэк порога предварительной фильтрации по векторной близости (`<= 0` — выключена); в runtime переопределяется `config_service.yaml -> scoring` |
| `SCORE_LLM_REQUEST_TIMEOUT` / `SCORE_LLM_MAX_RETRIES` | таймаут одного LLM-запроса (сек) и число повторов на уровне SDK |
| `SCORE_LLM_RETRY_MAX_ATTEMPTS` / `SCORE_LLM_RETRY_BACKOFF_SECONDS` | лимит возвратов задачи в очередь при транзиентном сбое LLM-провайдера и пауза перед повтором (по умолчанию `3` / `5.0`) |
| `SCORE_AUTH_TOKEN` | опциональный Bearer-токен для `POST /score` (пусто = открыто) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | LangFuse |

Надёжность очереди: Redis даёт at-most-once; воркер при старте/в цикле возвращает
в `scoring:jobs` «зависшие» задачи из `scoring:processing` (аренда истекла,
`SCORE_PROCESSING_TTL_SECONDS`, восстановление с приоритетом
`SCORE_PROCESSING_RECOVERY_PRIORITY`). Транзиентные сбои LLM-провайдера
(таймаут/недоступность, 429/5xx) не теряют задачу: она возвращается в очередь
с backoff, но не более `SCORE_LLM_RETRY_MAX_ATTEMPTS` раз подряд (счётчик —
`scoring:jobs_retries`); 4xx-отказы и прочие ошибки снимают задачу навсегда.
Скоринг идемпотентен через `POST /score`.

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
