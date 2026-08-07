# Диаграмма последовательности — алгоритм парсинга и скоринга

Последовательность действий парсера для одной площадки (Mermaid sequenceDiagram).
Соответствует `../../src/zakupki_parser/parser/orchestrator.py` и конвейеру скоринга
(ADR-7: `scoring_transport` + `scoring_service` + Redis).

```mermaid
sequenceDiagram
    autonumber
    participant S as Scheduler
    participant A as Parser API (FastAPI)
    participant B as BrowserManager<br/>(Chromium)
    participant L as Lister
    participant F as FiltersEngine
    participant E as Extractor
    participant D as Detail
    participant R as ProcurementRepository
    participant TR as Scoring Transport
    participant RS as Redis
    participant SG as Scoring Service
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
            alt новая запись (score_method=default)
                S->>TR: POST /api/scoring/jobs {id, default_score}
                TR->>RS: ZADD scoring:jobs (по приоритету)
            end
        end
        S->>L: goto_next_page()
    end

    Note over TR,SG: (асинхронный конвейер скоринга, вне цикла парсинга)
    SG->>RS: ZPOPMAX scoring:jobs
    SG->>A: GET /api/procurements/{id}
    A-->>SG: карточка (вкл. detail_json)
    SG->>SG: score (заглушка возвращает score из карточки)
    SG->>RS: LPUSH scoring:results {id, score}
    TR->>RS: BRPOP scoring:results
    TR->>A: POST /api/procurements/{id}/score
    A->>R: update_score (score_method=external)
    A->>A: score ≥ notify_min_score?
    alt score ≥ notify_min_score
        A->>N: notify(record)
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
- **Скоринг (ADR-7)**: при сохранении ставится `default` (или `deadline_expired` для
  просроченных). Для новой записи парсер **автоматически** отправляет задание в транспорт
  (`POST /api/scoring/jobs` с приоритетом = дефолтным score). Дальше конвейер работает
  **асинхронно**: `scoring_service` берёт задание из Redis (`ZPOPMAX`), получает карточку
  через API парсера (`GET /api/procurements/{id}`), считает score и публикует результат;
  транспорт возвращает его в API парсера (`POST /score`). Пока `score_use_stub` включён,
  `scoring_service` возвращает score из карточки без LLM-пайплайна.
- **Уведомление подписчиков** выполняется **в FastAPI-слое** — в обработчике
  `POST /api/procurements/{id}/score` после обновления финального score, только если
  `score ≥ notify_min_score` (порог из конфига). В цикле парсинга уведомлений нет.
- **upsert** гарантирует отсутствие повторной записи заявки с тем же номером
  (unique-констрейнт + проверка перед вставкой).
