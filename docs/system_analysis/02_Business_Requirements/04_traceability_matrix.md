# Матрица трассируемости требований

> Связывает US ↔ BR ↔ NFR ↔ ER-сущности ↔ диаграммы ↔ этап реализации.
> Источник статусов: `specification.md` §14.1, `plans/plan.md` (актуализировано 2026-08-24).
> ER-сущности — по фактической модели (`04_Data_and_NFR/01_er_diagram.md`); сущности,
> отмеченные «(цель)», — целевая модель пост-MVP.

## 1. User Stories → BR/NFR/ER/этап

| US | Название | BR | NFR | ER-сущности | Диаграмма | Этап | Статус |
| :-- | :------- | :-- | :-- | :---------- | :-------- | :--- | :----- |
| US-1.1 | Ключевые слова и исключения | BR-02 | NFR-PERF-1 | PROFILE, KEYWORD | TO-BE шаг 1 | 2 | ✅ |
| US-1.2 | Целевые ЭТП и законы | — | — | PROFILE | TO-BE шаг 1 | 2 | 🟡 |
| US-1.3 | Самостоятельное изменение слов | BR-02 | NFR-PERF-1 | KEYWORD | — | 2 | ✅ |
| US-1.4 | Несколько профилей | BR-07 | NFR-SEC-1 | PROFILE | — | 2 | ✅ |
| US-2.1 | Периодический сбор закупок | BR-01 | NFR-PERF-2, NFR-COST-2 | PROCUREMENT | Парсинг/скоринг; TO-BE шаг 2 | 3, 4 | ✅ |
| US-2.2 | Оценка Fit | BR-02 | NFR-COST-1/3 | PROCUREMENT_EVALUATION | Парсинг/скоринг; TO-BE шаг 3 | 3 | ✅ |
| US-2.3 | Сортировка по Fit | BR-02 | NFR-PERF-1 | PROCUREMENT_EVALUATION | — | 3 | ✅ |
| US-2.4 | Фильтр до списка | BR-02 | NFR-PERF-1 | KEYWORD, PROCUREMENT | Парсинг/скоринг; TO-BE шаг 2 | 3 | ✅ |
| US-2.5 | Скрытие отклонённых | BR-01 | — | PROCUREMENT_EVALUATION | Жизненный цикл (архив) | 7 | ❌ |
| US-3.1 | Оповещение о высокорелевантных | BR-02 | NFR-OBS-1 | PROCUREMENT_EVALUATION | Парсинг/скоринг; TO-BE шаг 4 | 8 | 🟡 |
| US-3.2 | Базовые поля карточки | — | NFR-PERF-1 | PROCUREMENT, CUSTOMER | TO-BE шаг 4 | 3 | ✅ |
| US-3.3 | Экспорт XLSX | BR-02 | NFR-SEC-4 | PROCUREMENT, PROCUREMENT_EVALUATION | TO-BE шаг 9 | 8 | 🟡 CSV |
| US-4.1 | Инициация анализа ТЗ | — | NFR-PERF-2, NFR-FT-2 | PROCUREMENT, PROCUREMENT_EVALUATION | Анализ ТЗ; TO-BE шаг 5 | 5 | ✅ |
| US-4.2 | Проверка опыта 2571 | BR-03 | NFR-COST-1/3 | PROCUREMENT_EVALUATION (rag_report) | Анализ ТЗ | 5 | 🟡 |
| US-4.3 | Реестр Минпромторга | BR-04 | NFR-COST-1/3 | PROCUREMENT_EVALUATION (rag_report) | Анализ ТЗ | 5 | 🟡 |
| US-4.4 | Проверка лицензий | — | NFR-COST-1/3 | PROCUREMENT_EVALUATION (rag_report) | Анализ ТЗ | 5 | 🟡 |
| US-4.5 | Маркеры в карточке | BR-03, BR-04 | — | PROCUREMENT_EVALUATION (rag_report) | Анализ ТЗ; TO-BE шаг 6 | 5 | 🟡 |
| US-5.1 | «В работу»/«Отклонить» | — | — | PROCUREMENT_EVALUATION (status) | Жизненный цикл; TO-BE шаг 7 | 7 | ❌ |
| US-5.2 | Причина отклонения | — | — | PROCUREMENT_EVALUATION (rejection_reason) | TO-BE шаг 8 | 7 | ❌ |
| US-5.3 | Предложение обновить профиль | BR-02 | — | PROFILE, KEYWORD | TO-BE шаг 8 | 7 | ❌ |
| US-6.1 | Сводка «В работу» в XLSX | BR-02 | NFR-SEC-4 | PROCUREMENT_EVALUATION | TO-BE шаг 9 | 8 | ❌ |
| US-6.2 | Маркеры в сводке | BR-03, BR-04 | NFR-SEC-4 | PROCUREMENT_EVALUATION | TO-BE шаг 9 | 8 | ❌ |
| US-7.1 | Самостоятельная регистрация | BR-07 | NFR-SEC-2 | USER | — | 1, 6 | ✅ |
| US-7.2 | Trial-доступ 10 лет | BR-05 | — | USER (trial_end_date) | Жизненный цикл аккаунта | 6 | 🟡 |
| US-7.3 | Заморозка по истечении trial | BR-05 | — | USER (status) | Жизненный цикл аккаунта | 6 | ❌ |
| US-7.4 | Предупреждения о конце trial | BR-05 | — | USER | Жизненный цикл аккаунта | 6 | ❌ |
| US-7.5 | Удаление через 90 дней | BR-05 | — | USER | Жизненный цикл аккаунта | 6 | ❌ |
| US-7.6 | Админ-контроль аккаунтов | BR-07 | NFR-OBS-2 | USER | Admin/Observability | 6, 10 | ❌ |
| US-7.7 | Создание администраторов | BR-07 | — | USER (role) | Admin/Observability | 6 | ❌ |
| US-7.8 | Изоляция данных | BR-07 | NFR-SEC-1 | PROFILE, PROCUREMENT_EVALUATION | — | 1 | ✅ |
| US-7.9 | Мультитенантность БД | BR-07 | NFR-SEC-1 | все пользовательские таблицы | — | 1 | ✅ |
| US-8.1 | Метрики в мониторинг | — | NFR-OBS-1 | — | Admin/Observability | 10 | 🟡 |
| US-8.2 | Панель управления пользователями | BR-07 | — | USER, AUDIT_LOG (цель) | Admin/Observability | 6, 10 | ❌ |
| US-8.3 | Горизонтальное масштабирование | BR-06 | NFR-PERF-3 | PROCUREMENT | Admin/Observability | 4C, 10 | 🟡 |
| US-9.1 | Дисклеймер | — | NFR-SEC-4 | — | — | 8, 9 | ❌ |
| US-9.2 | robots.txt, официальные API | — | NFR-SEC-5 | — | Context | 9 | ❌ |
| US-9.3 | Маскирование ПДн | — | NFR-SEC-3 | PROCUREMENT (text) | — | 9 | ❌ |
| US-9.4 | Аудит действий с IP | — | NFR-OBS-2 | AUDIT_LOG (цель) | Admin/Observability | 6, 9 | ❌ |

