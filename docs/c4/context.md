# C4 — Context (Уровень 1)

Контекстная диаграмма системы парсера площадок закупок.

```mermaid
flowchart LR
    classDef actor fill:#e3f2fd,stroke:#1565c0,color:#0d47a1

    U["👤 Оператор / аналитик<br/>настраивает парсер"]
    S["👤 Подписчик<br/>получает оповещения"]
    W["👤 Пользователь web-приложения<br/>просмотр / запуск парсера / конфиг"]

    subgraph System["zakupki-parser — Парсер закупок"]
        P["Собирает закупки через Playwright,<br/>сохраняет в БД, оповещает<br/>+ FastAPI web-приложение"]
    end

    Z["Платформы закупок<br/>zakupki.mos.ru, ЕИС, ЭТП"]
    TR["Scoring Transport<br/>gateway скоринга (ingest + возврат)"]
    RS[("Redis<br/>очереди заданий и результатов стадий")]
    SF["Scoring Service (Fit)<br/>LLM-скоринг: Fit → Judge → refine → ТЗ → Giga"]
    SP["P(win) Service<br/>вероятность победы"]
    SM["Margin Service<br/>маржа (НМЦК × margin_rate)"]
    TG["Telegram"]
    MX["MAX"]
    WH["Webhook"]

    U -->|"YAML-конфигурация"| P
    W <-->|"HTTP / WebSocket"| P
    Z -->|"HTML-страницы закупок"| P
    P -->|"POST /api/scoring/jobs<br/>{id, default_score, stage}"| TR
    TR -->|"ZADD jobs / BRPOP results"| RS
    RS -->|"ZPOPMAX jobs / LPUSH results"| SF
    RS -->|"ZPOPMAX pwin:jobs / LPUSH pwin:results"| SP
    RS -->|"ZPOPMAX margin:jobs / LPUSH margin:results"| SM
    TR -->|"POST /score (возврат результата стадии)"| P
    P -->|"POST JSON-уведомления<br/>(постадийно: fit/pwin/margin,<br/>порог по значению стадии)"| TG
    P -->|"POST JSON-уведомления<br/>(постадийно: fit/pwin/margin,<br/>порог по значению стадии)"| MX
    P -->|"POST JSON-уведомления<br/>(постадийно: fit/pwin/margin,<br/>порог по значению стадии)"| WH
    TG --> S
    MX --> S
    WH --> S

    class U,S,W actor
```

## Легенда
- **Оператор/аналитик**, **подписчик** и **пользователь web-приложения** — внешние роли
  (акторы; помечены символом 👤 и голубой заливкой — Mermaid `flowchart` не рисует
  «человечков»).
- **Платформы закупок**, **Scoring Transport**, **Redis**, **Scoring Service (Fit)**,
  **P(win) Service**, **Margin Service** и каналы
  **Telegram / MAX / Webhook** — внешние системы.
- **zakupki-parser** — внутренняя система: парсинг закупок (Playwright), сохранение
  в БД, уведомления и **FastAPI web-приложение** (просмотр закупок/заказчиков, запуск
  парсера, редактирование конфигурации).
- **Скоринг** — каскад **Fit → P(win) → Margin** (ADR-7/ADR-9): парсер после сохранения
  автоматически передаёт задание в транспорт, тот ставит его в Redis-очередь стадии.
  `scoring_service` считает **Fit** по LLM-пайплайну (Fit → Judge → refine → ТЗ →
  Giga-эмбеддинги); при прохождении порога `pwin_fit_threshold` закупка ставится в
  очередь **P(win)** (`pwin_service`), при `p_win ≥ margin_pwin_threshold` — в очередь
  **Margin** (`margin_service`). Результат каждой стадии возвращается через транспорт;
  уведомление подписчиков — **после каждой стадии** (fit/pwin/margin), если значение
  стадии прошло её порог.
