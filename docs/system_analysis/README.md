# Системный анализ — TenderSearch

Каталог артефактов системного анализа проекта **TenderSearch** (система
автоматизированного мониторинга и скоринга электронных торгов).

> Владелец набора: Игорь (архитектор). Регламент обновления — см. «Поддержание
> актуальности» ниже и `plans/plan.md` (SA-5).
> Актуализировано: 2026-08-25.

## 1. Карта артефактов

| Раздел | Артефакт | Статус | Назначение |
| :----- | :------- | :----- | :--------- |
| 01_Business_Context | [`01_vision_&_scope.md`](01_Business_Context/01_vision_%26_scope.md) | ✅ | Видение, границы MVP/пост-MVP, критерии успеха, целевые пользователи (роли/цели/задачи) |
| 01_Business_Context | [`02_stakeholders.md`](01_Business_Context/02_stakeholders.md) | ✅ | Стейкхолдеры и роли |
| 01_Business_Context | [`03_glossary.md`](01_Business_Context/03_glossary.md) | ✅ | Глоссарий предметной области и реализации |
| 01_Business_Context | [`04_as-is-to_be.md`](01_Business_Context/04_as-is-to_be.md) | ✅ | Модели процессов AS-IS и TO-BE (шаги 1–9) |
| 01_Business_Context | [`05_risks.md`](01_Business_Context/05_risks.md) | ✅ | Реестр рисков (вероятность/влияние/митигация) |
| 02_Business_Requirements | [`01_user_stories.md`](02_Business_Requirements/01_user_stories.md) | ✅ | Эпики 1–10, user stories, AC |
| 02_Business_Requirements | [`02_business_rules.md`](02_Business_Requirements/02_business_rules.md) | ✅ | Бизнес-правила BR-01…BR-09 |
| 02_Business_Requirements | [`03_product_backlog.md`](02_Business_Requirements/03_product_backlog.md) | ✅ | Итоговый Product Backlog (US/BR/FR/NFR) |
| 02_Business_Requirements | [`04_traceability_matrix.md`](02_Business_Requirements/04_traceability_matrix.md) | ✅ | Матрица трассируемости US↔BR↔NFR↔ER↔этап (человекочитаемое представление требований) |
| 02_Business_Requirements | [`05_functional_requirements.md`](02_Business_Requirements/05_functional_requirements.md) | ✅ | Функциональные требования FR-xx (включая FR-10 «Операционная работа») |
| traceability | [`requirements-registry.yaml`](traceability/requirements-registry.yaml) | ✅ | **Источник правды** «требование ↔ код ↔ тест ↔ стейкхолдер ↔ ADR» (машиночитаемый, проверяется `scripts/check_traceability.py`); 04_traceability_matrix — его представление |
| 03_System_Behavior | [`01_sequence_diagrams.md`](03_System_Behavior/01_sequence_diagrams.md) | ✅ | Диаграммы последовательности (парсинг, двухстадийный анализ ТЗ, каскад) |
| 03_System_Behavior | [`02_business_process.md`](03_System_Behavior/02_business_process.md) | ✅ | Диаграмма бизнес-процесса для стейкхолдеров |
| 04_Data_and_NFR | [`01_er_diagram.md`](04_Data_and_NFR/01_er_diagram.md) | ✅ | Модель данных: фактическая и целевая |
| 04_Data_and_NFR | [`02_non_functional_requirements.md`](04_Data_and_NFR/02_non_functional_requirements.md) | ✅ | Нефункциональные требования (COST/PERF/SEC/REL/OBS/FT) |
| 05_Diagrams | [`01_context_diagram.md`](05_Diagrams/01_context_diagram.md) | ✅ | Контекстная диаграмма (уровень 0) |
| 05_Diagrams | [`02_lifecycle_diagram.md`](05_Diagrams/02_lifecycle_diagram.md) | ✅ | Жизненный цикл закупки и аккаунта |
| 05_Diagrams | [`03_admin_observability.md`](05_Diagrams/03_admin_observability.md) | ✅ | Администрирование и наблюдаемость |
| 06_Acceptance | [`01_mvp_acceptance.md`](06_Acceptance/01_mvp_acceptance.md) | ✅ | Критерии приёмки MVP и DoD |
| 06_Acceptance | [`02_acceptance_test_cases.md`](06_Acceptance/02_acceptance_test_cases.md) | ✅ | Приёмочные тест-кейсы (API) |
| 07_UI | [`01_ui_requirements.md`](07_UI/01_ui_requirements.md) | ✅ | Требования к UI/UX |
| 07_UI | [`02_export_format.md`](07_UI/02_export_format.md) | ✅ | Спецификация экспортов (CSV/XLSX) |

