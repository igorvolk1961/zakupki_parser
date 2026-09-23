"use strict";

// Вкладка «Закупки»: таблица, фильтры, пагинация, выбор, карточка/модалка
// деталей, RAG-отчёт, постановка анализа/P(win)/Margin, селектор площадок.
import {
  $,
  escapeHtml,
  fitCell,
  fmtDate,
  fmtDateOnly,
  fmtMoney,
  fmtDT,
} from "./utils.js";
import { state } from "./store.js";
import { api, apiJSON, apiErrorDetail, authHeaders } from "./api.js";
import { hasRole } from "./roles.js";
import { renderMarkdown } from "./markdown.js";

let allItems = [];
// Пакетный выбор (чекбоксы/Ctrl+клик) — ТОЛЬКО для массовых операций (анализ/
// P(win)/Margin), не связан с тем, какая карточка сейчас показана в панели
// справа (см. panelDetailId) — отменено требование одновременного показа
// нескольких карточек (было раньше), решение пользователя.
let selected = new Set();
const PROC_PAGE_SIZE = 100;
let procPage = 1;
let procTotal = 0;
// id ЕДИНСТВЕННОЙ закупки, чья полная карточка показана в панели #proc-detail
// (клик по строке таблицы) — и её активная вкладка (сохраняется между
// перерисовками панели, как и у модалки, см. openDetailTab).
let panelDetailId = null;
let panelDetailTab = "data";

// Эффективная доступность опции scoring у пользователя (BR-09). Без неё
// фильтр «закупки без прошедшего скоринга» отключается: закупки скоринг не
// проходят вовсе, поэтому таблица показывает все собранные закупки.
let scoringEnabled = true;
let scoringGatePromise = null;

async function loadScoringGate() {
  try {
    const cab = await api("account/cabinet");
    const option = (cab.catalog || []).find((o) => o.key === "scoring");
    scoringEnabled = !option || option.enabled === true;
  } catch (err) {
    scoringEnabled = true;
  }
  return scoringEnabled;
}

function scoringGate() {
  if (!scoringGatePromise) scoringGatePromise = loadScoringGate();
  return scoringGatePromise;
}

// Состояние анализа документов: id закупок, для которых сейчас выполняется анализ.
// Кнопка «Анализ документов» блокируется до получения результата (см. pollProc).
const analyzingIds = new Set();
// id открытой карточки (модалки) и сигнатура её rag_report для автообновления.
let openDetailId = null;
let lastDetailSig = "";
// То же для карточки панели (#proc-detail) — см. refreshPanelDetail.
let lastPanelDetailSig = "";
// Активная вкладка карточки закупки («Данные закупки»/«Результаты скоринга и
// анализа»/«Метрики»). Сохраняется между перерисовками (pollProc автообновляет
// модалку по изменению rag_report, не сбрасывая выбранную вкладку).
let openDetailTab = "data";

function updateMinFit() {
  $("#min-fit-wrap").style.display = $("#proc-relevant").checked ? "inline" : "none";
}

function procRow(row) {
  const methodLabel = row.score_method
    ? { manual: "ручная", reject: "отклонена" }[row.score_method] || row.score_method
    : "";
  const rg = row.rag_report ? ` data-rag="${escapeHtml(JSON.stringify(row.rag_report))}"` : "";
  return `<tr data-id="${row.id}" class="${panelDetailId === row.id ? "sel" : ""}"${rg}>
    <td><input type="checkbox" class="row-sel" data-id="${row.id}" ${selected.has(row.id) ? "checked" : ""}></td>
    <td class="id">${row.id}</td>
    <td><div class="num">${escapeHtml(row.number)}</div><div class="subj">${escapeHtml(row.subject || "—")}${row.in_work ? ' <span class="pill active">в работе</span>' : ""}</div></td>
    <td><span class="pill">${escapeHtml(row.platform_id)}</span></td>
    <td><span class="pill score">${fitCell(row)}</span>${methodLabel ? `<span class="muted" style="font-size:11px"> ${escapeHtml(methodLabel)}</span>` : ""}</td>
    <td><span class="pill ${row.is_active ? "active" : "inactive"}">${row.is_active ? "Активна" : "Не активна"}</span></td>
    <td>${fmtDateOnly(row.publication_date)}</td>
  </tr>`;
}

// Сортировка выполняется на бэкенде (параметр sort API) — клиент отображает
// страницу как пришла, постранично (offset = страница × размер страницы).
function displayItems() {
  return allItems;
}

// Панель справа от таблицы показывает ПОЛНУЮ карточку (те же вкладки/данные,
// что и модалка) ровно ОДНОЙ выбранной закупки — отменено прежнее требование
// показывать несколько карточек одновременно. Строка карточки уже есть в
// allItems (список отдаёт те же поля, что и карточка, см. ProcurementOut) —
// отдельный запрос за деталями не нужен, в отличие от модалки (openDetail),
// которая делает собственный запрос карточки (напр. toggleCardFullscreen —
// переоткрытие текущей закупки шторкой в полноэкранном режиме).
function renderDetail() {
  const panel = $("#proc-detail");
  if (panelDetailId === null) {
    panel.innerHTML = `<div class="empty">Выберите закупку слева</div>`;
    return;
  }
  const row = allItems.find((r) => r.id === panelDetailId);
  if (!row) {
    panel.innerHTML = `<div class="empty">Закупка больше не в текущей выдаче (изменились фильтры или страница)</div>`;
    return;
  }
  lastPanelDetailSig = detailSig(row);
  panel.innerHTML = cardBodyHtml(row, { closable: false, containerId: "proc-detail" });
  setCardTab(panelDetailTab, "proc-detail");
}

function renderProc() {
  const items = displayItems();
  const tbody = $("#proc-rows"), empty = $("#proc-empty");
  if (!items.length) {
    tbody.innerHTML = "";
    empty.style.display = "block";
    empty.textContent = state.parserRunning
      ? "Идёт сбор закупок…"
      : "Закупок нет — запустите парсер, чтобы наполнить БД.";
  } else {
    empty.style.display = "none";
    tbody.innerHTML = items.map(procRow).join("");
  }
  renderDetail();
}

let lastProcSig = "";
function sigOfProc(data) {
  return data.total + ":" + data.items.map((r) => r.id + "@" + (r.update_date || "")).join(",");
}

function clampPage() {
  const pages = Math.max(1, Math.ceil(procTotal / PROC_PAGE_SIZE));
  if (procPage > pages) procPage = pages;
}

function renderPager() {
  const pages = Math.max(1, Math.ceil(procTotal / PROC_PAGE_SIZE));
  const pager = $("#proc-pager");
  pager.style.display = procTotal ? "flex" : "none";
  $("#proc-page-info").textContent = `Страница ${procPage} из ${pages} · всего ${procTotal}`;
  $("#proc-prev").disabled = procPage <= 1;
  $("#proc-next").disabled = procPage >= pages;
}

function goProcPage(page) {
  page = Math.min(Math.max(1, page), Math.max(1, Math.ceil(procTotal / PROC_PAGE_SIZE)));
  if (page === procPage) return;
  procPage = page;
  loadProc();
}

