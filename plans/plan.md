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
- [x] `config_dom.yaml` — селекторы, переменные, сортировка, фильтры (ADR-1)
- [x] `config_service.yaml` — таймер, сайты, пороги, флаги, БД, webhook, stop-условия
- [x] `config_score.yaml` — скоринг (method, fit_table, external_service_url, external_call_mode)
- [x] `config_log.yaml` — логирование (файл, `truncate_on_start`)

## 3. Браузер и антиблок
- [x] Playwright (полный Chromium вместо headless-shell — сайт блокирует headless-shell)
- [x] Stealth-скрипты (webdriver, языки/плагины), реалистичный UA
- [x] Вежливые задержки (4–12 с), лимит запросов
- [x] Персистентная сессия (куки), `ignore_https_errors` для ЕИС
- [ ] 🟡 Ротация прокси и пул IP (при появлении инфраструктуры)
- [ ] ⬜ Экспоненциальный backoff для операций браузера (сейчас — только circuit breaker)

## 4. Движок парсинга
- [x] Оркестратор основного алгоритма (`parser/orchestrator.py`)
- [x] Lister: вход, сортировка (по дате публикации), фильтры, пагинация
- [x] Extractor: извлечение по конфигу (selector/index/regex/обработчики)
- [x] Обработчики значений: strip/money/float/int/date/datetime/law/regex/pub_date/deadline
- [x] Детальные страницы в отдельной вкладке (список не теряется)
- [x] Стоп-порог по календарному дню (`is_older_than_cutoff`), last_seen
- [x] Метаданный режим файлов: имена+URL с ЭТП (ТЗ — 2 поля, остальные — `files_json`)
- [x] Скоринг: default/external/worker/calculating/deadline_expired

## 5. Скачивание файлов и хранилище
- [x] Скачивание через `page.request` (не `page.goto`)
- [x] Хранилище `ObjectStore`: local (documents) и S3/MinIO (boto3)
- [x] Фильтр «только техническое задание» (`download_technical_spec_only` + keywords)
- [x] Удаление опустевшей папки, сохранение ТЗ-файла
- [x] Защита от path traversal (имя из Content-Disposition)

## 6. Хранилище (PostgreSQL + SQLAlchemy + Liquibase)
- [x] ORM (SQLAlchemy 2.x async) и репозиторий (upsert, дубликаты, чтение)
- [x] Миграции Liquibase (1.0–1.7): колонки, даты, score, `technical_spec_*`, комментарии
- [x] Поля task.md: number, customer, law, subject, nmck, deadline, okpd2, ТЗ, files_json
- [x] Колонки security_amount/advance (обеспечение/аванс)
- [ ] 🟡 Заполнение execution_term/kpgz_codes/security_amount/advance на ЕИС (в работе)
- [ ] ⬜ Нормализация БД (справочники) — ADR-4, при разработке скорингового сервиса

## 7. Circuit Breaker и graceful degradation
- [x] `circuit.py` (CLOSED/OPEN/HALF_OPEN), отдельные инстансы для сайта и БД
- [x] Классификация ошибок БД (транзиентные/данные/дубликаты), retry с backoff
- [x] Сброс CB на дубликатах, last_seen при достижении порога
- [ ] ⬜ Тест с имитацией сбоя БД (retry + открытие CB)

## 8. Фильтрация закупок
- [x] URL-фильтр (ADR-2): mos.ru — `filter` JSON, ЕИС — query-параметры
- [x] Обобщённый механизм `criteria_map` (json_path/query_param)
- [x] Резолв ОКПД2 (точный код → потомки → предок) + маппинг `okpd2_tree.json` (mos.ru)
- [x] Маппинг ОКПД2 → id ЕИС (62→8873935, 63→8873937), эндпоинт `children.html`
- [ ] ⬜ ОКПД2-фильтр ЕИС: `okpd2Ids` через URL/POST игнорируется сервером; нужен
      точный JS-механизм (снять реальный запрос из DevTools)
- [ ] 🟡 DOM-шаги фильтров для площадок с панелью (не используется для mos.ru/ЕИС)

## 9. Площадки
- [x] Портал поставщиков Москвы (zakupki.mos.ru): Реестр закупок, детали, файлы
- [x] ЕИС (zakupki.gov.ru): список, детали (blockInfo__section), URL-фильтр, «Обновлено»
- [ ] 🟡 ЕИС: детальные поля по типам извещений (ea20 проработан; ezt20/zk20/ok504 — уточнить)
- [ ] 🟡 ЕИС: файлы закупок (страница `documents.html`)
- [ ] ⬜ Коммерческие ЭТП (Роселторг, B2B-Center и др.)

## 10. API-сервис (FastAPI)
- [x] `GET /health`
- [x] `GET /api/procurements` (фильтры + пагинация), `GET /api/procurements/{id}`
- [x] `GET /api/procurements/{id}/technical-spec` (скачивание ТЗ)
- [x] `POST /api/procurements/{id}/score` (внешний сервис обновляет score)

## 11. Уведомления
- [x] Webhook-заглушка (лог)
- [ ] ⬜ Реальный HTTP POST (`config_service.yaml -> webhook`)

## 12. Тесты и CI
- [x] Unit: обработчики, конфиг, circuit breaker, last_seen, stop-условия, ОКПД2, скоринг
- [x] Integration: фикстуры (mos.ru, ЕИС), репозиторий, API (PostgreSQL)
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
- [x] `docs/adr.md` (ADR-1…4)
- [x] `docs/codes/okpd2_tree.json` (маппинг ОКПД2 mos.ru)

## 15. Code Review и оптимизация кода
- [x] Первичный code review и устранение замечаний (last_seen при пороге, сброс CB,
      path traversal, detail_json из финальной записи)
- [ ] 🟡 Регулярный code review после каждой крупной фичи (ЕИС, критерии поиска, скоринг)
- [ ] ⬜ Оптимизация цикла парсинга:
- [ ] ⬜ Оптимизация работы с БД: батчи, индексы под типовые запросы, пагинация.
- [ ] ⬜ Аудит асинхронных утечек и таймаутов (page.request, сессии, пул БД).
- [ ] ⬜ Устранение дублирования кода между площадками (общие хелперы селекторов/дат).

## Текущий фокус
- ЕИС: ОКПД2-фильтр (блокер, нужен реальный запрос из DevTools), детальные поля ea20
  (deadline/execution_term/security_amount/subject — обработчик `datetime` готов к внедрению),
  файлы (`documents.html`).
- Webhook — закрытие заглушек.
