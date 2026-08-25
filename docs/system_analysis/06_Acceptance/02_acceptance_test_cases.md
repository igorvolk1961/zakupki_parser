# Сценарии приёмочного тестирования (тест-кейсы)

> Машинно-проверяемые сценарии по AC user stories (Given/When/Then) с привязкой к
> API-эндпоинтам (`src/zakupki_parser/api/app.py`). Формат пригоден для ручного прогона
> и (в перспективе) автотестов. Метод: API → функциональный.

## Группа A. Профили фильтрации (Эпик 1)

### TC-1.1 Создание профиля со словами (US-1.1)
- **Given**: авторизованный тендеролог.
- **When**: `POST /api/clients` с `{name, keywords, exclusion_words, okpd_codes, ...}`.
- **Then**: ответ `201/200` с `ProfileOut`; слова в таблице `keywords`; профиль появляется в `GET /api/clients`.

### TC-1.2 Выбор ЭТП и законов (US-1.2)
- **Given**: тендеролог открыл настройки профиля.
- **When**: `PUT /api/clients/{id}` с `target_etp`, `target_laws`.
- **Then**: профиль обновлён; поля сохранены в `profiles.target_etp/target_laws`.

### TC-1.3 Добавление слова-исключения (US-1.3)
- **Given**: профиль существует.
- **When**: `PUT /api/clients/{id}` с расширенным `exclusion_words` (например, «медицина»).
- **Then**: следующая закупка с этим словом отбрасывается клиентской фильтрацией (TC-2.4).

### TC-1.4 Активный профиль per-user (US-1.4)
- **Given**: у пользователя 2 профиля, активен A.
- **When**: `POST /api/clients/{B}/activate`.
- **Then**: активным становится B; A деактивирован; `GET /api/clients/active` → B.

### TC-1.5 Изоляция профилей (BR-07, US-7.8)
- **Given**: пользователи X и Y.
- **When**: X запрашивает `GET /api/clients/{id_профиля_Y}`.
- **Then**: `404` (профиль не найден в тенанте X); без утечки данных.

## Группа B. Парсинг и скоринг (Эпик 2)

### TC-2.1 Периодический сбор (US-2.1)
- **Given**: настроены площадки и ОКПД2.
- **When**: `POST /api/parser/start`.
- **Then**: статус `{"status":"started"}`; через цикл в БД появляются закупки; для новых закупок `scoring_queued_at` заполнен (авто-пуш Fit).

### TC-2.2 Auto-Fit скоринг (US-2.2)
- **Given**: закупка сохранена, задание fit в очереди.
- **When**: `scoring_service` возвращает результат через `POST /api/procurements/{id}/score`.
- **Then**: `fit_score` записан в `procurement_evaluations` под активный профиль; `score_method` обновлён.

### TC-2.3 Сортировка по Fit (US-2.3)
- **Given**: закупки с fit 0.3–0.95.
- **When**: `GET /api/procurements?sort=fit_score&scored=true`.
- **Then**: список отсортирован по убыванию `fit_score`.

### TC-2.4 Клиентская пост-фильтрация до записи (US-2.4, R9)
- **Given**: профиль с исключением «медицина»; фикстура списка закупок.
- **When**: парсер обрабатывает закупку с subject, содержащим «медицина».
- **Then**: закупка отбрасывается (`parser/filtering.py`), запись в БД не создаётся.

### TC-2.5 Дедуп и пропуск неизменного (BR-01)
- **Given**: закупка `(number, platform_id)` уже в БД с той же `update_date`.
- **When**: повторный проход.
- **Then**: дубликат не создаётся; повторная обработка пропущена.

### TC-2.6 Удаление нерелевантных (BR-01/BR-02)
- **Given**: закупки со `score_method=external` и `fit_score < 0.4`.
- **When**: `POST /api/db/clear-irrelevant` (парсер остановлен).
- **Then**: нерелевантные удалены, остальные сохранены.

## Группа C. Доставка и экспорт (Эпик 3)

### TC-3.1 Постадийное уведомление (US-3.1, BR-02)
- **Given**: настроен бэкенд уведомлений, `notify_min_fit_score` задан.
- **When**: стадия fit возвращает значение ≥ порога.
- **Then**: уведомление отправлено подписчикам; при значении < порога — не отправлено.

### TC-3.2 Карточка с базовыми полями (US-3.2)
- **Given**: закупка сохранена.
- **When**: `GET /api/procurements/{id}`.
- **Then**: поля: `number`, `customer`, `law`, `nmck`, `deadline`, `subject`, `fit_score`, `url`.

