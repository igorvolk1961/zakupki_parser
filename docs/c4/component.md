# C4 — Component (Уровень 3)

Компонентная диаграмма парсер-движка.

```mermaid
flowchart TB
    subgraph Engine["Парсер-движок"]
        ORC["Orchestrator<br/>основной алгоритм прохода"]
        LST["Lister<br/>вход, сортировка, фильтры, пагинация"]
        EXT["Extractor<br/>извлечение по config_dom"]
        DET["Detail<br/>детальная страница, файлы"]
        FLT["Filters engine<br/>шаги фильтров"]
        HND["Handlers<br/>постобработка значений"]
        OKPD["OKPD<br/>резолв кодов ОКПД2 в пути для поиска"]
        ORG["Organization<br/>ИНН / нормализация заказчика"]
        CUT["Cutoff<br/>порог дат последней обработки"]
        FILES["Files<br/>метаданные файлов / ТЗ"]
        SCD["Scoring<br/>default / deadline_expired + push в транспорт"]
        CSC["Cascade<br/>Fit → P(win) → Margin:<br/>переходы по порогам,<br/>постадийные уведомления"]
    end

    subgraph Store["Слой хранения"]
        REPO["ProcurementRepository<br/>upsert + контроль дубликатов"]
        CUSTR["Customers<br/>нормализация заказчиков (ADR-4)"]
        DB["Database<br/>SQLAlchemy async"]
    end

    subgraph Infra["Инфраструктура"]
        CB["CircuitBreaker<br/>CLOSED / OPEN / HALF_OPEN"]
        RETR["Retry<br/>экспоненциальный backoff"]
        TRC["Transport client<br/>POST /api/scoring/jobs {id, default_score, stage}"]
    end

    ORC --> LST
    LST --> FLT
    LST --> OKPD
    ORC --> DET
    DET --> EXT
    DET --> FILES
    EXT --> HND
    ORC --> SCD
    ORC --> CUT
    ORC --> ORG
    ORC --> REPO
    ORC --> CSC
    REPO --> CUSTR
    REPO --> DB
    ORC --> TRC
    ORC --> CB
    ORC --> RETR
```

## Комментарии
- **Handlers** — чистые функции, покрыты unit-тестами.
- **ProcurementRepository** гарантирует отсутствие повторной записи закупки с тем же
  номером (unique-констрейнт + проверка перед вставкой); **Customers** — нормализация
  заказчиков и поиск ИНН (ADR-4).
- **stop_conditions** обрабатываются в **Orchestrator** перед сохранением.
- **Обработка файлов** (PDF/DOCX/ZIP, поиск ТЗ) вынесена во **внешний сервис** (ADR-5):
  парсер хранит метаданные, результат внешний сервис возвращает через API.
- **Scoring**: при сохранении проставляется дефолтный score и `fit_score`
  (`default`, fit-множитель по ОКПД2) или `deadline_expired` для просроченных; затем
  **Transport client** автоматически отправляет задание в транспорт скоринга
  (`POST /api/scoring/jobs` с приоритетом = дефолтным score и `stage="fit"`).
- **Cascade** (в FastAPI-слое): после возврата результата стадии в `POST /score`
  переход к следующей стадии (Fit → P(win) → Margin) выполняется по порогам
  `pwin_fit_threshold`/`margin_pwin_threshold` (`config_score.yaml`), если стадия
  включена (`pwin_enabled`/`margin_enabled`); стадия сохраняет свой множитель
  (`fit_score`/`p_win`/`margin`), `score` — накопленное произведение (ADR-7/ADR-9).
- **Уведомления** подписчиков отправляются **не в движке**, а в FastAPI-слое —
  в обработчике `POST /api/procurements/{id}/score` **после каждой стадии** каскада
  (fit/pwin/margin), когда значение стадии прошло её порог
  (`notify_min_fit_score`/`notify_min_pwin`/`notify_min_margin` из `config_ops.yaml`).
