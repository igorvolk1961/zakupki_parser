# pwin_service

Стадия каскада скоринга `Fit -> P(win) -> Margin`: расчёт вероятности победы

    P(win) = base_pwin × k_smp × k_license × k_large × k_procedure × k_ai

Потребляет задачи из Redis-очереди `pwin:jobs`, получает карточку закупки через
REST API парсера, считает `P(win)` и публикует результат в `pwin:results`
(транспорт возвращает его в парсер через `POST /score`).

## Запуск

    uv run python -m pwin_service worker          # воркер Redis-очереди
    uv run python -m pwin_service score card.json # разовый расчёт по карточке

Настройки — `config.yaml` + env `PWIN_*` (аналогично `scoring_service`).
Общий код (`scoring_common`) подключается через `PYTHONPATH`.
