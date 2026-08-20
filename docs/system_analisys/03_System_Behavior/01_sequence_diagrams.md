# Диаграммы последовательности

## Диаграмма последовательности процесса парсинга и скоринга

```mermaid
sequenceDiagram
    participant JOB as Планировщик задач
    participant PARSER as Парсер
    participant DB as База данных
    participant ETP as ЭТП (ЕИС и др.)
    participant SCORING as Скоринг (Fit)
    participant NOTIFY as Уведомления
    participant TS as  Тендеролог

    Note over JOB,TS: Запуск фонового цикла парсинга для конкретного Тенанта

    JOB->>PARSER: Инициация задачи (user_id: 123)

    rect rgb(240, 248, 255)
    Note right of PARSER: Этап 1: Получение контекста и ранняя фильтрация
    PARSER->>DB: Загрузка Активного Профиля (user_id: 123)
    DB-->>PARSER: Ключевые слова, ОКПД2, стоп-слова, пороги

    PARSER->>ETP: Запрос закупок (передача ключевых слов/ОКПД2 в API или поиск)
    ETP-->>PARSER: Сырой список найденных закупок
    end

    loop Для каждой закупки из сырого списка
        PARSER->>DB: Проверка: закупка уже в базе? (BR-01)
        DB-->>PARSER: Статус (новая/обновленная/существующая)

        alt Новая или обновленная закупка
            PARSER->>DB: Сохранение/обновление базовых метаданных

            PARSER->>SCORING: Передача данных закупки и правил Профиля
            SCORING->>SCORING: Расчет Fit (BR-02: проверка стоп-слов, весов)

            alt Fit >= порог
                SCORING->>DB: Обновление карточки (Fit, статус)
                SCORING->>NOTIFY: Триггер уведомления
                NOTIFY-->>TS: "Найдена закупка с Fit=0.85"
            else Fit < порог
                SCORING->>DB: Сохранение с низким приоритетом
            end
        else Ошибка парсинга (CAPTCHA, 429)
            PARSER->>PARSER: Backoff (BR-06)
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
    participant WORKER as "Воркер"
    participant ETP as "ЭТП (Скачивание файла)"
    participant RAG as "RAG Pipeline"
    participant DB as "База данных"
    participant OBS as "LangFuse"

    Note over TS,OBS: Асинхронный процесс глубокого анализа ТЗ (On-Demand)

    TS->>UI: Клик по кнопке "Проанализировать ТЗ"
    UI->>API: POST /api/tenders/{id}/analyze
    API->>DB: Проверка прав и лимитов (user_id)
    DB-->>API: Доступ разрешен

    API->>QUEUE: Постановка задачи "analyze_tz"
    API-->>UI: HTTP 202 Accepted
    UI-->>TS: Статус "Идет анализ ТЗ..."

    QUEUE->>WORKER: Передача задачи
    WORKER->>DB: Загрузка метаданных закупки (URL) и условий из Профиля (user_id)
    DB-->>WORKER: Ссылка на файл ТЗ, условия проверки (опыт, Минпромторг, лицензии)

    WORKER->>ETP: Скачивание файла ТЗ по URL (On-Demand)
    ETP-->>WORKER: Текст/Файл ТЗ

    WORKER->>RAG: Передача текста ТЗ и условий из Профиля
    RAG->>RAG: Поиск релевантных чанков (без векторной БД, по заданным условиям)
    RAG->>RAG: Анализ чанков легкой LLM (поиск запретов, "не установлено" и т.д.)
    RAG-->>WORKER: JSON с результатами

    WORKER->>OBS: Логирование трейса (cost, latency, tokens)
    WORKER->>DB: Обновление карточки закупки (запись маркеров 🔴/🟡/🟢)
    WORKER->>UI: WebSocket: "Анализ ТЗ завершен"

    UI->>DB: Запрос обновленной карточки
    DB-->>UI: Данные с маркерами
    UI-->>TS: Отображение результатов проверки
```

# Диаграмма последовательности обратной связи и ручного управления
```mermaid
sequenceDiagram
    participant TS as "👤 Тендеролог"
    participant UI as "Веб-интерфейс"
    participant API as "API Gateway"
    participant DB as "База данных"

    Note over TS,DB: Сценарий 1: Отклонение и добавление минус-слова
    TS->>UI: Клик "Отклонить", выбор причины
    UI->>API: POST /api/tenders/{id}/reject
    API->>API: Анализ причины, извлечение минус-слова
    API-->>UI: Предложение добавить в стоп-слова
    TS->>UI: Клик "Да, добавить"
    UI->>API: POST /api/profiles/minus-words
    API->>DB: Обновление Профиля и статуса закупки
    DB-->>UI: Успех

    Note over TS,DB: Сценарий 2: Ручная корректировка скора
    TS->>UI: Ручное изменение Fit (переопределение)
    UI->>API: PATCH /api/tenders/{id}/score
    API->>DB: Обновление поля fit_score
    DB-->>UI: Успех
```
