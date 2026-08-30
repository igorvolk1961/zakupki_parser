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
import { api, apiJSON } from "./api.js";
import { hasRole } from "./roles.js";

let allItems = [];
let selected = new Set();
const PROC_PAGE_SIZE = 100;
let procPage = 1;
let procTotal = 0;

// Состояние анализа ТЗ: id закупок, для которых сейчас выполняется анализ.
// Кнопка «Анализ ТЗ» блокируется до получения результата (см. pollProc).
const analyzingIds = new Set();
// id открытой карточки (модалки) и сигнатура её rag_report для автообновления.
let openDetailId = null;
let lastDetailSig = "";

function updateMinFit() {
  $("#min-fit-wrap").style.display = $("#proc-relevant").checked ? "inline" : "none";
}

function card(row) {
  return `<div class="card selcard" data-id="${row.id}">
    <div class="num">${row.number}</div>
    <div class="subj">${escapeHtml(row.subject || "—")}</div>
    <div class="row"><span>Заказчик</span><b>${escapeHtml(row.customer || "—")}</b></div>
    <div class="row"><span>Тип процедуры</span><b>${escapeHtml(row.procedure_type || "—")}</b></div>
    <div class="row"><span>НМЦК</span><b>${fmtMoney(row.nmck)}</b></div>
    <div class="row"><span>Срок подачи</span><b>${fmtDate(row.deadline)}</b></div>
    <div class="row"><span>Статус</span><span class="pill ${row.is_active ? "active" : "inactive"}">${row.is_active ? "Активна" : "Не активна"}</span></div>
    <div class="row"><span>Площадка</span><span class="pill">${escapeHtml(row.platform_name || row.platform_id)}</span>
      <span class="pill score">score ${row.score ?? "—"}</span>
      <span class="pill score">fit ${fitCell(row)}</span>
      <span class="pill score">sim ${row.embedding_similarity ?? "—"}</span>
      ${row.langfuse_trace_url && hasRole("analyst") ? `<a class="pill" href="${escapeHtml(row.langfuse_trace_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">трейс</a>` : ""}</div>
  </div>`;
}

