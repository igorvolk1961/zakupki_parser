# План разработки парсера площадок закупок

Дата начала: 2026-08-03. Статусы: ✅ выполнено · 🟡 частично · ⬜ в работе/запланировано.

## 1. Каркас проекта
- [x] Инициализация `uv`-проекта, `pyproject.toml`, dev-группа (pytest, ruff, mypy)
- [x] Настройка `[tool.ruff]` (lint+format) и `[tool.mypy]` (strict)
- [x] `.gitignore`, структура каталогов

## 2. Конфигурация (всё через YAML)
- [x] Модели конфигов (pydantic): parser/dom/service/logging/score
- [x] Декомпозиция моделей в пакет `config/models/`
- [x] Загрузка и валидация (`config/loader.py`), env-переопределения (`ZAKUPKI_DB_DSN`)
- [x] `config_parser.yaml` — браузер и антиблок (UA, viewport, задержки, stealth, `ignore_https_errors`)
- [x] `configs/dom/<platform_id>.yaml` — селекторы, переменные, сортировка, фильтры (ADR-1)
- [x] `config_service.yaml` — аналитические настройки (сайты, пороги дат, критерии поиска, stop-условия)
- [x] `config_ops.yaml` — эксплуатационные настройки (таймер, БД, уведомления, export_dir, circuit breaker)
- [x] `config_score.yaml` — скоринг (fit_table, default_fit, p_win; дефолтный score в парсере)
- [x] `config_log.yaml` — логирование (файл, `truncate_on_start`)

## 3. Браузер и антиблок
- [x] Playwright (полный Chromium вместо headless-shell — сайт блокирует headless-shell)
- [x] Stealth-скрипты (webdriver, языки/плагины), реалистичный UA
- [x] Вежливые задержки (4–12 с), лимит запросов
- [x] Персистентная сессия (куки), `ignore_https_errors` для ЕИС
- [x] Экспоненциальный backoff для операций браузера и БД (`retry.py` + circuit breaker)
- [ ] 🟡 Ротация прокси и пул IP (при появлении инфраструктуры)

## 4. Движок парсинга
- [x] Оркестратор основного алгоритма (`parser/orchestrator.py`)
- [x] Lister: вход, сортировка (по дате публикации), фильтры, пагинация
- [x] URL-пагинация `page_param`/`page_size` (b2b_center, etpgpb, lot_online, zakupki_mos)
- [x] Потолок страниц за проход `parser.max_list_pages` (защита от вечного цикла)
- [x] Ранний пропуск прохода по числу результатов (`total_results_selector`/`total_results_regex`,
      `lister.extract_total_results`, `repository.count`) — relevance-режим без пост-фильтра
- [x] Пропуск уже сохранённых закупок (`repository.known_numbers`) — детали не открываем
- [x] Extractor: извлечение по конфигу (selector/index/regex/обработчики)
- [x] Обработчики значений: strip/money/float/int/date/datetime/law/regex/pub_date/deadline
- [x] Детальные страницы в отдельной вкладке (список не теряется)
- [x] Стоп-порог по календарному дню (`is_older_than_cutoff`), порог из БД (`MAX(update_date)`)
- [x] Метаданный режим файлов: имена+URL с ЭТП (ТЗ — 2 поля, остальные — `files_json`)
- [x] Скоринг: default/deadline_expired (внешний — через конвейер, ADR-7)
- [x] Конвейер внешнего скоринга (ADR-7): `scoring_transport` + `scoring_service` + Redis
      (очередь по дефолтному score, возврат результата через `POST /score`)
- [x] Выпил deprecated-путей (`ExternalScoreClient` `before_save`/`worker`,
      `run_scoring_worker`) и дублирующей fit-таблицы транспорта (ADR-7)
- [x] Авто-пуш задания в транспорт после сохранения закупки (шаг 1 ADR-7)
- [x] Пороговое уведомление `notify_min_fit_score` + отложенная отправка в `POST /score`
      (шаг 6 ADR-7)
- [x] LLM-пайплайн `scoring_service`: Fit → Judge → refine (`num_refine_rounds`) →
      уточнение по тексту ТЗ (`tz_review`) → Score = Fit × P(win) × Margin
      (заглушка `score_use_stub` снята)
