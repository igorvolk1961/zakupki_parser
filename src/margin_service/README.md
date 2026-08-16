# margin_service

Стадия каскада скоринга `Fit -> P(win) -> Margin`: расчёт маржи

    Margin = НМЦК × margin_rate

Потребляет задачи из Redis-очереди `margin:jobs`, получает карточку закупки через
REST API парсера, считает маржу и публикует результат в `margin:results`
(транспорт возвращает его в парсер через `POST /score`).

## Запуск

    uv run python -m margin_service worker          # воркер Redis-очереди
    uv run python -m margin_service score card.json # разовый расчёт по карточке

Настройки — `config.yaml` + env `MARGIN_*` (аналогично `scoring_service`).
Общий код (`scoring_common`) подключается через `PYTHONPATH`.
