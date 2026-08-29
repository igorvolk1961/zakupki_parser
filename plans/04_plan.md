# Этап 4 (пересмотр). Параллельные площадки (4B) — детальный план

> Источник: мастер-план `.kilo/plans/1787250023996-architecture-multitenancy-master-plan.md`
> (§4 «Кэш ЭТП и параллельная обработка площадок», R4/R5), трекер `plans/plan.md`.
>
> **Пересмотр скоупа (согласовано, 2026-08-29).** В дефолтном режиме
> `platform_then_profile` обходы (площадка × ОКПД2) дедуплицируются `_build_units`
> в один `CrawlUnit` за цикл, а TTL кэша `0.5 × timeout_seconds` < периода цикла,
> поэтому кэш 4A в одно-инстансном MVP не даёт пользы. Его ценность проявляется
> только при повторных запросах между воркерами (4C/10, scale-out). В связи с этим:
> - **Сейчас реализуем только 4B** (параллельные площадки) — польза безусловна.
> - **4A (кэш ЭТП, R4) — переносится в пост-MVP** (к этапу 4C/10, когда появится
>   реальный повтор запросов между воркерами; тогда же продумать схему кэша честно).
> - **Опция `profile_then_platform` удаляется** — её функция (изоляция обхода по
>   профилю) достигается `deduplicate_requests=false` внутри `platform_then_profile`
>   (отдельные `CrawlUnit` + отдельная статистика), а в целевой модели 4C выражается
>   через формат задания `(user_id, profile_id, platform_id)`; в HTTP-константе
>   `profiles_loop_order` больше не нужна.

## Цель

1. Ускорить проход и развязать площадки: простой/backoff одной площадки не блокирует
   другие — каждая включённая площадка обрабатывается в отдельной `asyncio.Task`
   с ограничением `max_concurrent_platforms`.
2. Упростить планировщик: один порядок обхода (`platform_then_profile` + `deduplicate_requests`),
   убрать неиспользуемый `profile_then_platform`.
3. Зафиксировать архитектурное решение об отказе от `profile_then_platform` и переносе
   кэша 4A в пост-MVP (ADR + документация + трекер).

## Контекст (по коду)

- `Scheduler.run_once` (src/zakupki_parser/scheduler.py) обходил площадки последовательно,
  с ветвлением по `profiles_loop_order` и методом `_ordered_platforms_for_profile`.
- `_process_platform` изолирует ошибку площадки (внутренний try/except) и вызывает
  `_on_update` — пригоден для параллельного запуска.
- Поле `profiles_loop_order: Literal[...]` — `config/models/service.py` (config_service.yaml).
- `CircuitBreaker` (circuit.py) — методы без `await` (атомарны в asyncio); общие
  `_site_cb`/`_db_cb` честно разделяются параллельными задачами.
- БД — SQLAlchemy async, пул `pool_max=5` (config_ops.yaml) — хватит на 2-3 параллельные
  площадки.
- Каждая площадка создаёт собственный `BrowserManager`+`Orchestrator` — независимые,
  параллелизм безопасен.
- Схема форм конфигов генерируется из pydantic (config_schema.py) — добавление поля в
  модель автоматически появляется в форме.

## Задачи (по порядку)

### 4.1. Удалить опцию `profile_then_platform` (упрощение)
- [x] `src/zakupki_parser/config/models/service.py`: удалено поле `profiles_loop_order`
  и импорт `Literal`.
- [x] `configs/config_service.yaml`: удалены строки `profiles_loop_order`.
- [x] `src/zakupki_parser/scheduler.py`: убрано ветвление в `run_once`, удалён метод
  `_ordered_platforms_for_profile`, обновлён docstring; `_profile_on_platform` сохранён.
- [x] `tests/unit/test_config_schema.py`: убрана проверка опций `profiles_loop_order`.
- [x] Убраны ссылки в `docs/profile-crawling.md`.

### 4.2. 4B — параллельная обработка площадок
- [x] `src/zakupki_parser/config/models/parser.py`: в `ParserConfig` добавлено
  `max_concurrent_platforms: int = Field(default=2, ge=1, ...)`.
