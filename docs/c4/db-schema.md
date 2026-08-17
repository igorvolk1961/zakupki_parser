# Схема базы данных

Схема БД парсера (PostgreSQL). Миграции — Liquibase (`../../docker/liquibase/changelog`),
ORM-модель — `../../src/zakupki_parser/storage/db.py`.

```mermaid
erDiagram
    CUSTOMERS {
        bigint id PK "автоинкремент"
        text name "наименование заказчика"
        text normalized_name "нормализованное имя (дедупликация), UNIQUE"
        varchar(12) inn "ИНН (заполняется универсальным механизмом)"
        double rating "рейтинг заказчика (заполняется через API внешним сервисом)"
        timestamptz created_at "server_default now()"
        timestamptz updated_at "server_default now(), onupdate"
    }

    PLATFORMS {
        bigint id PK "автоинкремент"
        varchar(128) platform_id "ключ платформы, UNIQUE (совпадает с configs/dom/<platform_id>.yaml)"
        text name "официальное наименование площадки"
        text url "главная страница площадки"
        timestamptz created_at "server_default now()"
        timestamptz updated_at "server_default now(), onupdate"
    }

    PROCEDURE_TYPES {
        bigint id PK "автоинкремент"
        text name "наименование типа процедуры"
        text normalized_name "нормализованное имя (дедупликация), UNIQUE"
        boolean is_canonical "true — канон из предзагруженного справочника; false — «сырое» значение площадки"
        timestamptz created_at "server_default now()"
        timestamptz updated_at "server_default now(), onupdate"
    }

    PROCEDURE_TYPE_MAPPINGS {
        bigint id PK "автоинкремент"
        varchar(128) platform_id "ключ площадки (platforms.platform_id)"
        text native_name "родное значение на площадке (purchase_type)"
        text normalized_name "нормализованное родное значение, UNIQUE(platform_id, normalized_name)"
        bigint procedure_type_id FK "канонический тип (procedure_types.id, CASCADE)"
        timestamptz created_at "server_default now()"
        timestamptz updated_at "server_default now(), onupdate"
    }

    PROCUREMENTS {
        bigint id PK "автоинкремент"
        varchar(64) number "реестровый номер заявки"
        varchar(128) platform_id "ключ платформы-источника (platforms.platform_id)"
        varchar(1024) url "ссылка на закупку"
        bigint customer_id FK "ссылка на заказчика (customers.id, SET NULL)"
        bigint procedure_type_id FK "ссылка на тип процедуры (procedure_types.id, SET NULL)"
        varchar(16) law "закон: 44-ФЗ / 223-ФЗ"
        text subject "предмет закупки"
        double nmck "начальная макс. цена контракта"
        timestamptz publication_date "дата публикации (из «с …»)"
        timestamptz update_date "дата обновления закупки («Обновлено» на ЕИС)"
        timestamptz deadline "срок приёма заявок"
        text execution_term "срок исполнения"
        double security_amount "обеспечение заявки/контракта"
        varchar(16) security_amount_unit "единица измерения обеспечения"
        double advance "аванс"
        text okpd2_codes "коды ОКПД2 (один или несколько, через запятую)"
        text kpgz_codes "коды КПГЗ (один или несколько, через запятую)"
        jsonb files_json "файлы: [{name, url скачивания с ЭТП}]"
        double score "скоринг Fit × P(win) × Margin"
        double fit_score "множитель Fit (default 0..1 по ОКПД2; external 0..1 нормализованный)"
        varchar(64) score_method "default | fit | pwin | margin"
        double embedding_similarity "косинусная близость 0..1 (Giga Embedder); NULL если ветка выключена"
        boolean is_active "активна ли закупка (false: завершённая/отменённая и т.п.)"
        jsonb detail_json "полный набор переменных карточки"
        timestamptz created_at "server_default now()"
        timestamptz updated_at "server_default now(), onupdate"
    }

    PROCUREMENTS }o--|| CUSTOMERS : "customer_id (FK, SET NULL)"
    PROCUREMENTS }o--|| PROCEDURE_TYPES : "procedure_type_id (FK, SET NULL)"
    PROCEDURE_TYPE_MAPPINGS }o--|| PROCEDURE_TYPES : "procedure_type_id (FK, CASCADE)"
    PROCUREMENTS }o--o| PLATFORMS : "platform_id (по ключу)"
    PROCEDURE_TYPE_MAPPINGS }o--o| PLATFORMS : "platform_id (по ключу)"
```

