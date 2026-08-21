# Этап 2. Профили фильтрации (Эпик 1) — детальный план

> Источник: мастер-план (этап 2, MVP), трекер `plans/plan.md`.
> Зависит от Этапа 1 (модель данных: `profiles`, `keywords`, tenant-скоуп).

## Цель

Per-user профили фильтрации (CRUD уже в tenant-скоупе с Этапа 1) дополнить:
выбор активного профиля, таблица `keywords` (нормализованное представление) с парсером
`data/profile.md` (R8) и сидом для профиля по умолчанию (сервис-аккаунт + каждый новый
пользователь). Слова хранятся в таблице ``keywords`` (канонический источник, ER:
PROFILE->KEYWORD; миграция 1.30 переносит данные из JSONB и удаляет колонки профиля).
Поведение парсера до Этапа 3 не меняется
(использует активный профиль сервис-аккаунта).

## Задачи (по порядку)

### 2.1. Парсер `data/profile.md` (`storage/keywords_parser.py`)
- Разбор секций «Ключевые слова» и «Минус слова» (заголовки `**...**`).
- Нормализация выражений: обрезка пробелов, снятие кавычек, сохранение синтаксиса
  `слов*`, `(фраза* фраза*)~N`, точных фраз.
- Результат: `{"keywords": [...], "exclusion_words": [...]}`.
- Путь к файлу: `data/profile.md` (относительно корня репозитория; env-оверрайд
  `ZAKUPKI_KEYWORDS_FILE`).

### 2.2. Репозиторий (`storage/repository.py`)
- `sync_profile_keywords(profile_id)`: переписывает строки `keywords` из
  `Profile.keywords` (type=keyword) и `Profile.exclusion_words` (type=exclusion);
  вызывается в `upsert_profile` после сохранения.
- `seed_default_profile(user_id, seed)` — создаёт/обновляет профиль `default`
  (активный, enabled) с `keywords`/`exclusion_words` из сида и синхронизирует `keywords`.
- `set_active_profile` — уже есть; `upsert_profile` при `is_active=true` сбрасывает
  остальные активные профили пользователя (гарантия единственного активного).

### 2.3. API (`api/app.py`)
- Регистрация и `_ensure_service_account()`: сид default-профиля с ключевыми словами
  из `data/profile.md` (R8) вместо пустого профиля.
- Эндпоинт выбора активного профиля: `PUT /api/clients/{id}` с `is_active=true` (или
  отдельный `POST /api/clients/{id}/activate`) — через `set_active_profile`.
- Профили CRUD уже per-user (Этап 1): расширить `ProfileOut` не нужно (поля уже есть).

### 2.4. Тесты
- Unit: парсер `profile.md` (секции, формы `слов*`, `(…)~N`, кавычки, минус-слова).
- Unit/integration: `sync_profile_keywords` (запись/перезапись, типы keyword/exclusion),
  сид default-профиля, единственный активный профиль, CRUD в tenant-скоупе.
- Прогон: `uv run pytest tests/unit -q`, integration при DSN, `ruff`, `mypy`,
  `zp check-config`.

## Заглушки
- `target_etp`/`target_laws`/`min_fit_threshold` сохраняются, влияют на парсинг с Этапа 3.
- Парсер использует активный профиль сервис-аккаунта (поведение как в Этапе 1).

## Критерии приёмки
1. `data/profile.md` разбирается в keywords/exclusion_words (секции, формы).
2. У каждого пользователя при регистрации есть активный default-профиль с сидом.
3. `keywords` синхронизируются с JSONB профиля; активный профиль — единственный.
4. unit 277+ без регрессий, ruff/mypy/check-config чисто.
