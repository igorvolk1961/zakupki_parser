# C4 — Container (Уровень 2)

Диаграмма контейнеров системы парсера.

```mermaid
flowchart LR
    U[Оператор<br/>команды / конфиг]

    subgraph ParserSys["Парсер закупок"]
        CLI["CLI / Scheduler<br/>Python, asyncio — запуск по таймеру"]
        ENG["Парсер-движок<br/>Playwright (Chromium)"]
        AB["Антиблок-слой<br/>stealth, задержки, лимиты"]
        CB["Circuit Breaker<br/>вежливая деградация"]
        ST["Слой хранения<br/>SQLAlchemy async"]
        FP["File processor<br/>обработка файлов (заглушка)"]
    end

    Z[Платформы закупок<br/>HTML через браузер]
    DB[(PostgreSQL<br/>хранилище закупок)]
    W[Webhook<br/>оповещения]

    U --> CLI
    CLI --> ENG
    ENG --> AB
    AB --> Z
    ENG --> ST
    ENG --> FP
    ST --> DB
    ENG --> W
    CB --> ENG
    CB --> ST
```

## Замечания
- **Слой хранения** пишет в PostgreSQL через SQLAlchemy 2.x (async) с контролем
  дубликатов по `number + source_platform`.
- **Антиблок-слой** включает stealth-скрипты, задержки, лимиты и персистентную сессию.
