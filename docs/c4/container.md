# C4 — Container (Уровень 2)

Диаграмма контейнеров системы парсера.

```mermaid
flowchart LR
    classDef actor fill:#e3f2fd,stroke:#1565c0,color:#0d47a1

    U["👤 Оператор<br/>команды / конфиг"]
    P["Парсер закупок<br/>Python, asyncio, Playwright (Chromium)<br/>CLI/Scheduler + FastAPI + SQLAlchemy async"]

    Z["Платформы закупок<br/>HTML через браузер"]
    DB[("PostgreSQL<br/>хранилище закупок")]
    TR["Scoring Transport<br/>gateway скоринга (ingest + возврат)"]
    RS[("Redis<br/>scoring:/pwin:/margin: jobs и results")]
    SF["Scoring Service (Fit)<br/>LLM-скоринг (Fit → Judge → refine → ТЗ → Giga)"]
    SP["P(win) Service<br/>P(win) = base × k_smp × k_license × …"]
    SM["Margin Service<br/>Margin = НМЦК × margin_rate"]
    AN["Analysis Service<br/>RAG-анализ ТЗ (стоп-условия)"]
    SUB["👤 Подписчики<br/>Telegram / MAX / Webhook"]

    U --> P
    P -->|"HTML-страницы"| Z
    P --> DB
    P -->|"POST /api/scoring/jobs {id, priority, stage, profile_id}"| TR
    TR <-->|"jobs (ZADD) / results (BRPOP)"| RS
    RS <-->|"jobs (ZPOPMAX) / results (LPUSH)"| SF
    RS <-->|"pwin:jobs / pwin:results"| SP
    RS <-->|"margin:jobs / margin:results"| SM
    RS <-->|"analysis:jobs / analysis:results"| AN
    TR -->|"POST /score (возврат результата стадии)"| P
    P -->|"уведомления (постадийно, порог по значению стадии)"| SUB

    class U,SUB actor
```

## Замечания
- **Акторы** (оператор, подписчики) помечены символом «человечка» 👤 и выделены
  голубой заливкой. Mermaid `flowchart` не рисует стикменов, поэтому люди обозначены
  значком 👤 (в строгом C4 акторы — стикмены).
- **Парсер закупок** — единый контейнер (CLI/Scheduler, FastAPI, парсер-движок
  Playwright, слой хранения SQLAlchemy async, Notifier). Пишет в PostgreSQL с контролем
  дубликатов по `number + platform_id`. Парсер не скачивает файлы — в БД хранятся
  только метаданные файлов (имя и URL скачивания с ЭТП).
- **Скоринг** — каскад **Fit → P(win) → Margin** (ADR-7/ADR-9): после сохранения закупки
  парсер автоматически передаёт задание в **Scoring Transport** (`POST /api/scoring/jobs`
  со `stage="fit"`, приоритет = время обновления/публикации, `profile_id`),
  транспорт ставит его в **Redis** очередь стадии, сервис стадии обрабатывает задание и
  возвращает результат через транспорт (`POST /score`). **Автокаскад отключён**: P(win)/Margin
  запускаются по явному запросу тендеролога (`POST /api/procurements/pwin-margin`, флаги
  `pwin_enabled`/`margin_enabled`); `analysis_service` — on-demand RAG-анализ ТЗ.
  Транспорт — единственная граница между конвейером и парсером; приоритет приходит из
  парсера (эвристика дефолтного score в транспорте не дублируется).
- **LLM-пайплайн** `scoring_service`: Fit → Judge → refine (`num_refine_rounds`) →
  уточнение по тексту ТЗ (`tz_review`) → параллельная ветка векторной близости
  **Giga Embedder** (влияет на score через `giga_embedding_alpha`, результат —
  `embedding_similarity`). Режим заглушки `score_use_stub` удалён (ADR-7).
- **Уведомления** подписчиков отправляются **после каждой стадии** каскада (fit/pwin/
  margin), когда значение стадии прошло её порог (`notify_min_fit_score`/
  `notify_min_pwin`/`notify_min_margin`; флаги `notify_{fit,pwin,margin}_enabled`
  в `config_ops.yaml`).
