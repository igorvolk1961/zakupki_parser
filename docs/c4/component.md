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
        SCD["Scoring<br/>deadline_expired + push fit в транспорт"]
        CSC["Каскад/скоринг (FastAPI)<br/>Fit → P(win) → Margin:<br/>per-profile результаты,<br/>постадийные уведомления"]
    end

    subgraph Store["Слой хранения"]
        REPO["ProcurementRepository<br/>upsert + контроль дубликатов"]
        CUSTR["Customers<br/>нормализация заказчиков (ADR-4)"]
        DB["Database<br/>SQLAlchemy async"]
    end

    subgraph Infra["Инфраструктура"]
        CB["CircuitBreaker<br/>CLOSED / OPEN / HALF_OPEN"]
        RETR["Retry<br/>экспоненциальный backoff"]
        TRC["Transport client<br/>POST /api/scoring/jobs {id, priority, stage, profile_id}"]
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
- **Scoring**: закупка сохраняется **без оценки** (дефолтный скор удалён, миграция 1.34);
  для просроченных выставляется `deadline_expired`. **Transport client** автоматически
  отправляет задание в транспорт скоринга (`POST /api/scoring/jobs` со `stage="fit"`,
  приоритет = время обновления/публикации, `profile_id` — пер-профильно, BR-07).
- **Каскад/скоринг** (в FastAPI-слое): результат стадии в `POST /score` пишется профилю
  из `body.profile_id` (BR-07); множители (`fit_score`/`p_win`/`margin`) сохраняются в
  `procurement_evaluations`, `score` — накопленное произведение. Автокаскад
  Fit → P(win) → Margin отключён: P(win)/Margin запускаются по явному запросу тендеролога
  (`POST /api/procurements/pwin-margin`, флаги `pwin_enabled`/`margin_enabled`); пороги
  `pwin_fit_threshold`/`margin_pwin_threshold` удалены (ADR-10). Дополнительно
  `analysis_service` выполняет on-demand RAG-анализ ТЗ (стоп-условия, маркеры).
- **Уведомления** подписчиков отправляются **не в движке**, а в FastAPI-слое —
  в обработчике `POST /api/procurements/{id}/score` **после каждой стадии** каскада
  (fit/pwin/margin), когда значение стадии прошло её порог
  (`notify_min_fit_score`/`notify_min_pwin`/`notify_min_margin` из `config_ops.yaml`).
