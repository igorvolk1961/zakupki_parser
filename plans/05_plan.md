# Этап 5 (закрытие). Явный статус «анализ отложен/недоступен» (NFR-FT-2)

> Источник: трекер `plans/plan.md` (Этап 5, 🟡), реестр `requirements-registry.yaml`
> (`FR-4.2` — implemented; `NFR-FT-2` — partial, `covered_by: FR-4.2`).
> Цель: закрыть единственный оставшийся пункт MVP — явно показать пользователю,
> что часть проверок ТЗ **не выполнена** из-за недоступности LLM/эмбеддингов,
> а не молча выдавать 🟢 «барьеров нет».

## Контекст (по коду)

- `RagAnalyzer` (src/analysis_service/analysis_service/pipeline/rag.py) собирает
  `rag_report` (сохраняется в `procurement_evaluations.rag_report`, JSONB;
  `EvaluationsRepository.update_rag_report`, schema `rag_report: dict[str, Any]`).
- Сбои сейчас **деградируют в 🟢** `no_stop_condition`:
  - `_analyze_system` при `data is None` (batch-LLM вернул None) — `_system_failed` →
    все системные проверки `no_stop_condition`+🟢 с reasoning «не выполнено (сбой)»
    (rag.py:228–232, 250–260);
  - `_analyze` при недоступности эмбеддингов — вопросы профиля `no_stop_condition`
    + top-level `error` (rag.py:166–185);
  - `_verdict_for_question` при `embed_one`/`chat_json` None — `no_stop_condition`
    + reasoning (rag.py:272–302).
- Матчер `apply_profile_facts` (matcher.py) трактует «нет данных» как
  `no_stop_condition` — для случая, когда LLM РАБОТАЛ и ничего не нашёл. Это корректно
  и НЕ трогается (тесты matcher.py/test_rag.py:276–327 остаются).
- Doй-носитель: `status` в `rag_report` отсутствует; вёрдикт ограничен
  `Literal["no_stop_condition","absolute","soft"]` (rag.py:47,68).
- UI рендер `procurements.js → ragReportHtml` (строки 305–340): обрабатывает
  `tz_found===false` и `report.error`, иначе рисует список с pill-бейджами
  (`verdictBadge`, строка 312–317).
- `clients.js:38–42` — только статический read-only список `sys:*`, без вёрдиктов
  (не правится). `prompts/verdict_system.md` — схема ответа LLM (без `unavailable`;
  `unavailable` — клиентское/аналитическое состояние, LLM его не выдаёт).
- Ретраев на сбой LLM нет: `process_stage_job` вызывает `compute` один раз
  (ретрай только 5xx/транспорт). DLQ/circuit breaker — пост-MVP (Этап 10).
  Значит «отложен» = **терминальное представленное состояние**, не перепостановка.

## Решение (согласовано)

Добавить вердикт `unavailable` (серый `⚪`, «не проверено», severity 0) для любой
проверки, которую не удалось оценить (сбой batch-LLM по системным, сбой
LLM/эмбеддингов по вопросам профиля), + верхнеуровневый `status` в `rag_report`:
`ok | deferred | error | no_tz`. `failed ≠ «без барьера»`.

## Задачи (по порядку)

### 5.1. Модель вердикта `unavailable`
- [ ] `src/analysis_service/analysis_service/pipeline/matcher.py`:
  - `VERDICT_UNAVAILABLE = "unavailable"`;
  - `MARKERS[VERDICT_UNAVAILABLE] = "⚪"`;
  - `SEVERITY[VERDICT_UNAVAILABLE] = 0`.
  (matcher — источник констант MARKERS/SEVERITY; rag.py импортирует).
