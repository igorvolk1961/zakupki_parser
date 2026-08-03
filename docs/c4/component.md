# C4 — Component (Уровень 3)

Компонентная диаграмма парсер-движка.

```mermaid
C4Component
  title C4 — Компоненты парсер-движка

  Container_Boundary(engine, "Парсер-движок") {
    Component(orchestrator, "Orchestrator", "Python", "Основной алгоритм прохода по страницам и записям")
    Component(lister, "Lister", "Playwright", "Вход, сортировка, фильтры, пагинация, контейнеры")
    Component(extractor, "Extractor", "Playwright", "Извлечение значений по config_dom")
    Component(detail, "Detail", "Playwright", "Детальная страница, файлы")
    Component(filters, "Filters engine", "Playwright", "Применение шагов фильтров")
    Component(handlers, "Handlers", "Python", "Постобработка значений (money, date, strip...)")
  }

  Container_Boundary(store, "Слой хранения") {
    Component(repo, "ProcurementRepository", "SQLAlchemy", "upsert + проверка дубликатов")
    Component(db, "Database", "SQLAlchemy async", "Engine/сессия PostgreSQL")
    Component(lastseen, "LastSeenStore", "JSON", "Дата последней обработки")
  }

  Container_Boundary(infra, "Инфраструктура") {
    Component(cb, "CircuitBreaker", "Python", "Статусы CLOSED/OPEN/HALF_OPEN")
    Component(notifier, "Notifier", "Python", "Webhook-уведомления (заглушка)")
    Component(fileproc, "FileProcessor", "Python", "Обработка файлов (заглушка)")
  }

  Rel(orchestrator, lister, "управляет")
  Rel(lister, filters, "применяет")
  Rel(lister, extractor, "извлекает list-переменные")
  Rel(orchestrator, detail, "переход")
  Rel(detail, extractor, "извлекает detail-переменные")
  Rel(extractor, handlers, "нормализует")
  Rel(orchestrator, repo, "сохраняет")
  Rel(repo, db, "SQL")
  Rel(orchestrator, lastseen, "обновляет")
  Rel(orchestrator, cb, "отказы")
  Rel(orchestrator, notifier, "уведомляет")
  Rel(orchestrator, fileproc, "файлы")
```

## Комментарии
- `handlers` — чистые функции, покрыты unit-тестами.
- `repo` гарантирует отсутствие повторной записи заявки с тем же номером
  (unique-констрейнт + проверка перед вставкой).
- `stop_conditions` обрабатываются в `orchestrator` перед сохранением/уведомлением.