- [x] Ветка векторной близости **Giga Embedder** (`modules/giga_embedder.py`:
      EmbeddingsGigaR, OAuth `RqUID`, чанкинг; `giga_embedding_alpha`) — отображение
      на карточке закупки (`embedding_similarity`)
- [x] Оценка качества и regression-гейт (`scoring_service/eval/`: metrics, dataset,
      evaluate; CLI `evaluate`, `--compare` с порогами MAE/RMSE/accuracy/Spearman)

## 5. Файлы (метаданные, без скачивания)
- [x] Парсер НЕ скачивает файлы: в БД сохраняются имя и URL скачивания с ЭТП
  (ТЗ — `technical_spec_name`/`technical_spec_url`, остальные — `files_json`).
- [x] Определение ТЗ по имени файла (по умолчанию «техническое задание»).
- [x] Глубокая обработка файлов (PDF/DOCX/ZIP, поиск ТЗ) — внешний сервис (ADR-5).
- ~~Скачивание файлов в хранилище (MinIO/local) — удалено~~

## 6. Хранилище (PostgreSQL + SQLAlchemy + Liquibase)
- [x] ORM (SQLAlchemy 2.x async) и репозиторий (upsert, дубликаты, чтение)
- [x] Миграции Liquibase (1.0–1.16): колонки, даты, score, `fit_score`/`embedding_similarity`/
      `is_active`, `technical_spec_*`, комментарии
- [x] Поля task.md: number, customer, law, subject, nmck, deadline, okpd2, ТЗ, files_json
- [x] Колонки security_amount/advance (обеспечение/аванс), security_amount_unit, update_date
- [ ] 🟡 Заполнение execution_term/kpgz_codes/security_amount/advance на ЕИС (сделано: okpd2, обеспечение+единица, срок; осталось: kpgz_codes/advance)
- [x] Нормализация БД по ADR-4: таблица `customers` (имя/нормализованное имя/ИНН/рейтинг),
      `procurements.customer_id` (FK, `customer` удалена), миграция 1.13 (create+backfill+drop),
      эндпоинты `/api/customers` и `POST /api/customers/{id}/rating`;
      ИНН — универсальный механизм (из org-ссылки или org-страницы, ADR-1/ADR-4).

## 7. Circuit Breaker и graceful degradation
- [x] `circuit.py` (CLOSED/OPEN/HALF_OPEN), отдельные инстансы для сайта и БД
- [x] Классификация ошибок БД (транзиентные/данные/дубликаты), retry с backoff
- [x] Сброс CB на дубликатах, обновление даты последней обработки при достижении порога
- [ ] ⬜ Тест с имитацией сбоя БД (retry + открытие CB)

## 8. Фильтрация закупок
- [x] URL-фильтр (ADR-2): mos.ru — `filter` JSON, ЕИС — query-параметры
- [x] Обобщённый механизм `criteria_map` (json_path/query_param)
- [x] Резолв ОКПД2 (точный код → потомки → предок) + маппинг `okpd2_tree.json` (mos.ru)
- [x] Маппинг ОКПД2 → id ЕИС (62→8873937, 63→8873938), эндпоинт `children.html`,
      файл `docs/codes/gov_okpd2_tree.json`
- [x] ОКПД2-фильтр ЕИС: полный список id/кодов поддерева через `criteria_map`
      (`query_params` okpd2Ids + okpd2IdsCodes) — проверено на живом сайте (687 записей)
- [ ] 🟡 DOM-шаги фильтров для площадок с панелью (не используется для mos.ru/ЕИС)

## 9. Площадки
- [x] Портал поставщиков Москвы (zakupki.mos.ru): Реестр закупок, детали, файлы
- [x] ЕИС (zakupki.gov.ru): список, детали (blockInfo__section), URL-фильтр, «Обновлено»
- [x] ЕИС: 223-ФЗ (11-значные номера), файлы закупок (`documents.html`)
- [ ] 🟡 ЕИС: детальные поля по типам извещений (ea20 проработан; ezt20/zk20/ok504 — уточнить)
- [ ] ⬜ Коммерческие ЭТП (Роселторг, B2B-Center и др.)

## 10. API-сервис (FastAPI)
- [x] `GET /health`
- [x] `GET /api/procurements` (фильтры + пагинация), `GET /api/procurements/{id}`
- [x] `GET /api/procurements/{id}/technical-spec` (скачивание ТЗ)
- [x] `POST /api/procurements/{id}/score` (возврат результата из транспорта; обновляет score
      и `fit_score`, при `fit_score ≥ notify_min_fit_score` отправляет уведомление — ADR-7)
