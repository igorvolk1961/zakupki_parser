# Концептуальная модель данных (ER-диаграмма)

```mermaid
erDiagram
    USER ||--o{ PROFILE : "создает"
    USER ||--o{ SUBSCRIPTION : "оформляет"
    USER ||--o{ AUDIT_LOG : "генерирует"
    USER ||--o{ PROCUREMENT_EVALUATION : "выполняет оценку"

    PROFILE ||--o{ KEYWORD : "содержит"

    CUSTOMER ||--o{ PROCUREMENT : "участвует в"
    PROCEDURE_CATEGORY ||--o{ PROCUREMENT : "классифицирует"

    PROCUREMENT ||--o{ PROCUREMENT_EVALUATION : "оценивается в"

    USER {
        int user_id PK
        string email
        string password_hash
        string role
        string status
        date trial_end_date
        date created_at
    }

    PROFILE {
        int profile_id PK
        int user_id FK
        string name
        string target_etp
        string target_laws
        float min_fit_threshold
        float pwin_fit_threshold
        float margin_pwin_threshold
    }

    KEYWORD {
        int keyword_id PK
        int profile_id FK
        string word
        string type
    }

    CUSTOMER {
        int customer_id PK
        string name
        string inn
        int rating
    }

    PROCEDURE_CATEGORY {
        int category_id PK
        string name
        float pwin_coefficient
    }

    PROCUREMENT {
        int procurement_id PK
        string registry_number
        string raw_procedure_name
        int category_id FK
        string etp_name
        int customer_id FK
        float nmc
        date deadline
        string okpd2
        string description
        string source_url
        date updated_at
    }

    PROCUREMENT_EVALUATION {
        int evaluation_id PK
        int procurement_id FK
        int user_id FK
        string score_method
        float fit_score
        float p_win
        float margin
        string analysis_result
        string status
        date evaluated_at
    }

    SUBSCRIPTION {
        int sub_id PK
        int user_id FK
        string payment_gateway_id
        string status
        date start_date
        date end_date
    }

    AUDIT_LOG {
        int log_id PK
        int user_id FK
        string action_type
        string resource_id
        string ip_address
        timestamp created_at
    }
```

## Ключевые архитектурные решения в модели:

**Разделение TENDER и TENDER_EVALUATION**:
    Закупка (TENDER) — это публичные данные с ЭТП, они одни и те же для всех. А вот результат скоринга, глубокого анализа и статус («В работе», «Отклонено») уникальны для каждого пользователя. Поэтому мы вынесли пользовательскую оценку в отдельную таблицу TENDER_EVALUATION, связанную и с TENDER, и с USER. Это классический паттерн для SaaS-агрегаторов.
**Таблица KEYWORD**:
    Ключевые слова и стоп-слова вынесены в отдельную сущность, а не хранятся как JSON-строка внутри PROFILE. Это позволяет легко добавлять, удалять и индексировать их на уровне базы данных, а также в будущем реализовать поиск по словам.
**Изоляция через user_id**:
    Во всех таблицах, содержащих приватные данные (PROFILE, TENDER_EVALUATION, SUBSCRIPTION, AUDIT_LOG), присутствует user_id (FK). Это гарантирует, что при любом запросе мы можем отфильтровать данные конкретного тендеролога (реализация бизнес-правила BR-07).
**Аудит и Биллинг**:
    Таблицы AUDIT_LOG и SUBSCRIPTION добавлены для закрытия требований Эпика 7 (жизненный цикл аккаунта) и Эпика 9 (Compliance).
