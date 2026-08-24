# Диаграммы последовательности

> Синхронизировано с реализацией (этапы 1–3): клиентская пост-фильтрация до записи (R9),
> авто-пуш заданий в `scoring_transport` (ADR-7), постадийные уведомления. Сценарий
> «обратная связь» (ручная корректировка/отклонение) — пост-MVP (этап 7).

## Диаграмма последовательности процесса парсинга и скоринга

```mermaid
sequenceDiagram
    participant JOB as Планировщик задач
    participant PARSER as Парсер
    participant FILT as Клиентская фильтрация (R9)
    participant DB as База данных
    participant ETP as ЭТП (ЕИС и др.)
    participant TR as Scoring Transport
    participant SCORING as Скоринг (Fit)
    participant NOTIFY as Уведомления
    participant TS as  Тендеролог

    Note over JOB,TS: Запуск фонового цикла парсинга (постоянный мониторинг)

    JOB->>PARSER: Инициация задачи (активный профиль)

    rect rgb(240, 248, 255)
    Note right of PARSER: Этап 1: Получение контекста и ранняя фильтрация
    PARSER->>DB: Загрузка Активного Профиля (user_id, okpd_codes, слова)
    DB-->>PARSER: ОКПД2, ключевые слова, исключения, пороги

    PARSER->>ETP: Запрос закупок (только коды ОКПД2 / обход «без кода»)
    ETP-->>PARSER: Сырой список найденных закупок
    end

    loop Для каждой закупки из сырого списка
        PARSER->>FILT: Проверка слов профиля (ДО записи, R9)
        alt Нет совпадений с позитивными словами ИЛИ есть исключение
            FILT-->>PARSER: отбросить (закупка НЕ сохраняется)
        else Закупка прошла фильтр
            FILT-->>PARSER: matched_keywords
            PARSER->>DB: Проверка дубликата (number+platform_id, BR-01)
            PARSER->>DB: Сохранение закупки + matched_keywords
            PARSER->>TR: POST /api/scoring/jobs (авто-пуш стадии fit, ADR-7)
            TR->>SCORING: Очередь Redis → LLM-скоринг Fit (auto-Fit)
            SCORING-->>TR: Результат стадии
            TR-->>PARSER: POST /score (возврат результата)
            PARSER->>DB: Обновление procurement_evaluations (fit_score)
            PARSER->>NOTIFY: Постадийное уведомление, если fit ≥ notify_min_fit_score
            NOTIFY-->>TS: «Найдена закупка с Fit=0.85»
        end
    end
```

## Диаграмма последовательности детальной проверки ТЗ

```mermaid
sequenceDiagram
    participant TS as " Тендеролог"
    participant UI as "Веб-интерфейс"
    participant API as "API Gateway"
    participant QUEUE as "Очередь задач"
    participant WORKER as "Воркер analysis_service"
    participant ETP as "ЭТП (Скачивание файла)"
    participant RAG as "RAG Pipeline"
    participant DB as "База данных"
    participant OBS as "LangFuse"

    Note over TS,OBS: Асинхронный процесс глубокого анализа ТЗ (On-Demand, Шаг 5–6 TO-BE v2.0)

    TS->>UI: Клик по кнопке "Проанализировать ТЗ"
    UI->>API: POST /api/procurements/analyze
    API->>DB: Проверка прав и лимитов (активный профиль)
    DB-->>API: Доступ разрешен

    API->>QUEUE: Постановка задач (fit, если не посчитан, + analysis)
    API-->>UI: {"status": "queued"}
    UI-->>TS: Статус "Идет анализ ТЗ..."

    QUEUE->>WORKER: Передача задачи analysis
    WORKER->>DB: Загрузка метаданных закупки (URL) и вопросов профиля
    DB-->>WORKER: Ссылка на файл ТЗ, вопросы профиля (компетенции)

    WORKER->>ETP: Скачивание файла ТЗ по URL (On-Demand)
    ETP-->>WORKER: Текст/Файл ТЗ

    WORKER->>RAG: Передача текста ТЗ и вопросов профиля
    RAG->>RAG: Эмбеддинги → top-k чанков → LLM-вердикт (absolute/soft/no_stop_condition)
    RAG-->>WORKER: JSON rag_report (вердикты по вопросам)

    WORKER->>OBS: Логирование трейса (cost, latency, tokens)
    WORKER->>DB: Обновление procurement_evaluations.rag_report
    WORKER->>UI: WebSocket: "Анализ ТЗ завершен"

    UI->>DB: Запрос обновленной карточки
    DB-->>UI: Данные с вердиктами
    UI-->>TS: Отображение результатов проверки

    Note over TS,UI: Целевое (этап 5): формализованные маркеры 🔴/🟡/🟢 по проверкам опыт 2571 / Минпромторг / лицензии (US-4.5)
```

## Диаграмма последовательности on-demand P(win)/Margin

```mermaid
sequenceDiagram
    participant TS as " Тендеролог"
    participant UI as "Веб-интерфейс"
    participant API as "API Gateway"
    participant TR as "Scoring Transport"
    participant PWIN as "P(win) Service"
    participant MARGIN as "Margin Service"
    participant DB as "База данных"

    Note over TS,DB: On-demand запуск стадий P(win)/Margin (автокаскад отключён)

    TS->>UI: Запрос оценки P(win)/Margin для выбранных закупок
    UI->>API: POST /api/procurements/pwin-margin
    API->>TR: Постановка задач (pwin, margin) если включены (config_score.yaml)
    API-->>UI: {"status": "queued"}
    TR->>PWIN: Очередь pwin:jobs
    PWIN-->>TR: p_win (формула: base × k_smp × k_license × …)
    TR->>MARGIN: Очередь margin:jobs
    MARGIN-->>TR: margin (НМЦК × margin_rate)
    TR-->>API: POST /score (результаты стадий)
    API->>DB: Обновление p_win/margin в procurement_evaluations
    API->>UI: Постадийные уведомления при прохождении порогов (pwin/margin)
```

# Диаграмма последовательности обратной связи и ручного управления (пост-MVP, этап 7)
```mermaid
sequenceDiagram
    participant TS as "👤 Тендеролог"
    participant UI as "Веб-интерфейс"
    participant API as "API Gateway"
    participant DB as "База данных"

    Note over TS,DB: Сценарий 1: Отклонение и добавление минус-слова (пост-MVP, Эпик 5)
    TS->>UI: Клик "Отклонить", выбор причины
    UI->>API: POST /api/tenders/{id}/reject
    API->>API: Анализ причины, извлечение минус-слова
    API-->>UI: Предложение добавить в стоп-слова
    TS->>UI: Клик "Да, добавить"
    UI->>API: POST /api/profiles/minus-words
    API->>DB: Обновление Профиля и статуса закупки
    DB-->>UI: Успех

    Note over TS,DB: Сценарий 2: Ручная корректировка скора (пост-MVP, Эпик 5)
    TS->>UI: Ручное изменение Fit (переопределение)
    UI->>API: PATCH /api/tenders/{id}/score
    API->>DB: Обновление поля fit_score
    DB-->>UI: Успех
```