Статусы: ✅ готово · 🟡 требует периодической актуализации · ❌ отсутствует.

## 2. Порядок чтения

1. **Бизнес-контекст**: `01_vision_&_scope.md` → `02_stakeholders.md` →
   `03_glossary.md` → `04_as-is-to_be.md` → `05_risks.md`.
2. **Требования**: `01_user_stories.md` → `02_business_rules.md` →
   `05_functional_requirements.md` → `04_traceability_matrix.md` → `03_product_backlog.md`.
3. **Поведение и данные**: `03_System_Behavior/*` → `04_Data_and_NFR/*` →
   `05_Diagrams/*`.
4. **Приёмка**: `06_Acceptance/*`; **интерфейс**: `07_UI/*`.

## 3. Терминология скоупа (SA-0.2)

- **MVP** — этапы 0–5 (4A+4B) по `plans/plan.md` (парсинг, профили, auto-Fit, RAG-анализ ТЗ).
- **пост-MVP** — этапы 6, 4C, 7, 8, 9, 10 (аккаунты, решения, экспорт, compliance, observability).
- **аккаунты/опции (Эпик 10, BR-09)** — наборы платных опций пользователя (`user_accounts`), триал `users.trial_end_at` (по умолчанию 14 дней); реализовано. Оплата/подписка (`subscriptions`) и заморозка/удаление (BR-05) — вне MVP / пост-MVP.
- **вне MVP** — подтверждение email при регистрации, оплата/подписка (`subscriptions`),
  автоматический каскад P(win)/Margin для всех закупок.

## 4. Шаблон артефакта (SA-0.1)

Каждый артефакт раздела следует структуре:

```
# <Название>
> Источник: <связанные артефакты/файлы> · Актуализировано: <дата>
<таблицы/диаграммы со ссылками на US/BR/NFR/ER/этап>
```

- H1-заголовок; блок «Источник» цитатой с датой актуализации;
- таблицы с ID и явными ссылками (US-*, BR-*, NFR-*, FR-*, ER-сущность, этап);
- статусы ✅/🟡/❌ для синхронизации с реализацией.

## 5. Поддержание актуальности (SA-5)

- На каждом этапе реализации (критерии приёмки этапа в `plans/NN_plan.md`) обновлять
  соответствующие артефакты: статусы US/FR, `03_product_backlog.md`,
  `04_Data_and_NFR/01_er_diagram.md`.
- **Источник правды по трассировке — `traceability/requirements-registry.yaml`**
  (требование → код-модуль → тест-файл → стейкхолдер → ADR → этап → статус). При
  изменении кода или теста обновляется **реестр**, а `04_traceability_matrix.md`
  приводится в соответствие с ним (напрямую вручную не правится, чтобы не возникло
  двух источников).
- **Проверка обязательна**: `scripts/check_traceability.py` — валидационные ошибки
  фатальны всегда, а «сироты» (тест-файл, не привязанный к требованию) фатальны
  **по умолчанию** (это `--strict`-режим). Опт-аут для ручного просмотра — `--no-strict`.
  Чекер подключён как pre-commit хук `traceability` (`.pre-commit-config.yaml`)
  и уже работает строго (`--strict`); при добавлении нового кода/теста его требуется
  зарегистрировать в реестре.
- Источник расхождений «требование → код» — gap-анализ `specification.md` §14.1;
  реестр и §14.1 синхронизируются взаимно.
- Решения, влияющие на требования, фиксируются в `docs/adr.md` и отражаются в
  артефактах (порядок: ADR → артефакт → бэклог → план).
