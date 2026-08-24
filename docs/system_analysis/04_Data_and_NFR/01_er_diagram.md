# Концептуальная модель данных (ER-диаграмма)

> Источник: фактическая схема БД (`src/zakupki_parser/storage/db.py`, Liquibase-миграции
> 1.29–1.35) и целевая модель TenderSearch (`specification.md` §14.2).
> Раздел 1 — **текущая (фактическая) модель**; раздел 2 — **целевая модель (пост-MVP)**
> с сущностями `SUBSCRIPTION`, `AUDIT_LOG` и наполнением `PROCEDURE_CATEGORIES`.

## 1. Фактическая модель данных (реализована, этапы 1–3)

```mermaid
erDiagram
    USERS ||--o{ PROFILES : "владеет"
    PROFILES ||--o{ KEYWORDS : "содержит"
    PROFILES ||--o{ PROCUREMENT_EVALUATIONS : "оценивает"
    PROCUREMENTS ||--o{ PROCUREMENT_EVALUATIONS : "оценивается в"
    CUSTOMERS ||--o{ PROCUREMENTS : "участвует в"
    PROCEDURE_TYPES ||--o{ PROCUREMENTS : "классифицирует"
    PROCEDURE_TYPE_MAPPINGS ||--o{ PROCEDURE_TYPES : "резолвится в"
    PLATFORMS ||--o{ PROCUREMENTS : "площадка (join по platform_id)"

    USERS {
        int id PK
        string username
        string email
        string password_hash
        string role
        datetime created_at
    }

    PROFILES {
        int id PK
        int user_id FK
        string name
        bool enabled
        bool is_active
        float min_fit_threshold
        jsonb target_etp
        jsonb target_laws
        jsonb okpd_codes
        float nmck_min
        float nmck_max
        bool active_only
        text competencies
        jsonb questions
        datetime created_at
    }

    KEYWORDS {
        int id PK
        int profile_id FK
        string word
        string type
    }

    CUSTOMERS {
        int id PK
        string name
        string normalized_name
        string inn
        float rating
    }

    PROCEDURE_TYPES {
        int id PK
        string name
        string normalized_name
        bool is_canonical
    }

    PROCEDURE_TYPE_MAPPINGS {
        int id PK
        string platform_id
        string native_name
        string normalized_name
        int procedure_type_id FK
    }

    PLATFORMS {
        int id PK
        string platform_id
        string name
        string url
    }

    PROCEDURE_CATEGORIES {
        int id PK
        string name
        float pwin_coefficient
    }

    PROCUREMENTS {
        int id PK
        string number
        string platform_id
        string url
        int customer_id FK
        int procedure_type_id FK
        int category_id FK
        string law
        string subject
        float nmck
        datetime publication_date
        datetime update_date
        datetime deadline
        string execution_term
        float security_amount
        string security_amount_unit
        float advance
        string okpd2_codes
        string kpgz_codes
        jsonb files_json
        datetime scoring_queued_at
        bool is_active
        jsonb detail_json
    }

    PROCUREMENT_EVALUATIONS {
        int id PK
        int procurement_id FK
        int profile_id FK
        float fit_score
        float score
        float p_win
        float margin
        string score_method
        jsonb rag_report
        jsonb matched_keywords
        string status
        string rejection_reason
    }
```

### Ключевые решения фактической модели

- **Разделение PROCUREMENT и PROCUREMENT_EVALUATIONS**: закупка — публичные данные ЭТП
  (одни для всех), а результат скоринга, RAG-отчёт и статус уникальны для
  **профиля фильтрации** (профиль — пользователю). Оценки хранятся только в
  `procurement_evaluations`; колонок score-* в `procurements` нет (динамические
  атрибуты подкладываются репозиторием при выдаче под активный профиль).
- **Таблица KEYWORDS** — канонический источник слов профиля (`keyword`/`exclusion`);
  JSONB-массивов слов в профиле нет (миграция 1.30).
- **Критерии поиска в профиле** (миграция 1.33): `okpd_codes`, `nmck_min/max`,
  `active_only` принадлежат профилю, а не глобальному конфигу.
- **Ключи уникальности**: `procurements (number, platform_id)`;
  `procurement_evaluations (procurement_id, profile_id)`; `keywords (profile_id, word, type)`.
- **Справочники**: `customers`, `procedure_types` (+ `procedure_type_mappings`),
  `platforms`; `procedure_categories` — заглушка (этап 1).
- **Изоляция через user_id / profile_id** (BR-07): все таблицы с приватными данными
  (`profiles`, `procurement_evaluations`) привязаны к пользователю/профилю; tenant-скоуп
  реализован в репозитории.

## 2. Целевая модель (пост-MVP, этапы 6–10)

Дополнения к фактической модели (миграции этапов 6/9/10; «заглушки» созданы на этапе 1):

```mermaid
erDiagram
    USERS ||--o{ SUBSCRIPTIONS : "оформляет"
    USERS ||--o{ AUDIT_LOG : "генерирует"
    PROCEDURE_CATEGORIES ||--o{ PROCUREMENTS : "классифицирует (pwin_coefficient)"
    USERS {
        int id PK
        string username
        string email
        string password_hash
        string role
        string status
        date trial_end_date
        datetime last_activity_at
        datetime delete_notified_at
    }
    SUBSCRIPTIONS {
        int id PK
        int user_id FK
        string status
        date start_date
        date end_date
    }
    AUDIT_LOG {
        int id PK
        int user_id FK
        string action_type
        string resource_id
        string ip_address
        timestamp created_at
    }
    PROCEDURE_CATEGORIES {
        int id PK
        string name
        float pwin_coefficient
    }
```

- `users.status` (`trial|active|frozen|deleted`), `trial_end_date` (= now()+10 лет) —
  жизненный цикл аккаунта (BR-05, этап 6); подтверждение email — целевая модель, в MVP
  не реализуется.
- `subscriptions` — оплата/подписка (в MVP **не обязательна**, заглушка).
- `audit_log` — аудит критичных действий пользователей с IP (US-9.4, этапы 6/9/10).
- `procedure_categories.pwin_coefficient` — наполняется и используется в формуле P(win)
  (этап 10 / калибровка скоринга).
