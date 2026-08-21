# Этап 1. Мультитенантная модель данных (BR-07) — детальный план

> Источник: мастер-план (этап 1, MVP), трекер `plans/plan.md`.
> Скоуп MVP: пользователь **не корректирует оценки вручную** — `manual-score`/`reject` и
> Эпик 5 вне MVP (удаляются). Колонки жизненного цикла аккаунта (`status`/`trial_end_date`/
> `last_activity_at`), `audit_log`, `subscriptions` — пост-MVP (этап 6), здесь не трогаем.

## Цель

Перевести модель данных с глобального «клиентского профиля» (`config_score.yaml ->
active_client_id`) на мультитенантную: профили и оценки принадлежат пользователю
(`user_id`), репозиторий работает в tenant-скоупе (BR-07), конвейер скоринга работает
под сервис-аккаунтом. Приложение остаётся работоспособным на каждом шаге (заглушки),
тесты зелёные, устаревшие тесты заменяются/удаляются.

## Задачи (по порядку)

### 1.1. Миграция `db.changelog-1.29.yaml`
1. `users` + `email` (nullable; без lifecycle-колонок — пост-MVP).
2. `renameTable` `client_profiles` → `profiles`.
3. `profiles` + `user_id` FK→`users.id` (nullable — app-гарантия заполнения),
   + `is_active bool default false`, + `min_fit_threshold float` (nullable),
   + `target_etp jsonb default '[]'`, + `target_laws jsonb default '[]'`.
4. `profiles`: drop `uq_client_profiles_name`, add `uq_profiles_user_name (user_id, name)`.
5. `renameTable` `procurement_scores` → `procurement_evaluations`;
   drop `uq_procurement_scores_proc_client`, add `uq_evaluations_proc_user (procurement_id, user_id)`;
   + `user_id` FK→`users.id` (nullable — app-гарантия);
   + `status varchar(32) default 'new'`, + `rejection_reason text` (nullable).
6. Новые таблицы: `keywords` (id, profile_id FK→profiles CASCADE, word, type
   `keyword|exclusion`, timestamps), `procedure_categories` (id, name UNIQUE,
   pwin_coefficient, timestamps — заглушка).
7. `procurements` + `category_id` FK→`procedure_categories` (nullable, заглушка).

> `user_id` в `profiles`/`procurement_evaluations` в БД nullable: приложение на старте
> вызывает `ensure_service_account()` (создаёт сервис-аккаунт при отсутствии пользователей
> и присваивает осиротевшие строки) — идемпотентно. Все операции репозитория всегда
> передают явный `user_id` (BR-07).

### 1.2. ORM (`storage/db.py`)
- `User` + `email`.
- `ClientProfile` → **`Profile`** (таблица `profiles`): + `user_id`, + `is_active`,
  + `min_fit_threshold`, + `target_etp`, + `target_laws`; UNIQUE `(user_id, name)`.
- `ProcurementScore` → **`ProcurementEvaluation`** (таблица `procurement_evaluations`):
  `client_id` → `user_id`, + `status`, + `rejection_reason`; UNIQUE `(procurement_id, user_id)`.
- Новые: `Keyword`, `ProcedureCategory`; `Procurement` + `category_id`.
- Обновить relationships.

### 1.3. Репозиторий (`storage/repository.py`)
- `get_active_profile(user_id)` (замена `get_active_client`): активный профиль пользователя
  (`is_active=true`), fallback — профиль `default`, fallback — первый enabled; иначе None.
- `ensure_service_account()` → `first_user()` + backfill: осиротевшие профили/оценки
  (`user_id IS NULL`) присваиваются сервис-аккаунту; при отсутствии пользователей —
  создание админа (логика на стороне API, репозиторий отдаёт `first_user`/`create_user`).
- `upsert_profile`/`get_profile(user_id, id)`/`list_profiles(user_id)`/`get_profile_by_name(user_id, name)`
  — все в скоупе `user_id` (заменяют `upsert_client`/`get_client`/`list_clients`/`get_client_by_name`).
- `set_active_profile(user_id, profile_id)` — выбор активного профиля.
- `upsert_score`/`get_score`/`update_rag_report`/`_apply_client_score`/`list_procurements(client_id=)`
  → ключ по `user_id`.
- `delete_irrelevant(client_id=)` → `user_id=`.
- Осиротевшие строки: репозиторий не создаёт профили/оценки без `user_id`.

### 1.4. API (`api/app.py`)
- `_effective_user(user)` → при auth off возвращает сервис-аккаунт (admin/first user);
  `_ensure_service_account()` — создание админа (env-сид/fallback) при пустых пользователях
  и backfill осиротевших строк.
- `_active_profile(user)` — профиль эффективного пользователя (замена `_active_client`).
- Эндпоинты списка/детали/score/analyze/pwin-margin/export/clear-irrelevant —
  переходят на `_active_profile(эффективный пользователь)`.
- `/api/clients/*` — скоуп на текущего пользователя (эффективного); `active` —
  пользователь или внутренний токен (сервис-аккаунт).
- **Удалить** `POST /api/procurements/{id}/manual-score` и `/reject`.
- `config_score.yaml -> active_client_id` объявлен deprecated (не используется).

### 1.5. Парсер (`parser/orchestrator/orchestrator.py`)
- Вместо `get_active_client(cfg.score.active_client_id)` — активный профиль сервис-аккаунта
  (`ensure_service_account()` + `get_active_profile`). Поведение парсера не меняется.

### 1.6. Web-демо (`api/zakupki.html`)
- Убрать select «Ручная» и кнопку «Откл» (+ JS-обработчики `manual-score`/`reject`).
- Пилюля активного клиента работает как раньше (`/api/clients/active`).

### 1.7. Конвейер скоринга
- HTTP-сервисы не меняются: `/api/clients/active` (X-Internal-Token) возвращает активный
  профиль сервис-аккаунта; `/score` пишет оценки под сервис-аккаунт.

### 1.8. Тесты
- Обновить фикстуры интеграционных тестов: сид пользователя (сервис-аккаунт) → профиль
  `default` под ним (вместо `upsert_client` без user).
- `test_multiclient.py`: заменить `manual_score_and_reject` (удалённые эндпоинты) на проверку
  изоляции BR-07 (пользователь A не видит профили/оценки B) и активного профиля; `analyze`-тест
  привести к актуальному поведению (транспорт задан → «queued», а не 409).
- `test_cascade.py`: убрать кейс `manual/reject` из уведомлений.
- Новые тесты: `get_active_profile` fallback, `ensure_service_account` backfill,
  изоляция (403/пустые списки), скоуп CRUD профилей по пользователю.
- Прогон: `uv run pytest tests/unit -q` (без регрессий), integration при DSN, `ruff`,
  `mypy`, `zp check-config`.

## Заглушки
- `procedure_categories` — пустая таблица (P(win)-коэффициент не используется).
- `target_etp`/`target_laws`/`min_fit_threshold` — сохраняются, влияют на парсинг с этапа 3.

## Критерии приёмки
1. Миграция 1.29 применима на чистой БД и на существующих данных (rename+backfill).
2. Профили/оценки создаются и читаются только в скоупе `user_id` (BR-07).
3. `manual-score`/`reject` удалены; web-демо их не вызывает.
4. Конвейер скоринга (score/active) работает под сервис-аккаунтом без изменений в воркерах.
5. unit 277+ без регрессий, integration зелёные (при DSN), ruff/mypy/check-config — чисто.
