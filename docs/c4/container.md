# C4 — Container (Уровень 2)

Диаграмма контейнеров системы парсера.

```mermaid
C4Container
  title C4 — Контейнеры системы парсинга закупок

  Person(user, "Оператор", "Настраивает через YAML")

  System_Boundary(parser_sys, "Парсер закупок") {
    Container(cli, "CLI / Scheduler", "Python, asyncio", "Запуск по таймеру, цикл по сайтам")
    Container(engine, "Парсер-движок", "Playwright (Chromium)", "Сортировка, фильтры, пагинация, извлечение")
    Container(antibot, "Антиблок-слой", "Stealth, задержки, лимиты", "Снижение риска блокировки IP")
    Container(cb, "Circuit Breaker", "Python", "Вежливая деградация")
    Container(store, "Слой хранения", "SQLAlchemy async", "Запись закупок, контроль дубликатов")
    Container(proc, "File processor", "Python", "Обработка скачанных файлов (заглушка)")
  }

  System_Ext(site, "Платформы закупок", "HTML через браузер")
  ContainerDb(db, "PostgreSQL", "Хранилище закупок")
  System_Ext(webhook, "Webhook", "Оповещения")

  Rel(user, cli, "команды / конфиг")
  Rel(cli, engine, "запуск прохода")
  Rel(engine, antibot, "браузерные действия")
  Rel(engine, site, "HTTP/Playwright")
  Rel(engine, store, "upsert закупок")
  Rel(engine, proc, "файлы заявок")
  Rel(store, db, "SQL")
  Rel(engine, webhook, "уведомления")
  Rel(cb, engine, "отказы/разрешения")
  Rel(cb, store, "отказы/разрешения")
```

## Замечания
- `store` пишет в PostgreSQL, используя SQLAlchemy 2.x (async) и контроль дубликатов
  по `number + source_platform`.
- `antibot` включает stealth-скрипты, задержки, лимиты и персистентную сессию.