async function procParams() {
  const params = { limit: PROC_PAGE_SIZE, offset: (procPage - 1) * PROC_PAGE_SIZE };
  if ($("#proc-sort").value) params.sort = $("#proc-sort").value;
  if ($("#proc-platform").value) params.platform_id = $("#proc-platform").value;
  if ($("#proc-active").value !== "") params.active = $("#proc-active").value === "1";
  if ($("#proc-in-work").checked) params.in_work = true;
  // «Только релевантные» — по желанию пользователя, НЕ автоматически: раньше при
  // доступном скоринге закупки без fit-score (ещё не обработанные конвейером)
  // скрывались всегда, независимо от чекбокса — пользователь не мог их увидеть,
  // даже сняв «Только релевантные». Теперь оба фильтра (порог fit-score И
  // «скрыть неоценённые») управляются ОДНИМ чекбоксом: снят — видно всё,
  // включая ещё не оценённые закупки.
  if ((await scoringGate()) && $("#proc-relevant").checked) {
    params.min_fit_score = $("#proc-min-fit").value;
    params.scored = true;
  }
  return params;
}

async function loadProc() {
  clampPage();
  const data = await api("procurements", await procParams());
  procTotal = data.total;
  const pages = Math.max(1, Math.ceil(procTotal / PROC_PAGE_SIZE));
  if (procPage > pages) {
    procPage = pages;
    return loadProc();
  }
  allItems = data.items;
  lastProcSig = sigOfProc(data);
  $("#cnt-proc").textContent = data.total;
  renderProc();
  renderPager();
}

// Авто-обновление: перерисовываем список только если данные в БД изменились.
async function pollProc() {
  try {
    clampPage();
    const data = await api("procurements", await procParams());
    const pages = Math.max(1, Math.ceil(data.total / PROC_PAGE_SIZE));
    if (procPage > pages) {
      procPage = pages;
      return loadProc();
    }
    const sig = sigOfProc(data);
    if (sig !== lastProcSig) {
      lastProcSig = sig;
      allItems = data.items;
      procTotal = data.total;
      $("#cnt-proc").textContent = data.total;
      renderProc();
      renderPager();
    }
    // Открытая карточка (модалка и/или панель) могла получить результат анализа
    // ТЗ (rag_report), даже если сигнатура списка не изменилась — обновляем
    // независимо от списка.
    await refreshOpenDetail();
    await refreshPanelDetail();
  } catch (err) {
    /* временные сбои игнорируем — попробуем на следующем тике */
  }
}

async function openDetail(id) {
  const row = await api("procurements/" + id);
  // При открытии новой карточки показываем «Данные закупки»; при возврате из
  // просмотра ТЗ (тот же id) сохраняем выбранную вкладку.
  if (id !== openDetailId) openDetailTab = "data";
  openDetailId = id;
  lastDetailSig = detailSig(row);
  renderModal(row);
}

// Сигнатура RAG-отчёта карточки — сравнение для автообновления модалки.
function detailSig(row) {
  return JSON.stringify(row.rag_report || null);
}

function renderModal(row) {
  $("#modal").innerHTML = cardBodyHtml(row, { closable: true, containerId: "modal" });
  setCardTab(openDetailTab, "modal");
  $("#modal-bg").classList.add("open");
}

// Компактная сводка по оценке (score/fit/sim + трейс) — раньше показывалась
// только на сокращённой карточке в панели; после отмены требования «несколько
// карточек одновременно» сокращённая карточка убрана, но эти данные всё
// равно нужны «на виду», не только на вкладке «Результаты скоринга и анализа»
// (которая требует отдельного клика) — показываем сразу под заголовком,
// независимо от активной вкладки.
function cardScoreSummaryHtml(row, analyst) {
  return `<div class="toolbar" style="margin:0 0 10px; gap:6px;">
    <span class="pill score">score ${row.score ?? "—"}</span>
    <span class="pill score">fit ${fitCell(row)}</span>
    <span class="pill score">sim ${row.embedding_similarity ?? "—"}</span>
    ${row.langfuse_trace_url && analyst ? `<a class="pill" href="${escapeHtml(row.langfuse_trace_url)}" target="_blank" rel="noopener">трейс</a>` : ""}
  </div>`;
}

// Полное содержимое карточки закупки (заголовок + сводка оценки + вкладки +
// панели + тулбар действий) — общее для модалки (``renderModal``, доступна с
// других вкладок, напр. «В работе») и панели справа от таблицы «Закупки»
// (``renderDetail``, единственная показываемая карточка). ``containerId`` —
// на чём именно рендерится карточка ("modal"/"proc-detail"): используется для
// скоупинга id (``${containerId}-excl-btn``) и переключения вкладок
// (``setCardTab``), чтобы модалка и панель, если обе одновременно видны
// (напр. модалка ТЗ поверх панели), не конфликтовали по дублирующимся id.
function cardBodyHtml(row, { closable, containerId }) {
  const f = (label, v) => `<tr><td>${label}</td><td>${v}</td></tr>`;
  const files =
    (row.files_json || [])
      .map(
        (x) =>
          `<div><a href="${escapeHtml(x.url)}" target="_blank" rel="noopener">${escapeHtml(x.name)}</a></div>`
      )
      .join("") || "–";
  const isAnalyzing = analyzingIds.has(row.id);
  const analyst = hasRole("analyst");
  // Вкладки карточки: «Данные закупки» — всегда; «Результаты скоринга и анализа» —
  // всегда; «Метрики» — только роли analyst (внутренняя метрика, costs отдаётся
  // только ей — см. converters).
  const tabs = [
    `<button type="button" class="active" data-cardtab="data" onclick="setCardTab('data', '${containerId}')">Данные закупки</button>`,
    `<button type="button" data-cardtab="scoring" onclick="setCardTab('scoring', '${containerId}')">Результаты скоринга и анализа</button>`,
  ];
  if (analyst) {
    tabs.push(
      `<button type="button" data-cardtab="metrics" onclick="setCardTab('metrics', '${containerId}')">Метрики</button>`
    );
  }
  const panels = [
    `<div class="card-tab-panel active" data-cardpanel="data">${cardDataPanel(row, f, files)}</div>`,
    `<div class="card-tab-panel" data-cardpanel="scoring" style="display:none">${cardScoringPanel(row, f, isAnalyzing, containerId)}</div>`,
  ];
  if (analyst) {
    panels.push(`<div class="card-tab-panel" data-cardpanel="metrics" style="display:none">${cardMetricsPanel(row)}</div>`);
  }
  // Тулбар действий — ЗАФИКСИРОВАН в подвале карточки (не скроллится вместе с
  // содержимым): контейнер (.proc-detail/.modal) — flex-колонка, .card-scroll
  // растягивается и скроллит контент сам, .card-footer — фиксированный
  // последний элемент этой колонки. Кнопка закрытия (×) — тоже вне скролла
  // (position:absolute поверх карточки, см. CSS), иначе уезжала бы при скролле.
  return `
    ${closable ? `<span class="close" onclick="closeModal()">×</span>` : ""}
    <div class="card-scroll">
      <div class="toolbar card-icon-actions">
        <button class="ghost icon-btn" title="На весь экран" onclick="toggleCardFullscreen('${containerId}', ${row.id})">⛶</button>
        <button class="ghost icon-btn" title="Выгрузить в Excel" onclick="exportCardXlsx(${row.id}, '${containerId}')">📊</button>
        <button class="ghost icon-btn" title="Печать" onclick="printCard('${containerId}')">🖨</button>
      </div>
      <h2>${escapeHtml(row.number)}</h2>
      ${cardScoreSummaryHtml(row, analyst)}
      <div class="tabs card-tabs">${tabs.join("")}</div>
      ${panels.join("")}
    </div>
    <div id="${containerId}-op-status" class="muted" style="min-height:18px; margin-top:8px;"></div>
    <div class="toolbar card-footer" style="justify-content:flex-end; flex-wrap:wrap; gap:6px;">
      <button class="ghost" id="${containerId}-excl-btn" disabled title="Выделите фрагмент текста в карточке, чтобы добавить его в исключения профиля" onclick="addSelectionToExclusions(${row.id}, '${containerId}')">В исключения</button>
      <button class="ghost" onclick="viewTz(${row.id})">Просмотр ТЗ</button>
      ${row.in_work
        ? `<button class="ghost" onclick="removeWorkByProc(${row.id})">Снять с работы</button>`
        : `<button class="primary" onclick="acceptWork(${row.id})">В работу</button>
           <button class="danger" onclick="openReject(${row.id})" title="Пометить как отклонённую и скрыть из выдачи">Отбраковать</button>`}
      <button class="ghost" onclick="viewRequirements(${row.id})">Требования к участнику</button>
      <button class="primary" id="analyze-btn-${row.id}" ${isAnalyzing ? "disabled" : ""} onclick="analyzeProc(${row.id}, '${containerId}')">${isAnalyzing ? "Анализ…" : "Анализ документов"}</button>
      <button onclick="pwinProc(${row.id})">Оценить P(win)/Margin</button>
      ${row.langfuse_trace_url && analyst ? `<button class="ghost" onclick="viewTrace(${row.id})">Трейс</button>` : ""}
      ${row.rag_report && row.rag_report.trace_url && analyst ? `<button class="ghost" onclick="viewTraceUrl('${escapeHtml(row.rag_report.trace_url)}')">Анализ</button>` : ""}
    </div>`;
}