- [ ] `src/analysis_service/analysis_service/pipeline/rag.py`:
  - `VERDICT_UNAVAILABLE` (импорт из matcher или локальная константа) + расширить
    `VERDICTS = (VERDICT_NONE, VERDICT_SOFT, VERDICT_ABSOLUTE, VERDICT_UNAVAILABLE)`;
  - `Verdict = Literal["no_stop_condition","absolute","soft","unavailable"]`
    и `QuestionVerdict.verdict` — тот же Literal.

### 5.2. Деградация → `unavailable`, а не 🟢 (rag.py)
- [ ] `_system_failed` (или переименовать в `_system_unavailable`) — вернуть
  `verdict=VERDICT_UNAVAILABLE`, `severity=0`, `marker="⚪"`, reasoning сохранить.
- [ ] Ветка недоступности эмбеддингов (`_analyze`, rag.py:166–185): вопросы профиля
  создавать как `VERDICT_UNAVAILABLE` (вместо `VERDICT_NONE`).
- [ ] `_verdict_for_question`: `embed_one is None` и `chat_json is None` →
  `VERDICT_UNAVAILABLE` (вместо `VERDICT_NONE`).
- [ ] Top-level `status` во всех возвращаемых `rag_report`-словарях (хелпер
  `_status(tz_found, error, questions)`):
  - `no_tz` — `tz_found is False`;
  - `deferred` — найден хотя бы один `verdict == "unavailable"`;
  - `error` — иначе если есть `error`;
  - `ok` — иначе.
  Подставлять `status` в ветки: tz-false, нет текста, нет чанков, embed-fail,
  штатная, и в ранний возврат `tz_found=False`.

### 5.3. UI (procurements.js)
- [ ] `verdictBadge`: для `unavailable` — label «не проверено», `cls="inactive"` (серый);
  для остальных оставить текущее поведение (🔴/🟡/🟢 по маркеру).
- [ ] `ragReportHtml`: при `report.status === "deferred"` добавить баннер
  (например, `⚠️ Недоступен LLM/эмбеддинги — часть проверок не выполнена.`) над
  списком вопросов; `tz_found===false` и `report.error` — без изменений.
- [ ] Свернуть label: `const LABELS = {absolute:"запрет", soft:"понижает",
  no_stop_condition:"нет", unavailable:"не проверено"}` — использовать вместо
  текущей тернарной цепочки (маркер остаётся как эмодзи перед пилюлей при наличии).

### 5.4. Тесты (src/analysis_service/tests/test_rag.py)
- [ ] Добавить:
  - `test_analyze_system_llm_failure_unavailable`: `_FakeLlm([None])` →
    `_analyze_system(...)` возвращает 3 вердикта `unavailable` с маркером `⚪`.
  - `test_analyze_deferred_when_embeddings_unavailable`: фейк-эмбеддер с
    `embed → None`; `analyze(...)` → вопросы профиля `unavailable`,
    `report["status"] == "deferred"` (системные — `ok`, если batch-LLM вернул данные).
  - `test_analyze_deferred_when_llm_verdict_none`: `_FakeLlm([{batch}, None])` →
    вопрос профиля `unavailable`, `status == "deferred"`.
  - `test_report_status_ok`: нормальный прогон → `status == "ok"`.
- [ ] Проверить НЕ трогать матчер-тесты (276–327): `apply_profile_facts` с
  None/отсутствующими блоками остаётся `no_stop_condition` (LLM работал, ничего не нашёл).
- [ ] `tests/integration/test_multiclient.py::test_rag_report_via_score_endpoint` —
  не ломается (report постится как есть, `status` опционален); при желании добавить
  отсутствие `status` → «как есть» (без изменений обязательно).

### 5.5. Реестр + документация (закрытие Этапа 5, MVP)
- [ ] `requirements-registry.yaml`: `NFR-FT-2` `status: partial → implemented`
  (graceful degradation + явный статус закрыты; `covered_by: FR-4.2` уже implemented).
  Код/тесты уже зарегистрированы (`rag.py`, `test_rag.py`) — новых сирот нет.
