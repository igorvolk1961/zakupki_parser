"use strict";

// Вкладка «В работе»: единый механизм с вкладкой «Закупки» — список закупок
// активного профиля, принятых «в работу», берётся тем же эндпоинтом, что и
// результаты поиска (GET /api/procurements?in_work=true), и обрабатывается так
// же (та же карточка/анализ/скоринг). Единственное отличие: к закупкам «в работе»
// не применимы операции «В работу» и «Отбраковать» — доступно «Снять с работы».
// Записи-снимки по URL, для которых закупки ещё нет в базе парсера (или она
// удалена при очистке БД), показываются отдельным блоком ниже.
import { $, escapeHtml, fmtDate, fmtMoney, fitCell } from "./utils.js";
import { api, apiJSON } from "./api.js";
import { openDetail } from "./procurements.js";

const WORK_LIMIT = 1000;
let workTotal = 0;
let workRows = [];
let orphans = [];

function workRow(row) {
  return `<tr data-id="${row.id}">
    <td class="id">${row.id}</td>
    <td>
      <div class="num">${escapeHtml(row.number)}</div>
      <div class="subj">${escapeHtml(row.subject || "—")} <span class="pill active">в работе</span></div>
    </td>
    <td><span class="pill">${escapeHtml(row.platform_name || row.platform_id)}</span></td>
    <td><span class="pill score">${fitCell(row)}</span></td>
    <td>${fmtMoney(row.nmck)}</td>
    <td>${fmtDate(row.deadline)}</td>
    <td style="white-space:nowrap;">
      <button class="ghost" onclick="openWorkCard(${row.id})">Анализировать</button>
      <button class="ghost" onclick="removeFromWork(${row.id})">Снять с работы</button>
    </td>
  </tr>`;
}

function orphanRow(item) {
  return `<tr>
    <td><a href="${escapeHtml(item.url || "")}" target="_blank" rel="noopener">${escapeHtml(item.url || "—")}</a></td>
    <td>${fmtDate(item.accepted_at)}</td>
    <td><button class="ghost" onclick="removeWorkItem(${item.id})">Удалить</button></td>
  </tr>`;
}

function renderWork() {
  const tbody = $("#work-rows");
  const empty = $("#work-empty");
  if (!workRows.length) {
    tbody.innerHTML = "";
    empty.style.display = "block";
  } else {
    empty.style.display = "none";
    tbody.innerHTML = workRows.map(workRow).join("");
  }
  const orphanWrap = $("#work-orphans-wrap");
  const orphanBody = $("#work-orphans");
  if (orphans.length) {
    orphanBody.innerHTML = orphans.map(orphanRow).join("");
    orphanWrap.style.display = "";
  } else {
    orphanWrap.style.display = "none";
    orphanBody.innerHTML = "";
  }
  $("#cnt-work").textContent = workTotal + orphans.length;
}

function workStatus(msg, isError) {
  const el = $("#work-status");
  if (!el) return;
  el.textContent = msg || "";
  el.style.color = isError ? "#dc2626" : "";
  if (msg) {
    setTimeout(() => {
      if ($("#work-status")) $("#work-status").textContent = "";
    }, 6000);
  }
}

export async function loadWork() {
  try {
    const data = await api("procurements", {
      in_work: true,
      limit: WORK_LIMIT,
      offset: 0,
    });
    workTotal = data.total || 0;
    workRows = data.items || [];
    // Записи-снимки по URL без закупки в базе (procurement_id IS NULL): BR-08 —
    // не пропадают при очистке результатов поиска и остаются управляемыми.
    const all = await api("procurements/work");
    orphans = (all.items || []).filter((i) => i.procurement_id == null);
    renderWork();
  } catch (err) {
    /* вкладка закрыта/недоступна — попробуем на следующем тике */
  }
}

export async function pollWork() {
  await loadWork();
}

export function openWorkCard(procurementId) {
  if (procurementId == null) return;
  openDetail(procurementId);
}

export async function addWorkByUrl() {
  const input = $("#work-url");
  const url = (input.value || "").trim();
  if (!url) {
    workStatus("Укажите URL закупки на ЭТП", true);
    return;
  }
  workStatus("принимаю в работу…");
  try {
    const r = await apiJSON("/api/procurements/work/by-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!r.ok) {
      const text = await r.text();
      workStatus("не удалось принять по URL: " + (text || "ошибка"), true);
      return;
    }
    input.value = "";
    await loadWork();
    workStatus("Закупка принята «в работу» ✓");
  } catch (err) {
    workStatus("не удалось принять по URL: " + (err.message || err), true);
  }
}

// Снятие с работы закупки из единого списка (по id закупки): удаляется только
// запись «в работе», закупка остаётся в общей выдаче.
export async function removeFromWork(procurementId) {
  const row = workRows.find((w) => w.id === procurementId);
  const label = row && (row.number || row.subject);
  if (!window.confirm(`Снять закупку${label ? ` «${label}»` : ""} с «в работе»?\nЗакупка останется в общей выдаче.`)) {
    return;
  }
  try {
    const r = await apiJSON("/api/procurements/" + procurementId + "/work", { method: "DELETE" });
    if (!r.ok) {
      workStatus("не удалось снять с работы", true);
      return;
    }
    await loadWork();
    workStatus("Закупка снята с «в работе»");
  } catch (err) {
    workStatus("не удалось снять с работы: " + (err.message || err), true);
  }
}

// Удаление записи-снимка по URL (без закупки в базе) по id записи «в работе».
export async function removeWorkItem(workItemId) {
  const item = orphans.find((w) => w.id === workItemId);
  const label = item && (item.number || item.url);
  if (!window.confirm(`Удалить запись${label ? ` «${label}»` : ""} из «в работе»?`)) {
    return;
  }
  try {
    const r = await apiJSON("/api/procurements/work/" + workItemId, { method: "DELETE" });
    if (!r.ok) {
      workStatus("не удалось удалить", true);
      return;
    }
    await loadWork();
    workStatus("Запись удалена из «в работе»");
  } catch (err) {
    workStatus("не удалось удалить: " + (err.message || err), true);
  }
}

export { renderWork };

$("#work-rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  if (e.target.closest("button")) return;
  openDetail(Number(tr.dataset.id));
});
$("#work-by-url").addEventListener("click", addWorkByUrl);
$("#work-refresh").addEventListener("click", () => {
  loadWork();
});
$("#work-url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") addWorkByUrl();
});
