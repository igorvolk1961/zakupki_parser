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
        SCD["Scoring<br/>default / deadline_expired / external"]
    end

    subgraph Store["Слой хранения"]
        REPO["ProcurementRepository<br/>upsert + контроль дубликатов"]
        DB["Database<br/>SQLAlchemy async"]
    end

    subgraph Infra["Инфраструктура"]
        CB["CircuitBreaker<br/>CLOSED / OPEN / HALF_OPEN"]
        NOT["Notifier<br/>telegram / max / webhook бэкенды"]
        ESC["ExternalScorer<br/>вызов микросервиса скоринга (async)"]
    end

    ORC --> LST
    LST --> FLT
    LST --> EXT
    ORC --> DET
    DET --> EXT
    EXT --> HND
    ORC --> SCD
    ORC --> REPO
    REPO --> DB
    ORC --> ESC
    ORC --> CB
    ORC --> NOT
```

## Комментарии
- **Handlers** — чистые функции, покрыты unit-тестами.
- **ProcurementRepository** гарантирует отсутствие повторной записи заявки с тем же
  номером (unique-констрейнт + проверка перед вставкой).
- **stop_conditions** обрабатываются в **Orchestrator** перед сохранением/уведомлением.
- **Обработка файлов** (PDF/DOCX/ZIP, поиск ТЗ) вынесена во **внешний сервис** (ADR-5):
  парсер хранит метаданные, результат внешний сервис возвращает через API.
- **ExternalScorer** вызывает микросервис скоринга после сохранения «сырой» записи;
  подписчики уведомляются только после обновления score (ADR-3/ADR-6).