- [ ] `plans/plan.md`: строку Этап 5 «🟡 Заглушка при недоступности LLM…» → ✅ («явный
  статус «анализ отложен» реализован (NFR-FT-2)»); заголовок Этап 5 → **✅ (MVP)**;
  «Текущий фокус» → MVP выполнен (Этапы 0–5), дальше пост-MVP (6, 4C, 7–10, кэш 4A).
- [ ] `specification.md §14.4`: строка roadmap Этап 5 → `MVP ✅` (добавить «явный статус
  „анализ отложен“ (NFR-FT-2)»); если есть отдельный пункт про graceful degradation —
  обновить.
- [ ] Мастер-план `.kilo/plans/…-master-plan.md` §5 — пометить закрытие Этапа 5.
- [ ] `docs/system_analysis/…` — статусы FR-4.2/NFR-FT-2 синхронизированы с реестром.

### 5.6. Проверки
- [ ] `uv run python scripts/check_traceability.py --strict` → 0 ошибок, 0 сирот.
- [ ] `uv run pytest src/analysis_service/tests -q` (новые + старые тесты rag/matcher).
- [ ] `uv run pytest tests -q` (root, вкл. `test_multiclient.py`).
- [ ] `bash scripts/check_subprojects.sh lint` (ruff+format+mypy по analysis_service).
- [ ] `bash scripts/test_all.sh` (полный прогон + покрытие корня).
- [ ] `uv run ruff check src tests; uv run mypy` — чисто.

## Заглушки
- Ретрай/DLQ при сбое LLM — НЕ в этом этапе (пост-MVP, Этап 10: BR-06, circuit breaker,
  DLQ). «Отложен» — только явное отображение.
- `analysis_status` «в работе» (Running) в БД — НЕ вводим: статус выполнения уже есть
  client-side (`analyzingIds` в procurements.js); добавляем терминальный `deferred`.

## Критерии приёмки
1. При сбое batch-LLM системные проверки имеют `verdict=unavailable`, `marker=⚪`,
   `status=deferred` — НЕ 🟢.
2. При недоступности эмбеддингов/LLM-верификации вопросы профиля — `unavailable`,
   `status=deferred`; системные проверки (успешные) остаются как есть.
3. UI при `status=deferred` показывает баннер «часть проверок не выполнена» и серые
   пилюли «не проверено»; при `status=ok` — без изменений.
4. Матчер-правила и их тесты не изменились; `tz_found===false`/`error` UI-ветки ок.
5. `NFR-FT-2` → `implemented`; трассируемость строгая (0 ошибок/сирот); MVP закрыт
   (Этапы 0–5 ✅).
6. Прогончики: root + analysis_service тесты зелёные; ruff/mypy чисто.

## Риски
- **Смена вёрдикта** может затронуть потребителей, ожидающих только 3 значения.
  В коде UI — единая точка (`verdictBadge`); бэкенд не фильтрует по вёрдикту анализа
  (стоп-условия парсера — отдельный `orchestrator/stop.py`, не анализ). Проверить
  `grep "verdict"` по `src` (scoring_service `judge.verdict` — НЕ трогаем).
- **Интеграционный тест** постит `rag_report` без `status` — `update_rag_report`
  хранит словарь как есть, ломки нет.
- **Over-engineering**: держим только `unavailable`+`status`; без отдельной сущности
  «Running» в БД.

## Открытые вопросы
- Значение/эмодзи для unavailable: принято `⚪` + «не проверено». Если хочется другой
  иконки (⁉️/⏳) — меняется в `MARKERS` (matcher.py) и `LABELS` (JS) синхронно.
- Нужно ли `status` также в интеграционном тесте (рекомендуется добавить как
  самостоятельный шаг): да, добавить `assert card["rag_report"]["status"] == "ok"`
  в `test_multiclient.py::test_rag_report_via_score_endpoint` (постим `"status": "ok"`).
