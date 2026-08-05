# Схема базы данных

Схема БД парсера (PostgreSQL). Миграции — Liquibase (`../../docker/liquibase/changelog`),
ORM-модель — `../../src/zakupki_parser/storage/db.py`.

```mermaid
erDiagram
    PROCUREMENTS {
        BIGSERIAL id PK "автоинкремент"
        VARCHAR(64) number "реестровый номер заявки"
        VARCHAR(128) source_platform "ключ площадки"
        VARCHAR(1024) url "ссылка на закупку"
        TEXT customer "заказчик"
        VARCHAR(16) law "закон: 44-ФЗ / 223-ФЗ"
        TEXT subject "предмет закупки"
        FLOAT nmck "начальная макс. цена контракта"
        TIMESTAMPTZ publication_date "дата публикации (из «с …»)"
        TEXT dates "исходная строка «с … до … (МСК)»"
        TIMESTAMPTZ deadline "срок приёма заявок"
        TEXT execution_term "срок исполнения"
        FLOAT security_amount "обеспечение заявки/контракта"
        FLOAT advance "аванс"
        TEXT okpd2_codes "коды ОКПД2 (один или несколько, через запятую)"
        TEXT technical_spec_url "URL скачивания ТЗ с ЭТП (или URL объекта в MinIO)"
        TEXT technical_spec_key "ключ объекта ТЗ в хранилище (при скачивании)"
        TEXT technical_spec_name "имя файла технического задания"
        JSONB files_json "остальные файлы: [{name, url скачивания с ЭТП}]"
        FLOAT score "скоринг Fit × P(win) × Margin"
        VARCHAR(64) score_method "default | external | calculating | deadline_expired"
        TEXT kpgz_codes "коды КПГЗ (один или несколько, через запятую)"
        JSONB detail_json "полный набор переменных карточки"
        TIMESTAMPTZ created_at "server_default now()"
        TIMESTAMPTZ updated_at "server_default now(), onupdate"
    }

    %% Схема имеет одну таблицу. Дата последней обработанной записи
    %% НЕ хранится в БД — она получается SQL-запросом, а порог по умолчанию
    %% хранится в state-файле data/last_seen.json и конфиге config_service.yaml.
```

## Замечания
- **Таблица одна** — `procurements`. Отдельной таблицы дат последней обработки нет
  (она получается SQL-запросом; текущий порог — в `data/last_seen.json`).
- **Защита от дубликатов**: уникальный констрейнт `uq_procurement_number_platform`
  на `(number, source_platform)`.
- **Индекс** `ix_procurements_created_at` по `created_at`.
- `detail_json` хранит весь набор извлечённых переменных карточки для аналитики.

## Ключевые SQL-запросы
- Проверка дубликата перед вставкой:
  ```sql
  SELECT id FROM procurements
  WHERE number = $1 AND source_platform = $2;
  ```
- Дата последней обработанной записи по площадке:
  ```sql
  SELECT MAX(updated_at) FROM procurements WHERE source_platform = $1;
  ```
- Одинарная/множественная вставка исключает повтор:
  ```sql
  INSERT INTO procurements (...) VALUES (...)
  ON CONFLICT (number, source_platform) DO NOTHING;
  ```