## 2. Business Rules → NFR/ER/этап

| BR | Название | Связанные US | NFR | ER-сущности | Этап | Статус |
| :-- | :------- | :----------- | :-- | :---------- | :--- | :----- |
| BR-01 | Частота парсинга и кэширование | US-2.1, US-2.5 | NFR-COST-2, NFR-PERF-2 | PROCUREMENT | 3, 4 | 🟡 |
| BR-02 | Первичный скоринг Fit и пост-фильтрация | US-1.1, US-2.2–2.4, US-3.1, US-5.3 | NFR-PERF-1, NFR-COST-1/3 | KEYWORD, PROCUREMENT_EVALUATION | 3 | ✅ |
| BR-03 | Валидация опыта (ПП РФ 2571) | US-4.2, US-4.5 | NFR-COST-1/3 | PROCUREMENT_EVALUATION (rag_report) | 5 | 🟡 |
| BR-04 | Реестр Минпромторга («не установлено») | US-4.3, US-4.5 | NFR-COST-1/3 | PROCUREMENT_EVALUATION (rag_report) | 5 | 🟡 |
| BR-05 | Жизненный цикл аккаунта | US-7.2–7.5 | — | USER, SUBSCRIPTION (цель) | 6 | ❌ |
| BR-06 | Обработка ошибок (DLQ) | US-8.3 | NFR-REL-2, NFR-FT-1/3/4 | PROCUREMENT | 4, 10 | 🟡 |
| BR-07 | Изоляция данных | US-7.8, US-7.9 | NFR-SEC-1 | PROFILE, PROCUREMENT_EVALUATION, SUBSCRIPTION, AUDIT_LOG | 1 | ✅ |

## 3. NFR → этап/статус (сводно)

| NFR | Этап | Статус | Покрывается |
| :-- | :--- | :----- | :---------- |
| COST-1/3 | 5, 10 | 🟡 | FR-4.1, BR-03/04 |
| COST-2 | 4 | 🟡 | BR-01, FR-2.5 |
| PERF-1 | — | ✅ | FR-2.6 |
| PERF-2 | 4 | 🟡 | FR-2.1, FR-4.1 |
| PERF-3 | 4C, 10 | 🟡 | FR-7.3 |
| SEC-1 | 1 | ✅ | BR-07, FR-6.2 |
| SEC-2 | — | ✅ | — |
| SEC-3 | 9 | ❌ | FR-8.3 |
| SEC-4 | 8, 9 | ❌ | FR-3.4, FR-8.1 |
| SEC-5 | 9 | ❌ | FR-8.2 |
| REL-1 | 10 | 🟡 | — |
| REL-2 | 10 | 🟡 | BR-06, FR-9.3 |
| OBS-1 | — | ✅ | FR-7.1 |
| OBS-2 | 6, 9 | ❌ | FR-8.4 |
| FT-1 | 1, 10 | ✅ | FR-9.2 |
| FT-2 | 5 | 🟡 | FR-4.2 |
| FT-3 | 10 | ✅ | BR-06 |
| FT-4 | 10 | 🟡 | BR-06 |
| FT-5 | 1, 10 | ✅ | FR-9.2 |

## 4. Функциональные требования → US/этап

> Полная спецификация FR — `02_Business_Requirements/05_functional_requirements.md`.
> Каждый FR уже содержит колонку US/BR, статус и этап; здесь сводная перекрёстная таблица
> «FR → Эпик»:

| FR-группа | Эпик | FR-ID | Статус |
| :-------- | :--- | :---- | :----- |
| Профили | 1 | FR-1.1–1.4 | ✅ |
| Парсинг/скоринг | 2 | FR-2.1–2.7 | ✅ |
| Доставка/экспорт | 3, 6 | FR-3.1–3.4 | 🟡 |
| Анализ ТЗ | 4 | FR-4.1–4.3 | 🟡 |
| Решения | 5 | FR-5.1–5.2 | ❌ |
| Доступ/аккаунты | 7 | FR-6.1–6.4 | 🟡 |
| Наблюдаемость | 8 | FR-7.1–7.3 | 🟡 |
| Compliance | 9 | FR-8.1–8.4 | ❌ |
| Каскад | 2, 4 | FR-9.1–9.3 | 🟡 |