// Вкладка «Данные закупки»: реквизиты карточки (без скоринга и метрик).
function cardDataPanel(row, f, files) {
  return `<table>
    ${f("Предмет", escapeHtml(row.subject || "—"))}
    ${f("Заказчик", escapeHtml(row.customer || "—") + " <span class='muted'>(id " + (row.customer_id ?? "—") + ")</span>")}
    ${f("Регион", escapeHtml(row.region || "—"))}
    ${f("Площадка", escapeHtml(row.platform_name || row.platform_id) + " <span class='muted'>(" + escapeHtml(row.platform_id) + ")</span>")}
    ${f("Тип процедуры", escapeHtml(row.procedure_type || "—"))}
    ${f("Закон", escapeHtml(row.law || "—"))}
    ${f("НМЦК", fmtMoney(row.nmck))}
    ${f("Опубликовано", fmtDate(row.publication_date))}
    ${f("Обновлено", fmtDate(row.update_date))}
    ${f("Срок подачи", fmtDate(row.deadline))}
    ${f("Активна", row.is_active ? "да" : "нет")}
    ${f("Обеспечение", fmtMoney(row.security_amount) + (row.security_amount_unit ? " " + escapeHtml(row.security_amount_unit) : ""))}
    ${f("ОКПД2", escapeHtml(row.okpd2_codes || "—"))}
    ${f("Срок исполнения", escapeHtml(row.execution_term || "—"))}
    ${f("Файлы", files)}
    ${f("Ссылка", row.url ? `<a href="${escapeHtml(row.url)}" target="_blank" rel="noopener">открыть</a>` : "—")}
  </table>`;
}

// Вкладка «Результаты скоринга и анализа»: оценки каскада + RAG-отчёт стоп-условий.
function cardScoringPanel(row, f, isAnalyzing, containerId) {
  const methodLabel = row.score_method
    ? { manual: "ручная", reject: "отклонена", fit: "fit", sim: "sim", pwin: "pwin", margin: "margin" }[row.score_method] || row.score_method
    : "—";
  const analyst = hasRole("analyst");
  // Два отдельных трейса: скоринг (langfuse_trace_url) и анализ документов (rag_report.trace_url).
  const traceLink = (url) =>
    `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">открыть трейс</a>`;
  const scoreTrace = row.langfuse_trace_url && analyst ? traceLink(row.langfuse_trace_url) : "—";
  const analysisTrace =
    row.rag_report && row.rag_report.trace_url && analyst ? traceLink(row.rag_report.trace_url) : "—";
  return `<table>
    ${f("Score", (row.score ?? "—") + " <span class='muted'>(" + escapeHtml(methodLabel) + ")</span>")}
    ${f("Fit-скор", fitCell(row))}
    ${f("P(win)", row.p_win ?? "—")}
    ${f("Margin", row.margin ?? "—")}
    ${f("Близость эмбеддингов", row.embedding_similarity ?? "—")}
    ${f("Трейс скоринга", scoreTrace)}
    ${f("Трейс анализа документов", analysisTrace)}
  </table>
  ${ragReportHtml(row.rag_report, isAnalyzing, containerId)}`;
}

// Вкладка «Метрики» (только analyst): токены, стоимость токенов, латенси,
// задержка и общее время обработки по сервисам (скоринг/анализ).
function cardMetricsPanel(row) {
  const scoringM = row.costs?.scoring;
  const analysisM = row.costs?.analysis;
  if (!scoringM && !analysisM) {
    return `<div class="empty" style="padding:20px 0;">Метрики обработки не собраны.<br><span class="muted" style="font-size:12px">Запустите скоринг и анализ документов закупки.</span></div>`;
  }
  const blocks = [];
  if (scoringM) blocks.push(metricsBlock("Скоринг (fit/judge/refine)", scoringM));
  if (analysisM) blocks.push(metricsBlock("Анализ документов", analysisM));
  const totals = metricsTotals(scoringM, analysisM);
  const costLine = cardCostLine(row);
  return `${costLine}${totals}${blocks.join("")}`;
}

// Строка стоимости обработки закупки (внутри вкладки «Метрики», analyst-only).
function cardCostLine(row) {
  const scoringUsd = row.costs?.scoring?.usd;
  const analysisUsd = row.costs?.analysis?.usd;
  const has = (scoringUsd !== undefined && scoringUsd !== null) || (analysisUsd !== undefined && analysisUsd !== null);
  if (!has || !hasRole("analyst")) return "";
  // Доли цента (например, $0.0025) не должны округляться до $0.00 — выводим
  // с той же точностью, что и «Стоимость токенов (USD)» в таблице метрик.
  const f = (v) => (Number(v) || 0).toFixed(4);
  const total = f((Number(scoringUsd) || 0) + (Number(analysisUsd) || 0));
  return `<div class="muted" style="margin:6px 0 4px;">Стоимость обработки: скоринг $${f(scoringUsd)} · анализ $${f(analysisUsd)} · всего <b>$${total}</b></div>`;
}

function _num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function metricsTotals(scoringM, analysisM) {
  const sum = (sel) => (scoringM ? sel(scoringM) : 0) + (analysisM ? sel(analysisM) : 0);
  const tokens = sum((m) => _num(m.tokens?.total));
  const calls = sum((m) => _num(m.calls));
  const usd = sum((m) => _num(m.usd));
  const latency = sum((m) => _num(m.latency_ms));
  const delay = sum((m) => _num(m.delay_ms));
  const duration = sum((m) => _num(m.duration_ms));
  return `<h4 class="mt-title">Итого по сервисам</h4>
    <table class="met">
      <tr><td>Токенов всего</td><td>${tokens.toLocaleString("ru-RU")}</td></tr>
      <tr><td>Вызовов</td><td>${calls}</td></tr>
      <tr><td>Стоимость токенов (USD)</td><td>$${usd.toFixed(4)}</td></tr>
      <tr><td>Латенси (модели)</td><td>${Math.round(latency)} мс</td></tr>
      <tr><td>Задержка (накладные)</td><td>${Math.round(delay)} мс</td></tr>
      <tr><td>Общее время обработки</td><td>${Math.round(duration)} мс</td></tr>
    </table>`;
}