function procRow(row) {
  const methodLabel = row.score_method
    ? { manual: "ручная", reject: "отклонена" }[row.score_method] || row.score_method
    : "";
  const rg = row.rag_report ? ` data-rag="${escapeHtml(JSON.stringify(row.rag_report))}"` : "";
  return `<tr data-id="${row.id}" class="${selected.has(row.id) ? "sel" : ""}"${rg}>
    <td><input type="checkbox" class="row-sel" data-id="${row.id}" ${selected.has(row.id) ? "checked" : ""}></td>
    <td class="id">${row.id}</td>
    <td><div class="num">${escapeHtml(row.number)}</div><div class="subj">${escapeHtml(row.subject || "—")}</div></td>
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

function selectedItems() {
  return allItems.filter((r) => selected.has(r.id));
}

function renderDetail() {
  const items = selectedItems();
  const panel = $("#proc-detail");
  if (!items.length) {
    panel.innerHTML = `<div class="empty">Выберите закупку слева<br><span class="muted" style="font-size:12px">Несколько — удерживайте Ctrl при клике</span></div>`;
    return;
  }
  panel.innerHTML = items.map(card).join("");
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

function procParams() {
  const params = { limit: PROC_PAGE_SIZE, offset: (procPage - 1) * PROC_PAGE_SIZE };
  if ($("#proc-sort").value) params.sort = $("#proc-sort").value;
  if ($("#proc-platform").value) params.platform_id = $("#proc-platform").value;
  if ($("#proc-active").value !== "") params.active = $("#proc-active").value === "1";
  if ($("#proc-relevant").checked) params.min_fit_score = $("#proc-min-fit").value;
  // Закупки без fit-score (ещё не обработаны конвейером скоринга) в таблице не показываем.
  params.scored = true;
  return params;
}

async function loadProc() {
  clampPage();
  const data = await api("procurements", procParams());
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
    const data = await api("procurements", procParams());
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
    // Открытая карточка могла получить результат анализа ТЗ (rag_report), даже если
    // сигнатура списка не изменилась — обновляем её независимо от списка.
    await refreshOpenDetail();
  } catch (err) {
    /* временные сбои игнорируем — попробуем на следующем тике */
  }
}

async function openDetail(id) {
  const row = await api("procurements/" + id);
  openDetailId = id;
  lastDetailSig = detailSig(row);
  renderModal(row);
}

// Сигнатура RAG-отчёта карточки — сравнение для автообновления модалки.
function detailSig(row) {
  return JSON.stringify(row.rag_report || null);
}

function renderModal(row) {
  const f = (label, v) => `<tr><td>${label}</td><td>${v}</td></tr>`;
  const files =
    (row.files_json || [])
      .map(
        (x) =>
          `<div><a href="${escapeHtml(x.url)}" target="_blank" rel="noopener">${escapeHtml(x.name)}</a></div>`
      )
      .join("") || "–";
  const isAnalyzing = analyzingIds.has(row.id);
  $("#modal").innerHTML = `
    <span class="close" onclick="closeModal()">×</span>
    <h2>${escapeHtml(row.number)}</h2>
    <table>
      ${f("Предмет", escapeHtml(row.subject || "—"))}
      ${f("Заказчик", escapeHtml(row.customer || "—") + " <span class='muted'>(id " + (row.customer_id ?? "—") + ")</span>")}
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
      ${f("Score", (row.score ?? "—") + " (" + escapeHtml(row.score_method || "") + ")")}
      ${f("Fit-скор", fitCell(row))}
      ${f("Близость эмбеддингов", row.embedding_similarity ?? "—")}
      ${f("Срок исполнения", escapeHtml(row.execution_term || "—"))}
      ${f("Файлы", files)}
      ${f("Ссылка", row.url ? `<a href="${escapeHtml(row.url)}" target="_blank" rel="noopener">открыть</a>` : "—")}
    </table>
    ${ragReportHtml(row.rag_report)}
    <div class="toolbar" style="margin-top:14px; margin-bottom:0; justify-content:flex-end;">
      <button class="ghost" onclick="viewTz(${row.id})">Просмотр ТЗ</button>
      <button class="primary" id="analyze-btn-${row.id}" ${isAnalyzing ? "disabled" : ""} onclick="analyzeProc(${row.id})">${isAnalyzing ? "Анализ…" : "Анализ ТЗ"}</button>
      <button onclick="pwinProc(${row.id})">Оценить P(win)/Margin</button>
      ${row.langfuse_trace_url && hasRole("analyst") ? `<button class="ghost" onclick="viewTrace(${row.id})">Трейс</button>` : ""}
      ${row.rag_report && row.rag_report.trace_url && hasRole("analyst") ? `<button class="ghost" onclick="viewTraceUrl('${escapeHtml(row.rag_report.trace_url)}')">Анализ</button>` : ""}
    </div>`;
  $("#modal-bg").classList.add("open");
}

// Автообновление открытой карточки при изменении данных БД (WS): подтягивает
// результат анализа ТЗ (rag_report) из базы и перерисовывает модалку — кнопка
// «Анализ ТЗ» при этом восстанавливается из состояния «Анализ…».
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
      <pre class="tz-view">${escapeHtml(r.text || "")}</pre>
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

// Возврат из просмотра ТЗ к карточке закупки (модалка перерисовывается).
async function closeTz(id) {
  closeModal();
  await openDetail(id);
}

// RAG-отчёт анализа стоп-условий (обязательные проверки + вопросы клиента).
function ragReportHtml(report) {
  if (!report) {
    return `<h3 style="margin:16px 0 4px;">Анализ ТЗ (стоп-условия)</h3>
      <p class="muted">Анализ не выполнялся. Нажмите «Анализ ТЗ», чтобы получить
      вердикты по стоп-условиям и вопросам профиля.</p>`;
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
    return `<h3 style="margin:16px 0 4px;">Анализ ТЗ</h3><p class="muted">ТЗ не найдено.</p>`;
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
  return `<h3 style="margin:16px 0 4px;">Анализ ТЗ (стоп-условия)</h3>
    <p class="muted" style="margin:0 0 4px;">Файл: ${escapeHtml(report.tz_file || "—")}</p>
    ${report.cost ? `<p class="muted" style="margin:2px 0 4px;">Стоимость анализа: LLM $${report.cost.llm_usd ?? 0} · эмбеддинги $${report.cost.embeddings_usd ?? 0} · всего <b>$${report.cost.total_usd ?? 0}</b></p>` : ""}
    ${banner}
    ${items || '<p class="muted">Вопросов к ТЗ пока нет.</p>'}`;
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
  $("#modal-bg").classList.remove("open");
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

// Переключить кнопку «Анализ ТЗ» в состояние «выполняется»/«готово».
function setAnalyzeBtn(id, analyzing) {
  const btn = document.getElementById("analyze-btn-" + id);
  if (!btn) return;
  btn.disabled = analyzing;
  btn.textContent = analyzing ? "Анализ…" : "Анализ ТЗ";
}

async function analyzeProc(id) {
  analyzingIds.add(id);
  setAnalyzeBtn(id, true);
  $("#parser-status").textContent = `Закупка #${id}: анализ ТЗ поставлен в очередь…`;
  let ok = false;
  try {
    const r = await apiJSON("/api/procurements/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ procurement_ids: [id] }),
    });
    ok = r.ok;
    $("#parser-status").textContent = ok
      ? `Закупка #${id}: анализ ТЗ запущен, жду результат…`
      : "не удалось поставить анализ ТЗ (транспорт не настроен?)";
  } catch (err) {
    $("#parser-status").textContent = "не удалось запустить анализ ТЗ: " + (err.message || err);
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
  closeTz,
  viewTrace,
  viewTraceUrl,
  loadPlatforms,
};

