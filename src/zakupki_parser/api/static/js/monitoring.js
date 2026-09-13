"use strict";

// Вкладка devops «Мониторинг»: очереди каскада скоринга, наполнение фонового
// индекса по ОКПД2, ресурсы хоста. Автообновление раз в 10с, пока вкладка
// открыта (по образцу авто-обновления «Логов», см. logs.js).
import { $, escapeHtml, fmtDT } from "./utils.js";
import { api } from "./api.js";
import { createConfigView } from "./config_view.js";

const STAGE_LABELS = {
  fit: "Fit",
  pwin: "P(win)",
  margin: "Margin",
  analysis: "Анализ ТЗ",
  index: "Индексация",
};

let monitoringTimer = null;
let indexingConfigDirty = false;
let indexingConfigLoaded = false;

// Настройки фоновой индексации (enabled/okpd2_prefixes/excluded_platforms) —
// редактируемая форма (не часть авто-обновляемой сводки выше: пере-рендер
// innerHTML каждые 10с стёр бы незасохранённый ввод). Грузится один раз при
// первой активации вкладки, как и остальные config-view панели приложения.
const indexingConfigView = createConfigView("mon-indexing", "devops/indexing-config", {
  onDirty: (v) => {
    indexingConfigDirty = v;
  },
});

function renderQueues(queues) {
  if (!queues || queues.available === false) {
    return `<div class="muted">Транспорт скоринга недоступен</div>`;
  }
  const rows = Object.entries(queues)
    .filter(([k]) => k !== "available")
    .map(
      ([stage, v]) =>
        `<tr><td>${escapeHtml(STAGE_LABELS[stage] || stage)}</td><td>${v.jobs}</td><td>${v.results}</td></tr>`
    )
    .join("");
  return `
    <table>
      <thead><tr><th>Стадия</th><th>В очереди</th><th>Непрочитанных результатов</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderIndex(index) {
  const counts = index.counts || {};
  const statusRows = Object.entries(counts)
    .filter(([k]) => k !== "total_procurements")
    .map(([status, n]) => `<tr><td>${escapeHtml(status)}</td><td>${n}</td></tr>`)
    .join("");
  const errors = index.recent_errors || [];
  const errorRows = errors
    .map(
      (e) =>
        `<tr><td>${escapeHtml(e.number || String(e.procurement_id))}</td><td>${escapeHtml(e.error_message || "")}</td><td>${fmtDT(e.updated_at)}</td></tr>`
    )
    .join("");
  return `
    <table>
      <thead><tr><th>Статус индекса</th><th>Закупок</th></tr></thead>
      <tbody>${statusRows || '<tr><td colspan="2" class="muted">нет данных</td></tr>'}</tbody>
    </table>
    <div class="muted">Всего закупок в БД: ${counts.total_procurements ?? "—"}</div>
    ${
      errors.length
        ? `<div class="panel-title" style="margin-top:12px;">Последние ошибки (${errors.length})</div>
    <table>
      <thead><tr><th>Закупка</th><th>Ошибка</th><th>Когда</th></tr></thead>
      <tbody>${errorRows}</tbody>
    </table>`
        : ""
    }`;
}

function fmtBytes(n) {
  if (n == null) return "—";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

function renderResources(res) {
  const mem = res.memory || {};
  const disk = res.disk || {};
  return `
    <div>CPU: ${res.cpu_percent?.toFixed(1) ?? "—"}%</div>
    <div>Память: ${fmtBytes(mem.used)} / ${fmtBytes(mem.total)} (${mem.percent?.toFixed(1) ?? "—"}%)</div>
    <div>Диск: ${fmtBytes(disk.used)} / ${fmtBytes(disk.total)} (${disk.percent?.toFixed(1) ?? "—"}%)</div>`;
}

async function loadMonitoring() {
  const view = document.getElementById("view-monitoring");
  if (!view) return;
  try {
    const data = await api("devops/monitoring");
    $("#mon-error").textContent = "";
    $("#mon-queues").innerHTML = renderQueues(data.queues);
    $("#mon-index").innerHTML = renderIndex(data.index);
    $("#mon-resources").innerHTML = renderResources(data.resources);
  } catch (e) {
    const msg = e && e.message ? e.message : String(e);
    $("#mon-error").textContent = `ошибка загрузки: ${msg}`;
  }
  if (!indexingConfigLoaded) {
    await indexingConfigView.load();
    indexingConfigLoaded = true;
  }
  if (!monitoringTimer) {
    monitoringTimer = setInterval(() => {
      const v = document.getElementById("view-monitoring");
      if (!v || v.style.display === "none") return;
      loadMonitoring();
    }, 10000);
  }
}

export function monitoringDirty() {
  return indexingConfigDirty;
}

export { loadMonitoring };