function metricsBlock(title, m) {
  const has = (v) => v !== undefined && v !== null;
  const cells = (label, v) => `<tr><td>${label}</td><td>${v}</td></tr>`;
  const models = (m.models || []).join(", ") || "—";
  // Сводка по стадии: стоимость/вызовы/время (компоненты ниже дают разбивку токенов).
  const summary = `<h4 class="mt-title">${escapeHtml(title)}</h4>
    <table class="met">
      ${cells("Модели", escapeHtml(models))}
      ${cells("Вызовов", has(m.calls) ? m.calls : "—")}
      ${cells("Стоимость (USD)", has(m.usd) ? "$" + m.usd.toFixed(4) : "—")}
      ${cells("Латенси (модели)", has(m.latency_ms) ? Math.round(m.latency_ms) + " мс" : "—")}
      ${cells("Задержка (накладные)", has(m.delay_ms) ? Math.round(m.delay_ms) + " мс" : "—")}
      ${cells("Общее время обработки", has(m.duration_ms) ? Math.round(m.duration_ms) + " мс" : "—")}
    </table>`;
  const comps = m.components || {};
  const names = Object.keys(comps);
  if (!names.length) {
    // Фолбэк: нет разбивки по компонентам (старые данные) — сводная таблица токенов.
    return summary + metricsTokensTable(m);
  }
  // Токены и стоимость LLM и эмбеддингов показываем раздельно.
  const parts = names.map((n) => metricsComponentBlock(n, comps[n])).join("");
  return summary + parts;
}

// Общие строки разбивки токенов и стоимости. Используются и в блоке компонента
// (LLM/эмбеддинги), и в сводной таблице фолбэка, чтобы форматирование и точность
// (toLocaleString("ru-RU") / toFixed(4)) не расходились между двумя представлениями.
function tokenCostRows(tokens, costDetails) {
  const has = (v) => v !== undefined && v !== null;
  const cells = (label, v) => `<tr><td>${label}</td><td>${v}</td></tr>`;
  return [
    cells("Токенов (вход)", has(tokens.input) ? tokens.input.toLocaleString("ru-RU") : "—"),
    cells("Токенов (выход)", has(tokens.output) ? tokens.output.toLocaleString("ru-RU") : "—"),
    cells("Токенов (кэш)", has(tokens.input_cached_tokens) ? tokens.input_cached_tokens.toLocaleString("ru-RU") : "—"),
    cells("Токенов всего", has(tokens.total) ? tokens.total.toLocaleString("ru-RU") : "—"),
    cells("Стоимость, вход", has(costDetails.input) ? "$" + costDetails.input.toFixed(4) : "—"),
    cells("Стоимость, выход", has(costDetails.output) ? "$" + costDetails.output.toFixed(4) : "—"),
    cells("Стоимость, кэш", has(costDetails.input_cached_tokens) ? "$" + costDetails.input_cached_tokens.toFixed(4) : "—"),
  ].join("");
}

// Таблица токенов/стоимости одной составляющей стадии (llm или embeddings).
function metricsComponentBlock(name, c) {
  const has = (v) => v !== undefined && v !== null;
  const cells = (label, v) => `<tr><td>${label}</td><td>${v}</td></tr>`;
  const label = (metricsComponentName(name) || name);
  const models = (c.models || []).join(", ") || "—";
  return `<h4 class="mt-title mt-sub">${escapeHtml(label)} · <span class="muted">${escapeHtml(models)}</span></h4>
    <table class="met">
      ${cells("Вызовов", has(c.calls) ? c.calls : "—")}
      ${tokenCostRows(c.tokens || {}, c.cost_details || {})}
      ${cells("Стоимость (USD)", has(c.usd) ? "$" + c.usd.toFixed(4) : "—")}
      ${cells("Латенси (модели)", has(c.latency_ms) ? Math.round(c.latency_ms) + " мс" : "—")}
    </table>`;
}

// Человекочитаемое имя компонента метрик (llm/embeddings).
function metricsComponentName(name) {
  return { llm: "LLM", embeddings: "Эмбеддинги" }[name] || null;
}

// Сводная таблица токенов стадии (фолбэк, когда разбивки по компонентам нет).
// Показывает только разбивку токенов/стоимости: агрегат (Стоимость/Латенси/
// Задержка/Общее время) уже выведен в сводке metricsBlock выше.
function metricsTokensTable(m) {
  return `<table class="met">${tokenCostRows(m.tokens || {}, m.cost_details || {})}</table>`;
}

// Переключение вкладки карточки закупки (вызывается из inline onclick).
// containerId — "modal" или "proc-detail": карточка может одновременно
// показываться в обоих местах (напр. панель на вкладке «Закупки» + модалка
// ТЗ поверх неё), переключение вкладки в одном не должно задевать другое.
function setCardTab(tab, containerId) {
  if (tab === "metrics" && !hasRole("analyst")) tab = "data";
  if (containerId === "modal") openDetailTab = tab;
  else panelDetailTab = tab;
  const container = $("#" + containerId);
  if (!container) return;
  const btns = container.querySelectorAll("[data-cardtab]");
  const panels = container.querySelectorAll("[data-cardpanel]");
  btns.forEach((b) => b.classList.toggle("active", b.dataset.cardtab === tab));
  panels.forEach((p) => {
    const on = p.dataset.cardpanel === tab;
    p.classList.toggle("active", on);
    p.style.display = on ? "block" : "none";
  });
}

// Автообновление открытой карточки при изменении данных БД (WS): подтягивает
// результат анализа документов (rag_report) из базы и перерисовывает модалку — кнопка
// «Анализ документов» при этом восстанавливается из состояния «Анализ…».
async function refreshOpenDetail() {
  if (openDetailId === null) return;
  try {
    const row = await api("procurements/" + openDetailId);
    if (detailSig(row) !== lastDetailSig) {
      lastDetailSig = detailSig(row);
      // Результат получен — анализ завершён: снимаем блокировку кнопки.
      if (row.rag_report) analyzingIds.delete(openDetailId);
      renderModal(row);
    }
  } catch (err) {
    /* временный сбой — попробуем на следующем тике */
  }
}

// То же для панели справа от таблицы «Закупки» (см. refreshOpenDetail): rag_
// report карточки, показанной в панели, может обновиться без изменения
// update_date (сигнатура списка, sigOfProc, тогда не меняется и allItems не
// перечитывается) — отдельный запрос и патч allItems, как и у модалки.
async function refreshPanelDetail() {
  if (panelDetailId === null) return;
  try {
    const row = await api("procurements/" + panelDetailId);
    if (detailSig(row) !== lastPanelDetailSig) {
      lastPanelDetailSig = detailSig(row);
      if (row.rag_report) analyzingIds.delete(panelDetailId);
      const idx = allItems.findIndex((r) => r.id === panelDetailId);
      if (idx !== -1) allItems[idx] = Object.assign({}, allItems[idx], row);
      renderDetail();
    }
  } catch (err) {
    /* временный сбой — попробуем на следующем тике */
  }
}

