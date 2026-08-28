# Диаграмма последовательности — алгоритм парсинга и скоринга

Последовательность действий парсера для одной площадки (Mermaid sequenceDiagram).
Соответствует `../../src/zakupki_parser/parser/orchestrator.py` и конвейеру скоринга
(ADR-7/ADR-9: `scoring_transport` + Redis + стадии `scoring_service`/`pwin_service`/
`margin_service`/`analysis_service`).

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
    participant SF as Scoring Service (Fit)
    participant PW as P(win) Service
    participant MM as Margin Service
    participant AN as Analysis Service
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
            S->>S: клиентская пост-фильтрация словами (R9) + stop_conditions
            alt условие stop (deadline истёк)
                Note over S: закупка пропускается
            end
            S->>R: upsert(record) (уровень списка, контроль дубликатов)
            S->>S: если компетенции профиля уже скорированы — копия результата группы (BR-07)
            alt новая запись (результата оценки группы нет)
                S->>TR: POST /api/scoring/jobs {id, priority, stage="fit", profile_id}
                TR->>RS: ZADD scoring:jobs (по приоритету)
            end
        end
        S->>L: goto_next_page()
    end

    Note over TR,AN: (асинхронное внешнее скоринг, вне цикла парсинга)
    SF->>RS: ZPOPMAX scoring:jobs
    SF->>A: GET /api/procurements/{id} + GET /api/clients/active (X-Profile-ID)
    A-->>SF: карточка + профиль (компетенции)
    SF->>SF: score (LLM: Fit → Judge → refine → ТЗ → Giga / векторная близость)
    SF->>RS: LPUSH scoring:results {id, fit_score, profile_id}
    TR->>RS: BRPOP scoring:results
    TR->>A: POST /api/procurements/{id}/score {fit_score, score_method="fit", profile_id}
    A->>R: update_score per-profile (score, fit_score; score_method=fit)

    Note over A,N: Уведомление — после стадии (порог по значению стадии)
    A->>A: результат стадии ≥ notify_min_fit_score?
    alt порог стадии пройден
        A->>N: notify(record)
    end

    Note over A,AN: P(win)/Margin/Analysis — on-demand (по запросу тендеролога)
    A->>TR: POST /api/scoring/jobs {id, priority, stage="pwin", profile_id}
    TR->>RS: ZADD pwin:jobs
    PW->>RS: ZPOPMAX pwin:jobs
    PW->>A: GET /api/procurements/{id}
    A-->>PW: карточка
    PW->>PW: P(win) = base × k_smp × k_license × …
    PW->>RS: LPUSH pwin:results {id, p_win, profile_id}
    TR->>RS: BRPOP pwin:results
    TR->>A: POST /api/procurements/{id}/score {p_win, score_method="pwin", profile_id}
    A->>R: update_score per-profile (score, p_win; score_method=pwin)

    A->>TR: POST /api/scoring/jobs {id, priority, stage="margin", profile_id}
    TR->>RS: ZADD margin:jobs
    MM->>RS: ZPOPMAX margin:jobs
    MM->>A: GET /api/procurements/{id}
    A-->>MM: карточка
    MM->>MM: Margin = НМЦК × margin_rate
    MM->>RS: LPUSH margin:results {id, margin, profile_id}
    TR->>RS: BRPOP margin:results
    TR->>A: POST /api/procurements/{id}/score {margin, score_method="margin", profile_id}
    A->>R: update_score per-profile (score, margin; score_method=margin)

    A->>TR: POST /api/scoring/jobs {id, priority, stage="analysis", profile_id}
    TR->>RS: ZADD analysis:jobs
    AN->>RS: ZPOPMAX analysis:jobs
    AN->>A: GET /api/procurements/{id} + GET /api/clients/active
    AN->>AN: RAG-анализ ТЗ (чанки → LLM-вердикт → матчер → маркеры)
    AN->>RS: LPUSH analysis:results {id, rag_report, profile_id}
    TR->>RS: BRPOP analysis:results
    TR->>A: POST /api/procurements/{id}/score {rag_report, profile_id}
    A->>R: update_rag_report per-profile

    S->>B: save_session() / close()
```

## Пояснения
- **Выход из цикла страниц** — при достижении конца пагинации или при встрече записи
  с датой публикации **старее** порога. Сравнение — по календарному дню; порог
  берётся из БД (`MAX(update_date)` по площадке), а при отсутствии записей — из
  `default_cutoff_days`.
- **stop_conditions** проверяются по данным уровня списка, ДО записи: если срок приёма заявок истёк
  (или до него меньше `min_deadline_days`) — закупка пропускается (не сохраняется,
  не уведомляется).
- **Детали площадки — досборка ПОСЛЕ скоринга (BR-08).** Закупка сохраняется на уровне
  списка (ОКПД2/файлы/ИНН НЕ запрашиваются), скоринг идёт по данным уровня списка
  (ADR-10 п.4). Обработчик `POST /score` ПЕРЕД записью результата в БД догружает
  платформенные детали по сохранённому контексту (`detail_api`) через единый
  интерфейс `extract_details` (браузерная страница) — одинаково для API- и
  DOM-площадок. Сбой деталей (например, HTTP 402 от API mos.ru) не роняет проход
  и не блокирует скоринг: карточка остаётся на уровне списка, результат всё равно
  записывается.
- **Файлы**: парсер не скачивает файлы — сохраняются только метаданные
  в `files_json` (включая ТЗ). Глубокую обработку (PDF/DOCX/ZIP, поиск ТЗ)
  выполняет **внешний сервис** (ADR-5).
- **Скоринг (ADR-7/ADR-9)**: закупка сохраняется **без оценки** (дефолтный скор удалён);
  для просроченных выставляется `deadline_expired`. Для новой записи парсер **автоматически**
  отправляет задание в транспорт (`POST /api/scoring/jobs` со `stage="fit"`, приоритет —
  время обновления/публикации, `profile_id` — пер-профильно, BR-07). Дальше скоринг
  работает **асинхронно**: `scoring_service` берёт задание из Redis (`ZPOPMAX`), получает
  карточку и профиль через API парсера (`GET /api/procurements/{id}` +
  `GET /api/clients/active`), считает Fit по LLM-пайплайну (Fit → Judge → refine →
  уточнение по ТЗ → ветка Giga-эмбеддингов) и публикует результат; транспорт возвращает
  его в API парсера (`POST /score`). **Автокаскад отключён**: P(win) (`pwin_service`) и
  Margin (`margin_service`) запускаются по явному запросу тендеролога
  (`POST /api/procurements/pwin-margin`, флаги `pwin_enabled`/`margin_enabled`);
  `analysis_service` — on-demand RAG-анализ ТЗ. Пороги `pwin_fit_threshold`/
  `margin_pwin_threshold` удалены (ADR-10).
- **Уведомление подписчиков** выполняется **в FastAPI-слое** — в обработчике
  `POST /api/procurements/{id}/score` **после каждой стадии** каскада (fit/pwin/margin),
  когда значение стадии прошло её порог (`notify_min_fit_score`/`notify_min_pwin`/
  `notify_min_margin` из `config_ops.yaml`; стадия выключается флагом `notify_*_enabled`).
  В цикле парсинга уведомлений нет.
- **upsert** гарантирует отсутствие повторной записи закупки с тем же номером
  (unique-констрейнт + проверка перед вставкой).
