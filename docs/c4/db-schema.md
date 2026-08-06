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
        TIMESTAMPTZ update_date "дата обновления закупки («Обновлено» на ЕИС)"
        TIMESTAMPTZ deadline "срок приёма заявок"
        TEXT execution_term "срок исполнения"
        FLOAT security_amount "обеспечение заявки/контракта"
        VARCHAR(16) security_amount_unit "единица измерения обеспечения"
        FLOAT advance "аванс"
        TEXT okpd2_codes "коды ОКПД2 (один или несколько, через запятую)"
        TEXT technical_spec_url "URL файла ТЗ (адрес скачивания с ЭТП)"
        TEXT technical_spec_name "имя файла технического задания"
        JSONB files_json "остальные файлы: [{name, url скачивания с ЭТП}]"
        FLOAT score "скоринг Fit × P(win) × Margin"
        VARCHAR(64) score_method "default | external | calculating | deadline_expired"
        TEXT kpgz_codes "коды КПГЗ (один или несколько, через запятую)"
        JSONB detail_json "полный набор переменных карточки"
        TIMESTAMPTZ created_at "server_default now()"
        TIMESTAMPTZ updated_at "server_default now(), onupdate"
    }
```

## Замечания
- **Таблица одна** — `procurements`. Дата последней обработанной записи **не хранится**
  в state-файле: порог берётся из БД (`MAX(update_date)` по площадке), а при отсутствии
  записей — из `default_cutoff_days` в `config_service.yaml`.
- **Защита от дубликатов**: уникальный констрейнт `uq_procurement_number_platform`
  на `(number, source_platform)`.
- **Индекс** `ix_procurements_created_at` по `created_at`.
- `detail_json` хранит весь набор извлечённых переменных карточки для аналитики.
- Справочник заказчиков (`customers`, рейтинг) — **будущая работа** по ADR-4
  (нормализация при разработке скорингового сервиса), ещё не реализована.

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