- [x] `configs/config_parser.yaml`: добавлено `max_concurrent_platforms: 2`.
- [x] `src/zakupki_parser/scheduler.py` — `run_once`: сбор пар `(platform_id, batch)`,
  запуск параллельно через `asyncio.Semaphore(max_concurrent_platforms)` +
  `asyncio.gather(..., return_exceptions=True)`; ошибки логируются.
- [x] `_recover_scoring_queue` выполняется до запуска платформенных задач.

### 4.3. Тесты
- [x] `tests/unit/test_scheduler_parallel.py` (новый): лимит параллельности (`=2`),
  последовательность (`=1`), изоляция сбоя площадки, «без профилей» и «без площадок».
- [x] `tests/unit/test_config_schema.py`: проверка присутствия `max_concurrent_platforms`.
- [x] Прогон: `uv run pytest tests/unit -q` (330 passed), `ruff check`, `ruff format`,
  `uv run mypy`, `uv run zp --configs configs check-config`.

### 4.4. Зафиксировать решение (ADR и документация)
- [x] `docs/adr.md` — ADR-11 «Параллельные площадки; отмена `profile_then_platform`;
  перенос кэша ЭТП (R4) в пост-MVP».
- [x] `docs/profile-crawling.md` — раздел «Взаимодействие с `profiles_loop_order`» заменён
  на «Взаимодействие с параллельной обработкой площадок».
- [x] `README.md`: добавлено упоминание параллельности (`max_concurrent_platforms`) в
  «Возможности» и «Конфигурация».
- [x] `specification.md`: R4 — пост-MVP, R5 — реализован; roadmap строки этапа 4 актуализированы.
- [x] `TODO.md`: параллельные площадки выполнены, кэш 4A — пост-MVP.
- [x] `plans/plan.md` (трекер): Этап 4 — 4B выполнено, 4A — пост-MVP, `profile_then_platform`
  удалена; «Текущий фокус» обновлён.
- [x] Мастер-план `.kilo/plans/…-master-plan.md` §4 — примечание о пересмотре.

## Заглушки
- Кэш ЭТП (4A) — не реализуется в MVP (перенос в пост-MVP, этап 4C/10).
- RabbitMQ-очередь `parser:jobs` + stateless-воркеры (4C) — пост-MVP, не в этом этапе.

## Критерии приёмки
1. `profiles_loop_order` отсутствует в коде/конфиге/схеме/тестах; `run_once` — только
   платформ-первый цикл.
2. Включённые площадки обрабатываются параллельно с лимитом `max_concurrent_platforms`;
   задержка/backoff одной площадки не блокирует другие.
3. Ошибка одной площадки не отменяет остальные; обходы статистик и `_on_update` работают.
4. `max_concurrent_platforms=1` сохраняет прежнее последовательное поведение.
5. unit без регрессий (330 passed); ruff/mypy (по конфигу)/check-config чисто; решение
   зафиксировано в ADR-11 и актуализированы docs + трекер.

## Риски
- **Нагрузка на площадки/антибот**: параллельные Chromium к разным доменам независимы,
  но `max_concurrent_platforms` следует держать небольшим (дефолт 2); при необходимости —
  снизить до 1.
- **Общий пул БД**: pool_max=5 в `config_ops.yaml`; если число параллельных площадок
  превысит пул — увеличить pool_max (существующие тесты с БД чувствительны).
- **Общий `_site_cb`/`_db_cb`**: атомарны в asyncio (нет await в методах), но при
  параллельных площадках счётчик ошибок суммируется — это ожидаемо.
- **`_on_update` (WebSocket)**: параллельные вызовы допустимы, но множественные
  широковещания подряд — стоит оставить как есть (не дедуплицировать в этом этапе).

## Открытые вопросы
- Значение `max_concurrent_platforms` по умолчанию (2) — подтвердить; число включённых
  площадок сейчас ~5 (zakupki_mos, etpgpb, lot_online_44/223 enabled=true).
- Нужно ли менять `pool_max` БД — оценить при первом параллельном прогоне; в MVP менять
  не планируем.
