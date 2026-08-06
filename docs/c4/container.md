# C4 — Container (Уровень 2)

Диаграмма контейнеров системы парсера.

```mermaid
flowchart LR
    U[Оператор<br/>команды / конфиг]

    subgraph ParserSys["Парсер закупок"]
        CLI["CLI / Scheduler<br/>Python, asyncio — запуск по таймеру"]
        ENG["Парсер-движок<br/>Playwright (Chromium)"]
        AB["Антиблок-слой<br/>stealth, задержки, лимиты"]
        CB["Circuit Breaker<br/>вежливая деградация + backoff"]
        ST["Слой хранения<br/>SQLAlchemy async"]
        NOT["Notifier<br/>telegram / max / webhook"]
    end

    Z[Платформы закупок<br/>HTML через браузер]
    DB[(PostgreSQL<br/>хранилище закупок)]
    OS[(Object store<br/>MinIO / local)]
    FS["Внешний сервис<br/>обработки файлов (ADR-5)"]
    SS["Микросервис скоринга<br/>(ADR-3 / ADR-6)"]
    SUB[Подписчики<br/>Telegram / MAX / Webhook]

    U --> CLI
    CLI --> ENG
    ENG --> AB
    AB --> Z
    ENG --> ST
    ST --> DB
    ST --> OS
    ENG --> FS
    ENG --> SS
    ENG --> NOT
    NOT --> SUB
    CB --> ENG
    CB --> ST
```

## Замечания
- **Слой хранения** пишет в PostgreSQL через SQLAlchemy 2.x (async) с контролем
  дубликатов по `number + source_platform`; скачанные файлы — в объектное
  хранилище (MinIO/local, `storage.object_store`), в БД — ссылка, а не бинарник.
- **Обработка файлов** (PDF/DOCX/ZIP, поиск ТЗ) вынесена во **внешний сервис** (ADR-5);
  парсер хранит метаданные файлов, результат внешний сервис возвращает через
  `POST /api/procurements/{id}/technical-spec`.
- **Скоринг** выполняется микросервисом асинхронно (ADR-3/ADR-6); оповещение
  подписчиков происходит только после обновления score в БД.
- **Антиблок-слой** включает stealth-скрипты, задержки, лимиты и персистентную сессию;
  ретраи с экспоненциальным backoff (`retry.py`) перед открытием circuit breaker.
