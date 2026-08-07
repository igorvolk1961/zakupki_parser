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
        SCD["Scoring<br/>default / deadline_expired + push в транспорт"]
    end

    subgraph Store["Слой хранения"]
        REPO["ProcurementRepository<br/>upsert + контроль дубликатов"]
        DB["Database<br/>SQLAlchemy async"]
    end

    subgraph Infra["Инфраструктура"]
        CB["CircuitBreaker<br/>CLOSED / OPEN / HALF_OPEN"]
        NOT["Notifier<br/>telegram / max / webhook<br/>(только при score ≥ notify_min_score)"]
        TRC["Transport client<br/>POST /api/scoring/jobs {id, default_score}"]
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
    ORC --> TRC
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
- **Scoring**: при сохранении проставляется дефолтный score (`default`) или
  `deadline_expired` для просроченных; затем **Transport client** автоматически отправляет
  задание в транспорт скоринга (`POST /api/scoring/jobs` с приоритетом = дефолтным score).
  Финальный внешний score возвращается в парсер через `POST /score` (ADR-7); подписчики
  уведомляются только после обновления score, если он прошёл порог `notify_min_score`.
