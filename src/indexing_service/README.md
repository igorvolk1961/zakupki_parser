# indexing_service

Фоновая индексация закупок+документов по конфигурируемому диапазону ОКПД2
(`IndexingConfig`, `config_service.yaml`): устраняет необходимость живого
повторного обхода площадок при смене профиля фильтрации для закупок из этого
диапазона — «горячий» пересбор отдаёт результат мгновенно из
`procurement_search_index` (Postgres full-text search).

Стадия `index` — отдельная от каскада `Fit -> P(win) -> Margin`: не профильная
(нет `score`/`profile_id`), обрабатывает закупки, сохранённые системным
«индексным» профилем (`zakupki_parser.scheduler._build_system_index_ctx`), у
которых парсер уже дозагрузил `files_json` во время обхода площадки.

Цикл воркера: `ZPOPMAX index:jobs` → карточка закупки (`GET
/api/procurements/{id}`, REST, без БД) → скачивание+извлечение текста
документов (`scoring_common.tz`, тот же модуль, что и `analysis_service`) →
`LPUSH index:results` → `scoring_transport` возвращает результат в парсер
(`POST /api/procurements/{id}/index-result`), который пишет строку
`procurement_search_index` (генерируемая `tsvector`-колонка `search_tsv`,
конфигурация `simple` — см. `parser/filtering_tsquery.py`).