// Просмотр текста ТЗ (в т.ч. из архива): открывает извлечённый Markdown в модалке.
// Закрытие возвращает к карточке закупки (id запоминается).
async function viewTz(id) {
  $("#modal").innerHTML = `
    <span class="close" onclick="closeTz(${id})">×</span>
    <h2>ТЗ закупки #${id}</h2>
    <p class="muted">Извлекаю текст…</p>`;
  $("#modal-bg").classList.add("open");
  try {
    const r = await api("procurements/" + id + "/tz");
    if (!r.found) {
      const reason = r.file_name
        ? `Не удалось извлечь текст из файла: ${escapeHtml(r.file_name)}`
        : "Файл ТЗ не найден ни среди файлов карточки, ни внутри архивов.";
      $("#modal").innerHTML = `
        <span class="close" onclick="closeTz(${id})">×</span>
        <h2>ТЗ закупки #${id}</h2>
        <p class="muted">${escapeHtml(reason)}</p>
        <div class="toolbar" style="margin-top:14px; margin-bottom:0; justify-content:flex-end;">
          <button class="primary" onclick="closeTz(${id})">Закрыть</button>
        </div>`;
      return;
    }
    $("#modal").innerHTML = `
      <span class="close" onclick="closeTz(${id})">×</span>
      <h2>ТЗ закупки #${id}</h2>
      <p class="muted" style="margin-top:0;">${escapeHtml(r.file_name)}${r.from_archive ? " <span class='pill inactive'>внутри архива</span>" : ""}</p>
      <div class="tz-view md">${renderMarkdown(r.text || "")}</div>
      <div class="toolbar" style="margin-top:14px; margin-bottom:0; justify-content:flex-end;">
        <button class="primary" onclick="closeTz(${id})">Закрыть</button>
      </div>`;
  } catch (err) {
    $("#modal").innerHTML = `
      <span class="close" onclick="closeTz(${id})">×</span>
      <h2>ТЗ закупки #${id}</h2>
      <p class="muted">Ошибка загрузки: ${escapeHtml(String(err))}</p>
      <div class="toolbar" style="margin-top:14px; margin-bottom:0; justify-content:flex-end;">
        <button class="primary" onclick="closeTz(${id})">Закрыть</button>
      </div>`;
  }
}

// Возврат из просмотра ТЗ (и «Требования к участнику», тот же обработчик).
// Модалка с полной карточкой перерисовывается, только если ДО открытия ТЗ она
// уже показывала карточку (openDetailId — приход с вкладки «В работе», где
// панели нет); если ТЗ было открыто из панели на вкладке «Закупки» — панель
// и так уже показывает карточку, просто закрываем модалку поверх неё.
async function closeTz(id) {
  closeModal();
  if (openDetailId !== null) await openDetail(id);
}

// Просмотр json-структуры «Требования к участнику» (поиск по всем документам).
// Поле БД читается как есть; если не заполнено — выполняется детерминированное
// извлечение и сохранение (эндпоинт GET /requirements). Закрытие возвращает к карточке.
async function viewRequirements(id) {
  $("#modal").innerHTML = `
    <span class="close" onclick="closeTz(${id})">×</span>
    <h2>Требования к участнику #${id}</h2>
    <p class="muted">Извлекаю структуру…</p>`;
  $("#modal-bg").classList.add("open");
  try {
    const r = await api("procurements/" + id + "/requirements");
    const req = r.requirements || {};
    if (!Object.keys(req).length) {
      $("#modal").innerHTML = `
        <span class="close" onclick="closeTz(${id})">×</span>
        <h2>Требования к участнику #${id}</h2>
        <p class="muted">Разделы «требования к участнику / к исполнителю / к составу заявки»
        не найдены ни в одном документе карточки.</p>
        <div class="toolbar" style="margin-top:14px; margin-bottom:0; justify-content:flex-end;">
          <button class="primary" onclick="closeTz(${id})">Закрыть</button>
        </div>`;
      return;
    }
    const json = JSON.stringify(req, null, 2);
    $("#modal").innerHTML = `
      <span class="close" onclick="closeTz(${id})">×</span>
      <h2>Требования к участнику #${id}</h2>
      <p class="muted" style="margin-top:0;">json-структура поля «Требования к участнику»</p>
      <pre class="tz-view" style="white-space:pre-wrap; overflow:auto; max-height:72vh;">${escapeHtml(json)}</pre>
      <div class="toolbar" style="margin-top:14px; margin-bottom:0; justify-content:flex-end;">
        <button class="primary" onclick="closeTz(${id})">Закрыть</button>
      </div>`;
  } catch (err) {
    $("#modal").innerHTML = `
      <span class="close" onclick="closeTz(${id})">×</span>
      <h2>Требования к участнику #${id}</h2>
      <p class="muted">Ошибка загрузки: ${escapeHtml(String(err))}</p>
      <div class="toolbar" style="margin-top:14px; margin-bottom:0; justify-content:flex-end;">
        <button class="primary" onclick="closeTz(${id})">Закрыть</button>
      </div>`;
  }
}

// RAG-отчёт анализа по вопросам клиента (персонализированные вопросы профиля).
// id у подсказки (${containerId}-rag-hint) — чтобы analyzeProc мог сразу
// поменять текст на «выполняется» по клику, не дожидаясь следующего опроса
// (rag_report появляется только по завершении анализа, до этого сигнатура
// карточки не меняется и полного перерендера не происходит — см.
// refreshOpenDetail/refreshPanelDetail).
function ragReportHtml(report, isAnalyzing, containerId) {
  if (!report) {
    const hint = isAnalyzing
      ? "Анализ выполняется. Вердикты по вопросам профиля появятся после завершения."
      : "Анализ не выполнялся. Нажмите «Анализ документов», чтобы получить вердикты по вопросам профиля.";
    return `<h3 style="margin:16px 0 4px;">Анализ документов</h3>
      <p class="muted" id="${containerId}-rag-hint">${hint}</p>`;
  }
  const verdictBadge = (q) => {
    const v = q.verdict;
    const cls = v === "absolute" ? "active" : "";
    let label = q.marker || "нет";
    if (v === "unavailable") {
      label = "не проверено";
    } else if (v === "absolute") {
      label = q.marker || "запрет";
    } else if (v === "soft") {
      label = q.marker || "понижает";
    } else {
      label = q.marker || "нет";
    }
    return `<span class="pill ${cls}"${v === "unavailable" ? ' title="Проверка не выполнена (недоступен LLM/эмбеддинги)"' : ""}>${escapeHtml(label)}</span>`;
  };
  if (report.tz_found === false) {
    // Различаем «файл найден, но текст не извлечён» и «файла ТЗ в карточке нет»:
    // часто в карточке есть документ с «ТЗ» в имени, но конвертация не удалась.
    const reason = report.tz_file
      ? `Файл найден (${escapeHtml(report.tz_file)}), но текст извлечь не удалось.`
      : "Файл ТЗ не найден среди файлов карточки (и внутри архивов).";
    return `<h3 style="margin:16px 0 4px;">Анализ документов</h3><p class="muted">${reason}</p>`;
  }
  let banner = "";
  if (report.status === "deferred") {
    banner = `<div style="margin:8px 0;padding:8px 10px;border:1px solid #d97706;border-radius:8px;background:rgba(217,119,6,0.08);">⚠️ Недоступен LLM/эмбеддинги — часть проверок не выполнена.${report.error ? `<span class="muted" style="display:block;margin-top:2px;">${escapeHtml(report.error)}</span>` : ""}</div>`;
  } else if (report.error) {
    banner = `<p class="muted" style="margin:8px 0;">${escapeHtml(report.error)}</p>`;
  }
  const items = (report.questions || [])
    .map(
      (q) => `
    <div style="border-top:1px solid var(--line); padding:8px 0;">
      <div style="display:flex; align-items:center; gap:8px;">
        <b>${escapeHtml(q.question_text)}</b> ${verdictBadge(q)}
        ${q.source === "system" ? '<span class="pill inactive" title="Обязательная системная проверка">обязат.</span>' : ""}
      </div>
      ${q.excerpt ? `<div class="muted" style="margin-top:4px;">«${escapeHtml(q.excerpt)}»</div>` : ""}
      ${q.reasoning ? `<div style="margin-top:4px;">${escapeHtml(q.reasoning)}</div>` : ""}
    </div>`
    )
    .join("");
  const fieldItems = (report.fields || [])
    .map((f) => {
      const value = f.found
        ? `${escapeHtml(String(f.value ?? ""))}${f.unit ? " " + escapeHtml(f.unit) : ""}`
        : '<span class="muted">не найдено</span>';
      const title = f.found && f.excerpt ? ` title="«${escapeHtml(f.excerpt)}»"` : "";
      return `<tr${title}><td>${escapeHtml(f.field_name)}</td><td>${value}</td></tr>`;
    })
    .join("");
  const fieldsBlock = fieldItems
    ? `<h3 style="margin:16px 0 4px;">Отчётные поля</h3>
       <div class="table-wrap"><table><tbody>${fieldItems}</tbody></table></div>`
    : "";
  return `<h3 style="margin:16px 0 4px;">Анализ документов</h3>
    <p class="muted" style="margin:0 0 4px;">Файл: ${escapeHtml(report.tz_file || "—")}</p>
    ${banner}
    ${items || '<p class="muted">Вопросов к документам пока нет.</p>'}
    ${fieldsBlock}`;
}