- [x] `POST /api/procurements/export` (выгрузка БД в CSV, каталог `export_dir`; кнопка «Выгрузить CSV»)
- [x] Управление парсером из web-демо: `POST /api/parser/start|stop`, `GET /api/parser/status`
- [x] Очистка БД: `POST /api/db/clear` (только при остановленном парсере)
- [x] Конфиг: `GET/PUT /api/config` (аналитические), `GET /api/config/threshold`; WebSocket `/ws`
- [ ] 🟡 Эндпоинт чистки БД по фильтрам/возрасту записи (`DELETE /api/procurements`) —
      при удалении записей удалять и связанные файлы из хранилища (S3/local), ссылки на которые
      хранятся в `technical_spec_url` и `files_json` (сейчас — только полная очистка `POST /api/db/clear`)

## 11. Уведомления
- [x] Бэкенды Telegram / MAX / webhook (реальный HTTP POST, `notify.py`)
- [x] `insecure_tls` для MAX-уведомлений, токены из env (`ZAKUPKI_TELEGRAM_TOKEN`, `ZAKUPKI_MAX_TOKEN`)
- [x] Порог `notify_min_fit_score` (уведомление только при `fit_score ≥ notify_min_fit_score`,
      отложено до `POST /score`)

## 12. Тесты и CI
- [x] Unit: обработчики, конфиг, circuit breaker, дата последней обработки, stop-условия, ОКПД2, скоринг, retry
- [x] Unit: извлечение общего числа результатов (`tests/unit/test_total_results.py`)
- [x] Integration: фикстуры (mos.ru, ЕИС 44-ФЗ/223-ФЗ, documents.html), репозиторий, API (PostgreSQL)
- [x] GitHub Actions CI: ruff, mypy, pytest (сервисный postgres), docker build
- [x] Фикстуры реальных страниц (list/detail mos.ru и ЕИС)
- [ ] ⬜ Полный оркестратор против локального HTTP-сервера
- [ ] ⬜ Тест имитации сбоя БД

## 13. Docker
- [x] `docker/Dockerfile` (python:3.12-slim + uv + playwright chromium)
- [x] `docker/docker-compose.yml`: db, liquibase, minio, parser, api
- [ ] 🟡 Проверка полного стека на реальном прогоне

## 14. Документация
- [x] `README.md`
- [x] `specification.md`
- [x] `TODO.md`
- [x] C4-диаграммы (context/container/component, db-schema, sequence) — Mermaid flowchart
- [x] `docs/adr.md` (ADR-1…7, включая ADR-7 — конвейер скоринга через транспорт)
- [x] `docs/codes/okpd2_tree.json` (маппинг ОКПД2 mos.ru)

## 15. Code Review и оптимизация кода
- [x] Первичный code review и устранение замечаний (дата последней обработки при пороге, сброс CB,
      path traversal, detail_json из финальной записи)
- [ ] 🟡 Регулярный code review после каждой крупной фичи (ЕИС, критерии поиска, скоринг)
- [ ] ⬜ Оптимизация цикла парсинга:
- [ ] ⬜ Оптимизация работы с БД: батчи, индексы под типовые запросы, пагинация.
- [ ] ⬜ Аудит асинхронных утечек и таймаутов (page.request, сессии, пул БД).
- [ ] ⬜ Устранение дублирования кода между площадками (общие хелперы селекторов/дат).

## Текущий фокус
- Конвейер внешнего скоринга (ADR-7) реализован и работает без заглушки: авто-пуш из
  парсера в транспорт, LLM-пайплайн scoring_service (Fit → Judge → refine → уточнение
  по ТЗ), параллельная ветка Giga-эмбеддингов, возврат через `POST /score`,
  отложенное пороговое уведомление (`notify_min_fit_score`). Далее — подбор гиперпараметров
  и порогов regression-гейта, оценка влияния ветки эмбеддингов (`giga_embedding_alpha`).
- Коммерческие ЭТП: верификация селекторов (list/detail) и включение по одной площадке.
- ЕИС: `kpgz_codes`/`advance` на детальных страницах, детальные поля по типам извещений
  (ezt20/zk20/ok504).
- Авторизация администраторов в web-приложении.
