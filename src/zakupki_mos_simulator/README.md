# Имитатор сайта zakupki.mos.ru

Независимый подпроект: веб-имитатор Портала поставщиков Москвы, воспроизводящий
**DOM-структуру, которую использует «Парсер закупок»** (не стиль и не наполнение).
Имитатор служит источником тестовой выборки закупок для демонстрации MVP парсера
и проверки точности сервиса скоринга.

Ключевая идея: HTML-страницы содержат те же CSS-классы (`styled-components sc-…`) и
контейнеры, что заданы в `demo_configs/config_dom.yaml` для площадки `zakupki_mos`.
Поэтому парсер парсит имитатор **без изменения кода** — меняется только `url` на
`http://localhost:8010` в демо-конфиге.

## Структура

```
src/zakupki_mos_simulator/
  cli.py                     # generate / serve / validate
  settings.py                # настройки (env ZAKUPKI_SIM_*)
  config/                    # загрузка селекторов из demo_configs/config_dom.yaml
  data/models.py             # pydantic-модели, 5 категорий выборки
  data/dataset.py            # load/save/балансировка датасета
  llm/                       # OpenAI-совместимый клиент + промпты + генерация + валидация
  web/                       # FastAPI-приложение и рендеры страниц
  demo_configs/              # 5 конфигов парсера, направленных на имитатор
  data/competencies.md       # текст компетенций поставщика (вход для генерации)
  tests/                     # unit-тесты
```

## Категории тестовой выборки

Выборка сбалансирована по 5 типам закупок относительно компетенций поставщика:

1. `perfect` — идеально подходят по семантике и набору терминов;
2. `synonym` — подходят по семантике, но термины синонимичны и не совпадают;
3. `close` — близко к компетенциям, но не полностью покрываются ими;
4. `far` — далеко от компетенций;
5. `false_friend` — далеко, но используют ряд терминов компетенций в другом смысле
   (например, «аппаратуры связи» vs «аппаратуры звукозаписи»).

Каждая закупка хранит ground-truth метку `category` — она нужна для валидации
точности сервиса скоринга.

## Запуск

Все команды запускаются из корня репозитория без правки `pyproject.toml`
(пакет не регистрируется как console-script, чтобы не конфликтовать с другими агентами).

### 1. Генерация тестовой выборки (LLM)

```bash
PYTHONPATH=src uv run python -m zakupki_mos_simulator generate \
  --competencies src/zakupki_mos_simulator/data/competencies.md \
  --per-category 8
```

Параметры LLM задаются env: `ZAKUPKI_SIM_LLM_BASE_URL`, `ZAKUPKI_SIM_LLM_API_KEY`,
`ZAKUPKI_SIM_LLM_MODEL`. Без доступа к LLM — детерминированный генератор:

```bash
PYTHONPATH=src uv run python -m zakupki_mos_simulator generate --no-llm --per-category 4
```

### 2. Запуск веб-имитатора

```bash
PYTHONPATH=src uv run python -m zakupki_mos_simulator serve --port 8010
```

### 3. Прогон парсера против имитатора

Сначала поднимите PostgreSQL (парсер-демо записывает закупки в БД):

```bash
./scripts/db_up.sh
```

Затем:

```bash
uv run zakupki-parser --configs src/zakupki_mos_simulator/demo_configs run-once
```

Демо-конфиг `config_score.yaml` вычисляет дефолтный score в парсере (ADR-7: внешний
скоринг идёт через конвейер transport + scoring_service). `config_dom.yaml` — на
`http://localhost:8010`; обрабатывается только площадка-имитатор `zakupki_mos`,
уведомления отключены.

**Изоляция БД.** Демо по умолчанию пишет в общую БД проекта `zakupki` (её поднимает
`./scripts/db_up.sh`), которую могут использовать другие агенты. Для повторных демо
задайте отдельную БД через env `ZAKUPKI_DB_DSN` (например, на базу `zakupki_demo`
с применённой схемой), чтобы не засорять общую.

### 4. Проверка точности сервиса скоринга

```bash
PYTHONPATH=src uv run python -m zakupki_mos_simulator validate \
  --dataset src/zakupki_mos_simulator/data/dataset.json \
  --scores /path/to/scores.csv \
  --threshold 0.5
```

Файл оценок: CSV с колонками `number,score` или JSON `{"number": score}`. Метрики:
accuracy, precision/recall/F1 по «высоко-привлекательной» группе
(perfect/synonym/close) и по категориям.

## Тесты

```bash
PYTHONPATH=src uv run pytest src/zakupki_mos_simulator/tests
```

## Замечания

- Имитатор слушает `127.0.0.1:8010` (не пересекается с API парсера на `:8000`).
- Имитатор **только** выдаёт HTML, понятный парсеру, и поставляет тестовую выборку
  с метками категорий. Скоринг — отдельный проект; никаких эндпоинтов скоринга
  в имитаторе нет.
- Все файлы — строго в этой папке; общие конфиги и код других агентов не меняются.
