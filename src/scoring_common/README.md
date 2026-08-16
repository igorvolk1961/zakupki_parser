# scoring_common

Общие компоненты каскада скоринга `Fit -> P(win) -> Margin`:

- `pwin.py` — формула `P(win) = base_pwin × k_smp × k_license × k_large × k_procedure × k_ai`
  (чистая функция, коэффициенты из конфига);
- `margin.py` — маржа `НМЦК × margin_rate`;
- `config.py` — модель коэффициентов P(win) и YAML-источник настроек;
- `queue.py` — параметризованная Redis-очередь задач/результатов стадии;
- `parser_api.py` — клиент REST API парсера (карточка + возврат результата);
- `schemas.py` — результат стадии каскада.

Используется сервисами `pwin_service` и `margin_service`.