async function loadPlatforms() {
  const set = new Set();
  // Включённые площадки из справочника platforms (доступен базовым ролям,
  // в отличие от analyst-only /api/config). Активность синхронизируется из
  // config_service.yaml при старте и сохранении конфигурации.
  try {
    const cfg = await api("platforms");
    (cfg.items || []).forEach((s) => {
      if (s.enabled) set.add(s.platform_id);
    });
  } catch (err) {
    /* справочник недоступен — полагаемся на БД */
  }
  // Площадки, по которым уже есть сохранённые закупки.
  const paged = await api("procurements", { limit: 100 });
  paged.items.forEach((r) => set.add(r.platform_id));
  ["#proc-platform"].forEach((id) => {
    const sel = document.querySelector(id);
    const cur = sel.value;
    sel.innerHTML = '<option value="">Все площадки</option>';
    set.forEach((p) => {
      const o = document.createElement("option");
      o.value = p;
      o.textContent = p;
      sel.appendChild(o);
    });
    if (cur) sel.value = cur;
  });
}

function closeModal() {
  $("#modal-bg").classList.remove("open", "fullscreen");
}

// «На весь экран» — по умолчанию карточка закупки открывается боковой
// панелью (шторка #modal справа или встроенная #proc-detail), эта кнопка
// на время возвращает прежний центральный вид (класс .fullscreen, см. CSS).
// Из встроенной панели (#proc-detail) полноэкранный режим есть только у
// шторки — переоткрываем ту же закупку в ней, сразу в режиме .fullscreen.
function toggleCardFullscreen(containerId, id) {
  if (containerId === "proc-detail") {
    openDetail(id).then(() => $("#modal-bg").classList.add("fullscreen"));
    return;
  }
  $("#modal-bg").classList.toggle("fullscreen");
}

