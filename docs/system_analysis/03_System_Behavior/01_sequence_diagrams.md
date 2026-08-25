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

## Диаграмма последовательности детальной проверки ТЗ (двухстадийный анализ)

```mermaid
sequenceDiagram
    participant TS as " Тендеролог"
    participant UI as "Веб-интерфейс"
    participant API as "API Gateway"
    participant QUEUE as "Очередь задач"
    participant WORKER as "Воркер analysis_service"
    participant ETP as "ЭТП (Скачивание файла)"
    participant RAG as "Analysis Pipeline"
    participant DB as "База данных"
    participant OBS as "LangFuse"

    Note over TS,OBS: Асинхронный on-demand анализ ТЗ (US-4.1): Stage A (факты ТЗ) → Stage B (матчер с фактами профиля)

    TS->>UI: Клик по кнопке "Проанализировать ТЗ"
    UI->>API: POST /api/procurements/analyze
    API->>DB: Проверка прав (активный профиль)
    DB-->>API: Доступ разрешен

    API->>QUEUE: Постановка задачи analysis
    API-->>UI: {"status": "queued"}
    UI-->>TS: Статус "Идет анализ ТЗ..."

    QUEUE->>WORKER: Передача задачи analysis
    WORKER->>DB: GET /api/clients/active (вопросы профиля + факты BR-03)
    DB-->>WORKER: questions[] + facts{license_codes, experience_codes}

    WORKER->>ETP: Скачивание файла ТЗ по URL (On-Demand)
    ETP-->>WORKER: Текст/Файл ТЗ

    rect rgb(240, 248, 255)
    Note over WORKER,RAG: Stage A — извлечение фактов из ТЗ (профиль в промпт НЕ попадает)
    WORKER->>RAG: Чанки ТЗ (split_tz_sections)
    RAG->>RAG: Лексический отбор секций по паттернам sys-проверок
    RAG->>RAG: 1 batch-LLM-вызов (sys:exp_2571, sys:minprom_registry, sys:license_sro) → факты
    WORKER->>RAG: Пользовательские вопросы профиля (эмбеддинги → top-k → LLM)
    end

    rect rgb(255, 250, 235)
    Note over WORKER,RAG: Stage B — сопоставление фактов ТЗ с фактами профиля (код, без LLM)
    RAG->>RAG: matcher.py: правила BR-03/BR-04 → verdict + marker 🔴/🟡/🟢
    RAG-->>WORKER: JSON rag_report (source=system|profile, marker, facts)
    end

    WORKER->>OBS: Логирование трейса (cost, latency, tokens)
    WORKER->>DB: Обновление procurement_evaluations.rag_report
    WORKER->>UI: WebSocket: "Анализ ТЗ завершен"

    UI->>DB: Запрос обновленной карточки
    DB-->>UI: Данные с вердиктами и маркерами
    UI-->>TS: Раздел «Анализ ТЗ»: системные проверки (обязат.) + вопросы клиента

    Note over TS,UI: Реализовано: маркеры 🔴/🟡/🟢 по проверкам опыт 2571 / Минпромторг / лицензии (US-4.5, BR-03/BR-04)
```

## Диаграмма процесса двухстадийного анализа ТЗ (Stage A / Stage B)

```mermaid
flowchart LR
    TZ[Текст ТЗ] --> CH[split_tz_sections → чанки]
    CH --> EMB[Эмбеддинги чанков<br/>1 вызов на карточку]

    subgraph A[Stage A — факты ТЗ (LLM, on-demand)]
        LEX[Лексический ретривал секций<br/>по паттернам sys-проверок]
        CH --> LEX
        LEX -->|нет совпадений| SKIP[sys-вердикты no_stop_condition<br/>LLM не вызывается]
        LEX -->|релевантные секции| BATCH[1 batch-LLM-вызов<br/>batch_system.md]
        BATCH --> F1[Факты: опыт 2571,<br/>реестр Минпромторга, лицензии/СРО]
        EMB --> RETR[top-k по эмбеддингам]
        CH --> RETR
        RETR --> USERQ[Per-question LLM-вызовы<br/>пользовательские вопросы]
        USERQ --> VUSER[Вердикты пользовательских вопросов]
    end

    subgraph B[Stage B — сопоставление с профилем (код, ≈$0)]
        PF[Факты профиля:<br/>license_codes, experience_codes<br/>GET /api/clients/active → facts] --> MATCH
        F1 --> MATCH[matcher.py<br/>правила BR-03/BR-04/US-4.4]
        MATCH --> VSYS[Вердикты sys-проверок<br/>+ marker 🔴/🟡/🟢]
        MATCH -->|вид лицензии не распознан| SOFT[soft «требует проверки»]
    end

    VSYS --> RAG[rag_report: verdict, marker,<br/>source system/profile, facts]
    VUSER --> RAG
    SKIP --> VSYS
```

> Экономичность: эмбеддинги системных вопросов не вычисляются (лексический ретривал);
> на типовую карточку — 1 эмбеддинг-вызов (чанки) + 1 batch-LLM-вызов (системные проверки)
> + редкие вызовы на пользовательские вопросы; Stage B — чистый код.

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
