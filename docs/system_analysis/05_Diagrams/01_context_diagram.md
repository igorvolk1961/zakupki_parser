# Контекстная диаграмма системы (Уровень 0)

> Синхронизировано с `docs/c4/context.md` и `docs/c4/container.md` (актуализировано
> 2026-08-24). Отражён фактический контур: каскад Fit → P(win) → Margin, Redis,
> LangFuse, постадийные уведомления.

```mermaid
graph TD
    subgraph External["Внешний мир"]
        ETP["ЭТП: ЕИС, Портал Москвы, Роселторг,<br>Фабрикант, B2B-Center, ЭТП ГПБ, lot-online"]
        LLM["LLM: DeepSeek, Giga (эмбеддинги)"]
        NOTIFY["Каналы уведомлений:<br>Telegram, Max, Webhook"]
        OBS["LangFuse: тресы, метрики, стоимость"]
    end

    subgraph Users["Пользователи"]
        TS["👤 Тендерологи"]
        ADM["👤 Администратор"]
        DEV["👤 DevOps"]
        LAW["👤 Юрист"]
    end

    subgraph System["Система SaaS (zakupki-parser / TenderSearch)"]
        API["API Gateway (FastAPI)<br>+ web-демо"]
        PARSER["Парсер (Playwright)<br>клиентская пост-фильтрация (R9)"]
        TR["Scoring Transport<br>gateway каскада (ingest + возврат)"]
        RS[("Redis<br>очереди стадий")]
        SF["Scoring Service (Fit)<br>LLM: Fit → Judge → refine → ТЗ → Giga"]
        SP["P(win) Service"]
        SM["Margin Service"]
        ANALYSIS["Analysis Service<br>RAG-анализ ТЗ по вопросам профиля"]
        DB[("PostgreSQL<br>закупки, профили, оценки")]
        AUDIT["Журнал аудита (целевой)"]
    end

    ETP -->|"HTML/API с robots.txt"| PARSER
    PARSER -->|"POST /api/scoring/jobs (авто-пуш Fit, ADR-7)"| TR
    TR -->|"очереди scoring:/pwin:/margin:"| RS
    RS -->|"ZPOPMAX jobs / LPUSH results"| SF
    RS -->|"pwin:jobs / pwin:results"| SP
    RS -->|"margin:jobs / margin:results"| SM
    RS -->|"analysis:jobs"| ANALYSIS
    SF -->|"Анализ текста/эмбеддинги"| LLM
    ANALYSIS -->|"RAG: эмбеддинги + LLM"| LLM
    SF -->|"Тресы, latency, cost"| OBS
    ANALYSIS -->|"Тресы, latency, cost"| OBS
    TR -->|"POST /score (возврат результата стадии)"| PARSER

    PARSER -->|"сохранение закупок/оценок"| DB
    API --> DB
    PARSER -->|"постадийные уведомления (fit/pwin/margin)"| NOTIFY
    API -->|"просмотр/запуск парсера/профили"| PARSER

    TS -->|"регистрация, профили, анализ ТЗ, экспорт"| API
    ADM -->|"управление пользователями/БД/конфиг"| API
    DEV -->|"мониторинг через LangFuse"| OBS
    LAW -->|"compliance"| API

    API --> PARSER
    PARSER --> ANALYSIS
    PARSER --> AUDIT
```

## Легенда

- **Парсер** — сбор закупок по ОКПД2 (клиентская пост-фильтрация словами до записи, R9),
  дедуп, авто-пуш заданий Fit в Scoring Transport (ADR-7).
- **Scoring Transport** — единственная граница между конвейером и парсером: ingest
  (`POST /api/scoring/jobs`) и возврат результата (`POST /score`).
- **Каскад Fit → P(win) → Margin** — отдельные stateless-воркеры за Redis-очередями;
  автокаскад отключён, P(win)/Margin — on-demand по запросу тендеролога.
- **Analysis Service** — on-demand RAG-анализ ТЗ по вопросам профиля (вердикты
  absolute/soft/no_stop_condition).
- **Уведомления** — постадийные (после fit/pwin/margin) при прохождении порога
  (`notify_min_fit_score`/`notify_min_pwin`/`notify_min_margin`).
- **Аудит** — целевая сущность (этап 6/9/10).