// Печать карточки закупки: клонируем её текущее содержимое в отдельное
// печатное окно (минимальные стили, без кнопок действий) — чтобы не тащить
// печать всей страницы приложения через сложные @media print правила.
function printCard(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const w = window.open("", "_blank", "width=800,height=900");
  if (!w) return;
  w.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>Печать закупки</title>
    <style>
      body { font-family: system-ui, sans-serif; padding:24px; color:#1f2937; }
      table { width:100%; border-collapse:collapse; font-size:13px; margin:0 0 14px; }
      td, th { text-align:left; padding:6px 8px; border-bottom:1px solid #d8dde6; vertical-align:top; }
      td:first-child { color:#6b7280; width:34%; }
      h2 { margin-top:0; }
      .close, .card-footer, .card-icon-actions, [id$="-op-status"] { display:none !important; }
      .card-tab-panel { display:block !important; }
      .tabs { display:none !important; }
    </style></head><body>${el.innerHTML}</body></html>`);
  w.document.close();
  w.focus();
  setTimeout(() => w.print(), 300);
}

// Выгрузка одной карточки закупки в XLSX (кнопка-иконка на панели).
async function exportCardXlsx(id, containerId) {
  const r = await fetch(`/api/procurements/${id}/export.xlsx`, { headers: authHeaders() });
  if (!r.ok) {
    setOpStatus(containerId, "не удалось выгрузить Excel: " + (await apiErrorDetail(r)), true);
    return;
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `procurement_${id}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Открыть LangFuse-трейс закупки в новой вкладке. Доступно ТОЛЬКО роли analyst:
// сама кнопка «Трейс» рендерится только при hasRole("analyst") и непустой ссылке
// (procurement_evaluations.langfuse_trace_url); ссылки нет, если LangFuse не
// настроен/недоступен.
function viewTrace(id) {
  if (!hasRole("analyst")) return;
  const row = allItems.find((r) => r.id === id);
  const url = row && row.langfuse_trace_url;
  if (url) window.open(url, "_blank", "noopener");
}

// Открыть произвольный LangFuse-трейс (анализ) в новой вкладке. Доступно ТОЛЬКО
// роли analyst; url берётся из rag_report.trace_url (построен analysis_service).
function viewTraceUrl(url) {
  if (!hasRole("analyst") || !url) return;
  window.open(url, "_blank", "noopener");
}

// Переключить кнопку «Анализ документов» в состояние «выполняется»/«готово».
function setAnalyzeBtn(id, analyzing) {
  const btn = document.getElementById("analyze-btn-" + id);
  if (!btn) return;
  btn.disabled = analyzing;
  btn.textContent = analyzing ? "Анализ…" : "Анализ документов";
}

// Статус операции — ПРЯМО в карточке закупки (модалка/панель), т.к. кнопка
// нажимается из карточки. В шапку (#parser-status) дублировать не нужно —
// карточка уже показывает результат, а из-под модалки шапка всё равно не
// видна. Шапка — только запасной вариант, если локального блока вдруг нет.
function setOpStatus(containerId, text, isError) {
  const local = containerId ? document.getElementById(`${containerId}-op-status`) : null;
  if (local) {
    local.textContent = text;
    local.style.color = isError ? "#dc2626" : "";
    return;
  }
  const header = $("#parser-status");
  header.textContent = text;
  header.style.color = isError ? "#dc2626" : "";
}

async function analyzeProc(id, containerId) {
  analyzingIds.add(id);
  setAnalyzeBtn(id, true);
  setOpStatus(containerId, `Закупка #${id}: анализ документов поставлен в очередь…`, false);
  let ok = false;
  try {
    const r = await apiJSON("/api/procurements/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ procurement_ids: [id] }),
    });
    ok = r.ok;
    if (ok) {
      setOpStatus(containerId, `Закупка #${id}: анализ документов запущен, жду результат…`, false);
      // Пока rag_report не пришёл, сигнатура карточки не меняется и полного
      // перерендера не будет (см. refreshOpenDetail/refreshPanelDetail) —
      // подсказку «анализ не выполнялся» иначе не заменить на «выполняется»
      // до самого завершения, что вводит пользователя в заблуждение.
      const hintEl = containerId && document.getElementById(`${containerId}-rag-hint`);
      if (hintEl) {
        hintEl.textContent = "Анализ выполняется. Вердикты по вопросам профиля появятся после завершения.";
      }
    } else {
      setOpStatus(containerId, `Закупка #${id}: ` + (await apiErrorDetail(r)), true);
    }
  } catch (err) {
    setOpStatus(containerId, "не удалось запустить анализ документов: " + (err.message || err), true);
  }
  if (!ok) {
    analyzingIds.delete(id);
    setAnalyzeBtn(id, false);
    return;
  }
  // Страховка: анализ обычно занимает секунды; через 3 минуты снимаем блокировку,
  // если результат так и не пришёл (воркер недоступен / очередь зависла).
  setTimeout(() => {
    if (analyzingIds.has(id)) {
      analyzingIds.delete(id);
      setAnalyzeBtn(id, false);
    }
  }, 180000);
}

async function pwinProc(id) {
  try {
    const r = await apiJSON("/api/procurements/pwin-margin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ procurement_ids: [id] }),
    });
    $("#parser-status").textContent = r.ok
      ? `Закупка #${id}: поставлена оценка P(win)/Margin…`
      : "не удалось поставить P(win)/Margin";
  } catch (err) {
    $("#parser-status").textContent = "не удалось поставить P(win)/Margin: " + (err.message || err);
  }
}

// --- «В работу» / «Отбраковать» (Эпик 5, US-5.1/5.2, US-5.4) ---------------

async function acceptWork(id) {
  try {
    const r = await apiJSON("/api/procurements/" + id + "/work", { method: "POST" });
    if (!r.ok) {
      $("#parser-status").textContent = "не удалось принять «в работу»";
      return;
    }
    $("#parser-status").textContent = `Закупка #${id} принята «в работу»`;
    await loadProc();
    // Перерисовать карточку в модалке нужно, только если она сейчас показана
    // там (вкладка «В работе», без панели) — на «Закупки» loadProc уже
    // обновил панель через renderProc -> renderDetail.
    if (openDetailId !== null) await openDetail(id);
  } catch (err) {
    $("#parser-status").textContent = "не удалось принять «в работу»: " + (err.message || err);
  }
}

async function removeWorkByProc(id) {
  try {
    const r = await apiJSON("/api/procurements/" + id + "/work", { method: "DELETE" });
    if (!r.ok) {
      $("#parser-status").textContent = "не удалось снять с работы";
      return;
    }
    $("#parser-status").textContent = `Закупка #${id} снята с «в работе»`;
    await loadProc();
    if (openDetailId !== null) await openDetail(id);
  } catch (err) {
    $("#parser-status").textContent = "не удалось снять с работы: " + (err.message || err);
  }
}

// Добавление закупки «в работу» по URL карточки на ЭТП (живая подгрузка,
// см. POST /api/procurements/by-url) — для закупок, найденных не через наш
// поиск (напр. другим инструментом тендеролога), в т.ч. вне фильтров профиля.
async function addProcurementByUrl() {
  const input = $("#proc-add-url");
  const url = (input.value || "").trim();
  const status = $("#proc-add-url-status");
  if (!url) {
    status.textContent = "Укажите URL закупки на ЭТП";
    status.style.color = "#dc2626";
    return;
  }
  status.style.color = "";
  status.textContent = "добавляю…";
  try {
    const r = await apiJSON("/api/procurements/by-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!r.ok) {
      status.textContent = await apiErrorDetail(r);
      status.style.color = "#dc2626";
      return;
    }
    input.value = "";
    status.textContent = "Закупка добавлена «в работу» ✓";
    await loadProc();
  } catch (err) {
    status.textContent = "не удалось добавить: " + (err.message || err);
    status.style.color = "#dc2626";
  }
}

// Модалка «Отбраковать»: причина + опции «убрать ключевые слова» / «добавить
// слово-исключение». Предложение слов (US-5.3) отложено — действия только явные.
async function openReject(id) {
  $("#modal").innerHTML = `
    <span class="close" onclick="closeReject(${id})">×</span>
    <h2>Отбраковать закупку #${id}</h2>
    <p class="muted" style="margin-top:0;">Закупка будет помечена как отклонённая и скрыта из будущих выдач (показ отклонённых можно включить фильтром).</p>
    <label style="display:block; margin:10px 0;">Причина отклонения (необязательно)
      <textarea id="reject-reason" rows="2" spellcheck="false" placeholder="например, не наш профиль, слишком низкая НМЦК…" style="width:100%;"></textarea>
    </label>
    <label style="display:flex; align-items:center; gap:8px; cursor:pointer;" title="Удалить из профиля ключевые слова, по которым эта закупка была отобрана (matched_keywords)">
      <input type="checkbox" id="reject-remove-matched"> Убрать из профиля ключевые слова, по которым отобрана закупка
    </label>
    <label style="display:block; margin:10px 0;">Добавить слово-исключение в профиль (необязательно)
      <input type="text" id="reject-excl" placeholder="фраза или слово" spellcheck="false" style="width:100%;">
    </label>
    <div class="toolbar" style="margin-top:14px; margin-bottom:0; justify-content:flex-end;">
      <button class="ghost" onclick="closeReject(${id})">Отмена</button>
      <button class="danger" id="reject-confirm" onclick="doReject(${id})">Отбраковать</button>
      <span id="reject-status" class="muted"></span>
    </div>`;
  $("#modal-bg").classList.add("open");
}

async function closeReject(id) {
  closeModal();
  if (openDetailId !== null) await openDetail(id);
}

async function doReject(id) {
  const reason = ($("#reject-reason").value || "").trim() || null;
  const removeMatched = $("#reject-remove-matched").checked;
  const exclusion = ($("#reject-excl").value || "").trim() || null;
  const btn = $("#reject-confirm");
  if (btn) btn.disabled = true;
  try {
    const r = await apiJSON("/api/procurements/" + id + "/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rejection_reason: reason,
        remove_matched_keywords: removeMatched,
        exclusion_word: exclusion,
      }),
    });
    if (!r.ok) {
      if ($("#reject-status")) {
        $("#reject-status").textContent = "не удалось отбраковать";
        $("#reject-status").style.color = "#dc2626";
      }
      if (btn) btn.disabled = false;
      return;
    }
    $("#parser-status").textContent = `Закупка #${id} отклонена`;
    await loadProc();
    closeModal();
  } catch (err) {
    if ($("#reject-status")) {
      $("#reject-status").textContent = "ошибка: " + (err.message || err);
      $("#reject-status").style.color = "#dc2626";
    }
    if (btn) btn.disabled = false;
  }
}

// Текущее выделение в карточке закупки (модалка ИЛИ панель #proc-detail) ->
// фраза для исключений, либо null, если выделения нет/оно пустое/вне карточки.
// containerId — "modal" или "proc-detail" (см. cardBodyHtml): проверяем
// выделение именно в ТОМ контейнере, откуда нажата кнопка — оба контейнера
// могут одновременно существовать в DOM (панель под модалкой ТЗ/Отбраковать).
// Если правый край выделения приходится на середину слова, к фразе
// добавляется хвостовая «*» (стеб-поиск — единственный вид wildcard, который
// понимает движок фильтрации, см. filtering.py::_token_regex); обрыв слева на
// wildcard не влияет — ведущих «*» движок не поддерживает, добавлять её было
// бы бессмысленно.
function selectionExclusionPhrase(containerId) {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const container = $("#" + containerId);
  if (!container) return null;
  const range = sel.getRangeAt(0);
  if (!container.contains(range.commonAncestorContainer)) return null;
  const raw = range.toString();
  const trimmed = raw.trim();
  if (!trimmed) return null;
  let cutRight = false;
  if (range.endContainer.nodeType === Node.TEXT_NODE) {
    const value = range.endContainer.textContent || "";
    // endOffset относится к «сырому» (необрезанному) выделению — сдвигаем его
    // назад на длину обрезанных пробелов в конце, чтобы попасть на конец trimmed.
    const trailingWs = raw.length - raw.replace(/\s+$/, "").length;
    const nextChar = value.charAt(range.endOffset - trailingWs);
    if (nextChar && /[\p{L}\p{N}]/u.test(nextChar)) cutRight = true;
  }
  return cutRight ? trimmed + "*" : trimmed;
}

