# C4 — Context (Уровень 1)

Контекстная диаграмма системы парсера площадок закупок.

```mermaid
C4Context
  title C4 — Контекст системы парсинга закупок

  Person(user, "Оператор/аналитик", "Настраивает парсер, просматривает результаты")
  Person(subscriber, "Подписчик", "Получает оповещения о новых закупках")

  System(parser, "Парсер закупок (zakupki-parser)", "Собирает закупки с платформ через Playwright, сохраняет в БД, оповещает")

  System_Ext(site, "Платформы закупок", "zakupki.mos.ru, ЕИС, ЭТП")
  System_Ext(webhook, "Webhook-приёмник", "Оповещения о новых закупках")

  Rel(user, parser, "Задаёт конфигурацию (YAML)")
  Rel(site, parser, "Отдаёт HTML-страницы закупок")
  Rel(parser, webhook, "POST JSON-уведомления")
  Rel(webhook, subscriber, "Доставляет уведомления")

  UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

## Легенда
- **Окружение**: оператор и подписчики — внешние роли.
- **Внешние системы**: платформы закупок (источник данных) и webhook-приёмник.
- **Внутренняя система**: `zakupki-parser`.
