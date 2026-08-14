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

    PROCUREMENTS {
        bigint id PK "автоинкремент"
        varchar(64) number "реестровый номер заявки"
        varchar(128) source_platform "ключ площадки"
        varchar(1024) url "ссылка на закупку"
        bigint customer_id FK "ссылка на заказчика (customers.id, SET NULL)"
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
        varchar(64) score_method "default | external | deadline_expired"
        double embedding_similarity "косинусная близость 0..1 (Giga Embedder); NULL если ветка выключена"
        boolean is_active "активна ли закупка (false: завершённая/отменённая и т.п.)"
        jsonb detail_json "полный набор переменных карточки"
        timestamptz created_at "server_default now()"
        timestamptz updated_at "server_default now(), onupdate"
    }

    PROCUREMENTS }o--|| CUSTOMERS : "customer_id (FK, SET NULL)"
```

## Замечания
- **Две таблицы**: `procurements` и справочник `customers` (ADR-4). Заказчик
  нормализован: вместо денормализованной колонки `customer` — FK `customer_id`
  (при удалении заказчика — `SET NULL`).
- **Справочник заказчиков** `customers`: `name`, `normalized_name` (ключ дедупликации,
  UNIQUE `uq_customers_normalized_name`), `inn`, `rating` (заполняется через API
  внешним сервисом).
- Дата последней обработанной записи **не хранится** в state-файле: порог берётся
  из БД (`MAX(update_date)` по площадке), а при отсутствии записей — из
  `default_cutoff_days` в `config_service.yaml`.
- **Защита от дубликатов**: уникальный констрейнт `uq_procurement_number_platform`
  на `(number, source_platform)`.
- **Индексы**: `ix_procurements_created_at` по `created_at`,
  `ix_procurements_customer_id` по `customer_id`.
- `score_method`: `default | external | deadline_expired` (значение `calculating`
  удалено вместе с воркером внешнего скоринга, ADR-7).
- `fit_score` (миграция 1.15): множитель Fit — дефолтный (0..1 из `fit_table` по ОКПД2)
  или нормализованный внешний (0..1). `embedding_similarity` (миграция 1.16) —
  косинусная близость ветки Giga Embedder. `is_active` (миграция 1.14) — активна ли
  закупка; выставляется парсером по текстовому `status` из `detail_json`.
- `detail_json` хранит весь набор извлечённых переменных карточки для аналитики.

## Ключевые SQL-запросы
- Проверка дубликата перед вставкой:
  ```sql
  SELECT id FROM procurements
  WHERE number = $1 AND source_platform = $2;
  ```
- Дата последней обработанной записи по площадке (порог):
  ```sql
  SELECT MAX(update_date) FROM procurements WHERE source_platform = $1;
  ```
- Одинарная/множественная вставка исключает повтор:
  ```sql
  INSERT INTO procurements (...) VALUES (...)
  ON CONFLICT (number, source_platform) DO NOTHING;
  ```