async function addSelectionToExclusions(id, containerId) {
  const phrase = selectionExclusionPhrase(containerId);
  if (!phrase) return;
  const btn = $("#" + containerId + "-excl-btn");
  if (btn) btn.disabled = true;
  try {
    const r = await apiJSON("/api/procurements/" + id + "/exclusion-word", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word: phrase }),
    });
    if (!r.ok) {
      $("#parser-status").textContent = "не удалось добавить в исключения: " + (await apiErrorDetail(r));
      $("#parser-status").style.color = "#dc2626";
      return;
    }
    const data = await r.json();
    $("#parser-status").textContent = data.added
      ? `Добавлено в исключения: «${phrase}»`
      : `Уже есть в исключениях: «${phrase}»`;
    $("#parser-status").style.color = "";
    window.getSelection().removeAllRanges();
  } catch (err) {
    $("#parser-status").textContent = "ошибка: " + (err.message || err);
    $("#parser-status").style.color = "#dc2626";
  }
}

function updateSelUi() {
  const n = selected.size;
  $("#batch-analyze").disabled = !n;
  $("#batch-pwin-margin").disabled = !n;
  $("#sel-count").textContent = n ? `выбрано: ${n}` : "";
}

export {
  updateMinFit,
  renderProc,
  goProcPage,
  loadProc,
  pollProc,
  openDetail,
  closeModal,
  analyzeProc,
  pwinProc,
  viewTz,
  viewRequirements,
  closeTz,
  viewTrace,
  viewTraceUrl,
  setCardTab,
  loadPlatforms,
  acceptWork,
  removeWorkByProc,
  openReject,
  closeReject,
  doReject,
  addSelectionToExclusions,
  toggleCardFullscreen,
  printCard,
  exportCardXlsx,
};

// Гейт «мониторинг без скоринга» (BR-09): узнав доступность опции scoring,
// применяем состояние фильтров. Без скоринга фильтр «Только релевантные»
// (fit-порог) отключаем, сортировку по умолчанию меняем на дату и показываем
// подсказку — в таблице видны все собранные закупки, в т.ч. без fit-оценки.
scoringGate().then((hasScoring) => {
  const rel = $("#proc-relevant");
  if (!hasScoring) {
    if (rel) {
      rel.checked = false;
      rel.disabled = true;
      rel.title = "Скоринг отключён — фильтр по fit-оценке недоступен";
    }
    updateMinFit();
    const sortSel = $("#proc-sort");
    if (sortSel && sortSel.value === "fit_score") sortSel.value = "publication_date";
    const note = $("#proc-no-scoring-note");
    if (note) note.style.display = "";
    // Список мог загрузиться с фильтром «scored» до выяснения опций — перечитываем.
    const view = $("#view-proc");
    if (view && view.style.display !== "none") loadProc();
  }
});

$("#proc-rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  const id = Number(tr.dataset.id);
  // Чекбокс/Ctrl+клик — ТОЛЬКО пакетный выбор для массовых операций (анализ/
  // P(win)/Margin), не влияет на то, какая карточка показана в панели справа
  // (см. panelDetailId) — решение пользователя: несколько карточек больше не
  // показываются одновременно, но пакетные операции остаются как были.
  if (e.target.classList.contains("row-sel")) {
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    e.target.checked = selected.has(id);
    updateSelUi();
    return;
  }
  if (e.ctrlKey) {
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    updateSelUi();
    return;
  }
  // Обычный клик по строке — показать ЕЁ полную карточку в панели (и только
  // её; предыдущая, если была другая, скрывается).
  panelDetailId = id;
  panelDetailTab = "data";
  document.querySelectorAll("#proc-rows tr").forEach((r) => r.classList.toggle("sel", r === tr));
  renderDetail();
});
$("#sel-all").addEventListener("change", (e) => {
  allItems.forEach((r) => {
    if (e.target.checked) selected.add(r.id);
    else selected.delete(r.id);
  });
  renderProc();
  updateSelUi();
});
$("#batch-analyze").addEventListener("click", async () => {
  const ids = [...selected];
  $("#parser-status").style.color = "";
  $("#parser-status").textContent = `Ставлю анализ документов для ${ids.length} закупок…`;
  try {
    const r = await apiJSON("/api/procurements/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ procurement_ids: ids }),
    });
    if (r.ok) {
      $("#parser-status").textContent = `Поставлен анализ документов для ${ids.length} закупок…`;
    } else {
      $("#parser-status").textContent = await apiErrorDetail(r);
      $("#parser-status").style.color = "#dc2626";
    }
  } catch (err) {
    $("#parser-status").textContent = "не удалось поставить анализ: " + (err.message || err);
    $("#parser-status").style.color = "#dc2626";
  }
});
$("#batch-pwin-margin").addEventListener("click", async () => {
  const ids = [...selected];
  const r = await apiJSON("/api/procurements/pwin-margin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ procurement_ids: ids }),
  });
  $("#parser-status").textContent = r.ok
    ? `Поставлена оценка P(win)/Margin для ${ids.length} закупок…`
    : "не удалось поставить P(win)/Margin";
});
$("#modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "modal-bg") closeModal();
});
// Кнопка «В исключения» доступна, только пока в карточке выделен непустой
// фрагмент текста (по умолчанию недоступна — включается выделением мышью).
document.addEventListener("selectionchange", () => {
  // Модалка и панель могут одновременно быть в DOM (напр. модалка ТЗ поверх
  // панели «Закупки») — обновляем каждую кнопку «В исключения» независимо, по
  // выделению именно в ЕЁ контейнере.
  ["modal", "proc-detail"].forEach((containerId) => {
    const btn = $("#" + containerId + "-excl-btn");
    if (btn) btn.disabled = selectionExclusionPhrase(containerId) === null;
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});
$("#proc-sort").addEventListener("change", () => {
  procPage = 1;
  loadProc();
});
$("#proc-prev").addEventListener("click", () => goProcPage(procPage - 1));
$("#proc-next").addEventListener("click", () => goProcPage(procPage + 1));
$("#proc-relevant").addEventListener("change", () => {
  localStorage.setItem("zp_relevant", $("#proc-relevant").checked ? "1" : "0");
  updateMinFit();
  procPage = 1;
  loadProc();
});
$("#proc-min-fit").addEventListener("change", () => {
  procPage = 1;
  loadProc();
});
function stepFit(d) {
  const input = $("#proc-min-fit");
  let v = parseFloat(input.value);
  if (isNaN(v)) v = 0.4;
  v = Math.min(0.9, Math.max(0, Math.round((v + d) * 10) / 10));
  input.value = v;
  procPage = 1;
  loadProc();
}
$("#fit-up").addEventListener("click", () => stepFit(0.1));
$("#fit-dn").addEventListener("click", () => stepFit(-0.1));
$("#proc-platform").addEventListener("change", () => {
  procPage = 1;
  loadProc();
});
$("#proc-active").addEventListener("change", () => {
  procPage = 1;
  loadProc();
});
$("#proc-in-work").addEventListener("change", () => {
  procPage = 1;
  loadProc();
});
$("#proc-add-url-btn").addEventListener("click", addProcurementByUrl);
$("#proc-add-url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") addProcurementByUrl();
});
