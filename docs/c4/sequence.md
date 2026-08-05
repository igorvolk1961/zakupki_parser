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
    participant DL as Downloader
    participant R as ProcurementRepository
    participant N as Notifier
    participant LS as LastSeenStore

    S->>B: start() (stealth, задержки)
    B-->>S: context/page
    S->>L: open_list_page(url)
    L-->>S: список загружен
    S->>L: setup_sort_and_filters()
    L->>F: apply_filters(steps)
    F-->>L: фильтры применены

    loop По страницам
        Note over S,L: Выход: конец пагинации<br/>или запись старее порога дат
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
            else файлы скачиваются
                S->>DL: download_files(number)
                DL-->>S: paths
                S->>FP: process(files)
                FP-->>S: extracted (заглушка)
                Note over S: delete_files_after_processing
            end
            S->>R: upsert(record) (контроль дубликатов)
            alt новая запись
                S->>N: notify(record)
            end
            S->>LS: save(last_processed)
        end
        S->>L: goto_next_page()
    end

    S->>B: save_session() / close()
```

## Пояснения
- **Выход из цикла страниц** — при достижении конца пагинации или при встрече записи
  с датой обновления **старее** порога `last_processed_date` (по умолчанию «сейчас − 1 неделя»).
- **stop_conditions** проверяются после извлечения деталей: если срок приёма заявок истёк —
  заявка пропускается (не сохраняется, не уведомляется).
- **upsert** гарантирует отсутствие повторной записи заявки с тем же номером
  (unique-констрейнт + проверка перед вставкой).
- Webhook и обработка файлов — сейчас заглушки (лог).
