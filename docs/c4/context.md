# C4 — Context (Уровень 1)

Контекстная диаграмма системы парсера площадок закупок.

```mermaid
flowchart LR
    U["Оператор / аналитик<br/>настраивает парсер"]
    S["Подписчик<br/>получает оповещения"]
    W["Пользователь web-демо<br/>просмотр / запуск парсера / конфиг"]

    subgraph System["zakupki-parser — Парсер закупок"]
        P["Собирает закупки через Playwright,<br/>сохраняет в БД, оповещает<br/>+ FastAPI web-демо"]
    end

    Z["Платформы закупок<br/>zakupki.mos.ru, ЕИС, ЭТП"]
    TR["Scoring Transport<br/>gateway скоринга"]
    RS[("Redis<br/>очередь заданий и результатов")]
    SG["Scoring Service<br/>LLM-скоринг закупок"]
    TG["Telegram"]
    MX["MAX"]
    WH["Webhook"]

    U -->|"YAML-конфигурация"| P
    W -->|"HTTP / WebSocket"| P
    Z -->|"HTML-страницы закупок"| P
    P -->|"POST /api/scoring/jobs<br/>{id, default_score}"| TR
    TR -->|"ZADD jobs / BRPOP results"| RS
    RS -->|"ZPOPMAX jobs / LPUSH results"| SG
    TR -->|"POST /score (возврат результата)"| P
    P -->|"POST JSON-уведомления (score ≥ notify_min_score)"| TG
    P -->|"POST JSON-уведомления (score ≥ notify_min_score)"| MX
    P -->|"POST JSON-уведомления (score ≥ notify_min_score)"| WH
    TG --> S
    MX --> S
    WH --> S
```

## Легенда
- **Оператор/аналитик**, **подписчик** и **пользователь web-демо** — внешние роли.
- **Платформы закупок**, **Scoring Transport**, **Redis**, **Scoring Service** и каналы
  **Telegram / MAX / Webhook** — внешние системы.
- **zakupki-parser** — внутренняя система: парсинг закупок (Playwright), сохранение
  в БД, уведомления и **FastAPI web-демо** (просмотр закупок/заказчиков, запуск
  парсера, редактирование конфигурации).
- **Скоринг** выполняется конвейером `Scoring Transport` → `Redis` → `Scoring Service`
  (ADR-7): парсер после сохранения автоматически передаёт задание в транспорт, а
  уведомление подписчиков отправляется только после возврата финального скора, если
  `score ≥ notify_min_score`.
