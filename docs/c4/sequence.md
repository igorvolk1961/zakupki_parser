# Диаграмма последовательности — алгоритм парсинга

Последовательность действий парсера для одной площадки (Mermaid sequenceDiagram).
Соответствует `../../src/zakupki_parser/parser/orchestrator.py`.

```mermaid
sequenceDiagram
    autonumber
    participant S as Scheduler
    participant B as BrowserManager<br/>(Chromium)
    participant L as Lister
    participant F as FiltersEngine
    participant E as Extractor
    participant D as Detail
    participant R as ProcurementRepository
    participant SC as ExternalScorer
    participant N as Notifier

    S->>B: start() (stealth, задержки)
    B-->>S: context/page
    S->>L: open_list_page(url, cutoff)
    L-->>S: список загружен
    S->>L: setup_sort_and_filters()
    L->>F: apply_filters(steps)
    F-->>L: фильтры применены

    loop По страницам
        Note over S,L: Выход: конец пагинации<br/>или запись старее порога даты
        loop По контейнерам записей
            S->>L: next container
            L->>E: extract_from_scope(list.variables)
            E-->>S: list_vars
            S->>D: open_detail(detail_url)
            D-->>S: страница деталей
            S->>E: extract_from_scope(detail.variables)
            E-->>S: detail_vars
            S->>S: merge record + stop_conditions
            alt условие stop (deadline истёк)
                Note over S: заявка пропускается
            else файлы (метаданные)
                Note over S: files_json + technical_spec_name/url<br/>(глубокая обработка — внешний сервис, ADR-5)
            end
            S->>S: score (default / deadline_expired)
            S->>R: upsert(record) (контроль дубликатов)
            alt новая запись
                S->>SC: score(record) (асинхронно)
                SC-->>S: score
                S->>R: update score
                S->>N: notify(record) (после score)
            end
        end
        S->>L: goto_next_page()
    end

    S->>B: save_session() / close()
```

## Пояснения
- **Выход из цикла страниц** — при достижении конца пагинации или при встрече записи
  с датой публикации **старее** порога. Сравнение — по календарному дню; порог
  берётся из БД (`MAX(update_date)` по площадке), а при отсутствии записей — из
  `default_cutoff_days`.
- **stop_conditions** проверяются после извлечения деталей: если срок приёма заявок истёк
  (или до него меньше `min_deadline_days`) — заявка пропускается (не сохраняется,
  не уведомляется).
- **Файлы**: в основном режиме не скачиваются — сохраняются только метаданные
  (`files_json`, `technical_spec_name/url`). Глубокую обработку (PDF/DOCX/ZIP, поиск ТЗ)
  выполняет **внешний сервис** (ADR-5).
- **Скоринг**: перед записью ставится `default` (или `deadline_expired` для просроченных);
  микросервис скоринга вызывается асинхронно после сохранения, уведомление подписчиков
  происходит только после обновления score (ADR-3/ADR-6).
- **upsert** гарантирует отсутствие повторной записи заявки с тем же номером
  (unique-констрейнт + проверка перед вставкой).
