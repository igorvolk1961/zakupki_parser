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
    end

    subgraph Store["Слой хранения"]
        REPO["ProcurementRepository<br/>upsert + контроль дубликатов"]
        CUSTR["Customers<br/>нормализация заказчиков (ADR-4)"]
        DB["Database<br/>SQLAlchemy async"]
    end

    subgraph Infra["Инфраструктура"]
        CB["CircuitBreaker<br/>CLOSED / OPEN / HALF_OPEN"]
        RETR["Retry<br/>экспоненциальный backoff"]
        TRC["Transport client<br/>POST /api/scoring/jobs {id, default_score}"]
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
    REPO --> CUSTR
    REPO --> DB
    ORC --> TRC
    ORC --> CB
    ORC --> RETR
```

## Комментарии
- **Handlers** — чистые функции, покрыты unit-тестами.
- **ProcurementRepository** гарантирует отсутствие повторной записи заявки с тем же
  номером (unique-констрейнт + проверка перед вставкой); **Customers** — нормализация
  заказчиков и поиск ИНН (ADR-4).
- **stop_conditions** обрабатываются в **Orchestrator** перед сохранением.
- **Обработка файлов** (PDF/DOCX/ZIP, поиск ТЗ) вынесена во **внешний сервис** (ADR-5):
  парсер хранит метаданные, результат внешний сервис возвращает через API.
- **Scoring**: при сохранении проставляется дефолтный score (`default`) или
  `deadline_expired` для просроченных; затем **Transport client** автоматически отправляет
  задание в транспорт скоринга (`POST /api/scoring/jobs` с приоритетом = дефолтным score).
  Финальный внешний score возвращается в парсер через `POST /score` (ADR-7).
- **Уведомления** подписчиков отправляются **не в движке**, а в FastAPI-слое —
  в обработчике `POST /api/procurements/{id}/score` после обновления score, если
  `score ≥ notify_min_score` (порог из конфига).