$("#proc-rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  const id = Number(tr.dataset.id);
  if (e.target.classList.contains("row-sel")) {
    if (selected.has(id)) {
      selected.delete(id);
    } else {
      selected.add(id);
    }
    e.target.checked = selected.has(id);
    tr.classList.toggle("sel", selected.has(id));
    renderDetail();
    updateSelUi();
    return;
  }
  if (e.ctrlKey) {
    if (selected.has(id)) {
      selected.delete(id);
      tr.classList.remove("sel");
    } else {
      selected.add(id);
      tr.classList.add("sel");
    }
  } else {
    selected = new Set([id]);
    document.querySelectorAll("#proc-rows tr").forEach((r) => r.classList.toggle("sel", r === tr));
  }
  renderDetail();
  updateSelUi();
});
$("#sel-all").addEventListener("change", (e) => {
  allItems.forEach((r) => {
    if (e.target.checked) selected.add(r.id);
    else selected.delete(r.id);
  });
  renderProc();
  renderDetail();
  updateSelUi();
});
$("#batch-analyze").addEventListener("click", async () => {
  const ids = [...selected];
  $("#parser-status").textContent = `Ставлю RAG-анализ для ${ids.length} закупок…`;
  try {
    const r = await apiJSON("/api/procurements/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ procurement_ids: ids }),
    });
    $("#parser-status").textContent = r.ok
      ? `Поставлен RAG-анализ для ${ids.length} закупок…`
      : "не удалось поставить анализ (транспорт не настроен?)";
  } catch (err) {
    $("#parser-status").textContent = "не удалось поставить анализ: " + (err.message || err);
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
$("#proc-detail").addEventListener("click", (e) => {
  const card = e.target.closest(".card");
  if (card) openDetail(card.dataset.id);
});
$("#modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "modal-bg") closeModal();
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