### TC-3.3 Экспорт CSV (US-3.3)
- **Given**: активные релевантные закупки.
- **When**: `POST /api/procurements/export`.
- **Then**: файл `export_dir/procurements.csv` (UTF-8 BOM) с колонками CSV_COLUMNS; ответ `{"status":"exported","count":N}`.

## Группа D. Анализ ТЗ (Эпик 4)

### TC-4.1 On-demand анализ (US-4.1)
- **Given**: закупка с ТЗ.
- **When**: `POST /api/procurements/analyze` `{procurement_ids:[id]}`.
- **Then**: `{"status":"queued"}`; после обработки `rag_report` в оценке; WebSocket «Анализ ТЗ завершен».

### TC-4.2 Системная проверка реестра Минпромторга «не установлено» (US-4.3, BR-04)
- **Given**: ТЗ с запретом иностранной продукции и пометкой «не установлено» в том же разделе.
- **When**: анализ ТЗ завершён.
- **Then**: в `rag_report` есть `sys:minprom_registry` с `verdict=no_stop_condition`, `marker=🟢`, `source=system`; цитата в `excerpt`.

### TC-4.3 Системная проверка лицензии с сопоставлением с профилем (US-4.4)
- **Given**: ТЗ требует лицензию МЧС; профиль без лицензий (`facts.license_codes=[]`).
- **When**: анализ ТЗ завершён.
- **Then**: `sys:license_sro` → `verdict=absolute`, `marker=🔴`. Если в профиле есть `mchs` → `no_stop_condition`/🟢. Нераспознанный вид лицензии → `soft`/🟡 «требует проверки».

### TC-4.4 Системная проверка опыта (US-4.2, BR-03)
- **Given**: ТЗ требует подтверждение опыта на площадке (ПП РФ 2571).
- **When**: анализ ТЗ завершён.
- **Then**: `sys:exp_2571` → `absolute`/🔴 при отсутствии `platform` в `facts.experience_codes`; `no_stop_condition`/🟢 при наличии; сканы актов/выписка из реестра → `soft`/🟡.

### TC-4.5 Обязательность системных проверок (FR-4.3/FR-4.4)
- **Given**: профиль без пользовательских вопросов (`questions=[]`).
- **When**: анализ ТЗ завершён.
- **Then**: в `rag_report` всегда присутствуют три системные проверки (`sys:exp_2571`, `sys:minprom_registry`, `sys:license_sro`) с `source=system`; их нельзя удалить через профиль — `PUT /api/clients/{id}` с `questions:[{id:"sys:exp_2571",...}]` не сохраняет `sys:*`.

### TC-4.6 Сбой LLM не роняет анализ (NFR-FT-2, FR-4.2)
- **Given**: LLM недоступен.
- **When**: анализ закупки.
- **Then**: задание не падает; вердикты с пометкой «не выполнено (сбой)» или `tz_found=false`; при отсутствии релевантных секций системные вердикты — `no_stop_condition` без вызова LLM.

### TC-4.7 Факты профиля для Stage B (FR-4.6)
- **Given**: у профиля есть лицензии/опыт.
- **When**: `GET /api/clients/active`.
- **Then**: в ответе `facts = {"license_codes": [...], "experience_codes": [...]}`.

## Группа E. Доступ и мультитенантность (Эпик 7)

### TC-5.1 Регистрация и логин (US-7.1)
- **Given**: нет аккаунта.
- **When**: `POST /api/auth/register` `{username, password, ...}`.
- **Then**: `TokenOut` с токеном; роль `tenderologist`; создан профиль по умолчанию.

### TC-5.2 Изоляция оценок (BR-07, US-7.9)
- **Given**: пользователи X и Y, одна закупка.
- **When**: X и Y запрашивают карточку.
- **Then**: каждый видит `fit_score` своего активного профиля; чужие оценки недоступны.

### TC-5.3 Внутренние вызовы (BR-07)
- **Given**: авторизация включена (`auth.enabled=true`), внешний сервис стадии.
- **When**: `POST /api/procurements/{id}/score` без корректного X-Internal-Token.
- **Then**: `401` («Неверный внутренний токен»); при незаданном `internal_token` — `503` (`require_internal`, fail-closed).

## Группа F. Администрирование

### TC-6.1 Запуск/остановка парсера (US-8.x)
- **Given**: администратор авторизован.
- **When**: `POST /api/parser/start`, затем `POST /api/parser/stop`.
- **Then**: статусы `started`/`stopping`; `GET /api/parser/status` отражает состояние.

### TC-6.2 Очистка БД (BR-01)
- **Given**: парсер остановлен.
- **When**: `POST /api/db/clear-inactive`.
- **Then**: неактивные закупки удалены; при работающем парсере — `409`.
