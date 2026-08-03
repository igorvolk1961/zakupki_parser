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
    end

    subgraph Store["Слой хранения"]
        REPO["ProcurementRepository<br/>upsert + контроль дубликатов"]
        DB["Database<br/>SQLAlchemy async"]
        LS["LastSeenStore<br/>дата последней обработки"]
    end

    subgraph Infra["Инфраструктура"]
        CB["CircuitBreaker<br/>CLOSED / OPEN / HALF_OPEN"]
        NOT["Notifier<br/>webhook (заглушка)"]
        FPR["FileProcessor<br/>файлы (заглушка)"]
    end

    ORC --> LST
    LST --> FLT
    LST --> EXT
    ORC --> DET
    DET --> EXT
    EXT --> HND
    ORC --> REPO
    REPO --> DB
    ORC --> LS
    ORC --> CB
    ORC --> NOT
    ORC --> FPR
```

## Комментарии
- **Handlers** — чистые функции, покрыты unit-тестами.
- **ProcurementRepository** гарантирует отсутствие повторной записи заявки с тем же
  номером (unique-констрейнт + проверка перед вставкой).
- **stop_conditions** обрабатываются в **Orchestrator** перед сохранением/уведомлением.