## Замечания
- **Пять таблиц**: `procurements`, справочники `customers` (ADR-4),
  `procedure_types`, `procedure_type_mappings` и `platforms`. Заказчик нормализован:
  вместо денормализованной колонки `customer` — FK `customer_id` (при удалении
  заказчика — `SET NULL`). Тип процедуры (`purchase_type` из карточки списка)
  нормализован в `procedure_types` (миграция 1.20).
- **Платформы** (миграция 1.21): справочник `platforms` (натуральный ключ
  `platform_id`, официальное имя, главная страница; сид из `configs/dom/*.yaml`).
  Колонка `procurements.source_platform` переименована в `platform_id` — единое
  именование ключа платформы во всех таблицах и конфигах. FK на `procurements`
  намеренно нет: ключ стабильный, набор платформ задаётся конфигом; join по ключу
  (официальное имя/URL отдаются через API как `platform_name`/`platform_url`).
- **Типы процедур — гибрид «предзагруженный справочник + fallback»**:
  - `procedure_types` — канонический справочник (способы 44-ФЗ/223-ФЗ, засеян из
    дропдауна «Тип процедуры» fabrikant и способов ЕИС) + «сырые» значения площадок
    без маппинга (`is_canonical=false`);
  - `procedure_type_mappings` — предзагруженное соответствие
    «площадка + родное значение (native_name) -> канонический тип» (например
    «Электронный запрос котировок» roseltorg -> «Запрос котировок»);
  - резолв при сохранении закупки: маппинг -> тип по имени -> find-or-create
    (новый тип логируется: «нужен маппинг»).
- **Справочник заказчиков** `customers`: `name`, `normalized_name` (ключ дедупликации,
  UNIQUE `uq_customers_normalized_name`), `inn`, `rating` (заполняется через API
  внешним сервисом).
- Дата последней обработанной записи **не хранится** в state-файле: порог берётся
  из БД (`MAX(update_date)` по площадке), а при отсутствии записей — из
  `default_cutoff_days` в `config_service.yaml`.
- **Защита от дубликатов**: уникальный констрейнт `uq_procurement_number_platform`
  на `(number, platform_id)`.
- **Индексы**: `ix_procurements_created_at` по `created_at`,
  `ix_procurements_customer_id` по `customer_id`,
  `ix_procurements_procedure_type_id` по `procedure_type_id`,
  `ix_procedure_type_mappings_platform` по `platform_id`,
  `ix_platforms_platform_id` по `platform_id`.
- `score_method`: `default | fit | pwin | margin` (каскад Fit → P(win) → Margin, ADR-7).
- `fit_score` (миграция 1.15): множитель Fit — дефолтный (0..1 из `fit_table` по ОКПД2)
  или нормализованный внешний (0..1). `embedding_similarity` (миграция 1.16) —
  косинусная близость ветки Giga Embedder. `is_active` (миграция 1.14) — активна ли
  закупка по статусу; выставляется парсером по текстовому `status` из `detail_json`
  (`list_config.active_statuses`). Текущая дата (срок актуальности `deadline`) при
  записи не учитывается — она применяется на стороне клиента (фильтр `active`
  в `list_procurements`, эффективная активность в API-ответах).
- `detail_json` хранит весь набор извлечённых переменных карточки для аналитики.

## Ключевые SQL-запросы
- Проверка дубликата перед вставкой:
  ```sql
  SELECT id FROM procurements
  WHERE number = $1 AND platform_id = $2;
  ```
- Дата последней обработанной записи по площадке (порог):
  ```sql
  SELECT MAX(update_date) FROM procurements WHERE platform_id = $1;
  ```
- Одинарная/множественная вставка исключает повтор:
  ```sql
  INSERT INTO procurements (...) VALUES (...)
  ON CONFLICT (number, platform_id) DO NOTHING;
  ```
