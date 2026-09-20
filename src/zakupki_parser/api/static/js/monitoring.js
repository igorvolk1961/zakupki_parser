"use strict";

// Вкладка devops «Мониторинг»: очереди каскада скоринга, наполнение фонового
// индекса по ОКПД2, ресурсы хоста. Автообновление раз в 10с, пока вкладка
// открыта (по образцу авто-обновления «Логов», см. logs.js).
import { $, escapeHtml, fmtDT } from "./utils.js";
import { api, apiJSON } from "./api.js";
import { createConfigView } from "./config_view.js";

const STAGE_LABELS = {
  fit: "Fit",
  pwin: "P(win)",
  margin: "Margin",
  analysis: "Анализ ТЗ",
  index: "Индексация",
};

let monitoringTimer = null;
let monitoringTimerReady = false;
let indexingConfigDirty = false;
let indexingConfigLoaded = false;

// Интервал автообновления вкладки «Мониторинг» — настраивается селектом
// #mon-refresh-interval (5/10/30/60с или «выкл»), сохраняется в localStorage
// этого браузера. По умолчанию — прежние 10с.
const MON_REFRESH_STORAGE_KEY = "mon-refresh-interval-ms";
const MON_REFRESH_DEFAULT_MS = 10000;

function readStoredRefreshMs() {
  try {
    const raw = localStorage.getItem(MON_REFRESH_STORAGE_KEY);
    const n = raw == null ? NaN : Number(raw);
    return Number.isFinite(n) && n >= 0 ? n : MON_REFRESH_DEFAULT_MS;
  } catch {
    return MON_REFRESH_DEFAULT_MS;
  }
}

function restartMonitoringTimer() {
  if (monitoringTimer) {
    clearInterval(monitoringTimer);
    monitoringTimer = null;
  }
  const sel = $("#mon-refresh-interval");
  const ms = sel ? Number(sel.value) : MON_REFRESH_DEFAULT_MS;
  if (!ms) return; // «выкл» — обновление только при заходе на вкладку/вручную
  monitoringTimer = setInterval(() => {
    const v = document.getElementById("view-monitoring");
    if (!v || v.style.display === "none") return;
    loadMonitoring();
  }, ms);
}

// Статистика по площадкам: своё состояние (поиск/пагинация), НЕ на общем
// 10с-таймере — при сотнях площадок пере-рендер по таймеру сбрасывал бы
// прокрутку/фокус пользователя, пока он ищет/листает. Грузится один раз при
// первой активации вкладки и по явному действию (поиск/чекбокс/пагинация/
// кнопка «Обновить»), как и indexingConfigView выше.
const PLATFORM_STATS_LIMIT = 50;
let platformStatsOffset = 0;
let platformStatsTotal = 0;
let platformStatsLoaded = false;

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

function renderDeadLetter(entries) {
  if (!entries || !entries.length) {
    return `<div class="muted">Пусто — сбойных записей, исчерпавших повторы, нет</div>`;
  }
  const rows = entries
    .map(
      (e) => `<tr>
        <td>${escapeHtml(e.number || String(e.procurement_id))}</td>
        <td>${escapeHtml(e.subject || "")}</td>
        <td>${e.attempts}</td>
        <td>${escapeHtml(e.error_message || "")}</td>
        <td>${escapeHtml(fmtDT(e.updated_at))}</td>
        <td><button data-dlq-retry="${e.procurement_id}" title="Сбросить попытки и поставить закупку в очередь индексации заново">Повторить</button></td>
      </tr>`
    )
    .join("");
  return `
    <table>
      <thead><tr><th>Закупка</th><th>Тема</th><th>Попыток</th><th>Ошибка</th><th>Когда</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function retryDeadLetterEntry(procurementId) {
  await apiJSON(`/api/devops/index-dead-letter/${procurementId}/retry`, { method: "POST" });
  await loadMonitoring();
}

function fmtDuration(seconds) {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s} с`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m} мин ${rem} с`;
}

function renderCycles(cycles) {
  const last = cycles && cycles.last;
  const avg = cycles && cycles.average;
  if (!last && !avg) {
    return `<div class="muted">Ещё не завершился ни один цикл обхода</div>`;
  }
  const row = (label, l, a, fmt) => {
    const f = fmt || ((v) => v);
    return `<tr><td>${escapeHtml(label)}</td><td>${last ? f(l) : "—"}</td><td>${avg ? f(a) : "—"}</td></tr>`;
  };
  const round1 = (v) => (typeof v === "number" ? v.toFixed(1) : v);
  return `
    <table>
      <thead><tr><th></th><th>Последний цикл</th><th>В среднем${avg ? ` (по ${avg.sample_size})` : ""}</th></tr></thead>
      <tbody>
        ${row("Закупок получено", last && last.received, avg && avg.received, round1)}
        ${row("Закупок сохранено в БД", last && last.saved, avg && avg.saved, round1)}
        ${row("Сбоев обращения к площадкам", last && last.platforms_failed, avg && avg.platforms_failed, round1)}
        ${row("Длительность цикла", last && last.duration_seconds, avg && avg.duration_seconds, fmtDuration)}
      </tbody>
    </table>
    ${last ? `<div class="muted">Последний цикл: ${escapeHtml(fmtDT(last.started_at))} — ${escapeHtml(fmtDT(last.finished_at))} (${last.platforms_total} площадок)</div>` : ""}`;
}

function renderStorage(storage) {
  if (!storage) return `<div class="muted">нет данных</div>`;
  return `
    <div>Файловое хранилище (data/): ${fmtBytes(storage.file_storage_bytes)}</div>
    <div>База данных: ${storage.db_bytes == null ? "—" : fmtBytes(storage.db_bytes)}</div>`;
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
    <div>Диск: ${fmtBytes(disk.used)} / ${fmtBytes(disk.total)} (${disk.percent?.toFixed(1) ?? "—"}%)</div>
    ${renderProcesses(res.processes)}`;
}

function renderProcesses(items) {
  if (!items || !items.length) return "";
  const totalCpu = items.reduce((s, p) => s + (p.cpu_percent || 0), 0);
  const totalRss = items.reduce((s, p) => s + (p.rss_bytes || 0), 0);
  const totalRssPct = items.reduce((s, p) => s + (p.rss_percent || 0), 0);
  const rows = items
    .map(
      (p) => `<tr>
        <td>${escapeHtml(p.label)}</td>
        <td class="muted">${p.pid}</td>
        <td>${p.cpu_percent.toFixed(1)}%</td>
        <td>${fmtBytes(p.rss_bytes)} (${p.rss_percent.toFixed(1)}%)</td>
      </tr>`
    )
    .join("");
  return `
    <div class="panel-title" style="margin-top:12px;" title="Доля CPU/RAM с момента предыдущего опроса (как и общий CPU выше), не мгновенный снимок. Виден только сам процесс api и его дочерние (например, браузер парсера); отдельные воркеры каскада (scoring_service/analysis_service/...) видны только при локальном запуске run_all.sh — в docker-стеке у каждого свой контейнер, процессы других контейнеров отсюда не видны.">Процессы программы</div>
    <table>
      <thead><tr><th>Процесс</th><th>PID</th><th>CPU</th><th>Память</th></tr></thead>
      <tbody>${rows}</tbody>
      <tfoot><tr>
        <td colspan="2"><b>Итого</b></td>
        <td><b>${totalCpu.toFixed(1)}%</b></td>
        <td><b>${fmtBytes(totalRss)} (${totalRssPct.toFixed(1)}%)</b></td>
      </tr></tfoot>
    </table>`;
}

function renderPlatformStats(items) {
  if (!items || !items.length) {
    return `<div class="muted">Нет данных</div>`;
  }
  const rows = items
    .map((p) => {
      const status = p.last_success
        ? '<span style="color:var(--ok,#2e7d32);">OK</span>'
        : `<span style="color:var(--danger,#c0392b);" title="${escapeHtml(p.last_error || "")}">сбой</span>`;
      return `<tr>
        <td>${escapeHtml(p.platform_id)}</td>
        <td>${status}</td>
        <td>${escapeHtml(fmtDT(p.last_finished_at))}</td>
        <td>${p.last_received} / ${p.last_saved}</td>
        <td>${p.avg_received.toFixed(1)} / ${p.avg_saved.toFixed(1)}</td>
        <td>${fmtDuration(p.avg_duration_seconds)}</td>
        <td>${p.runs_total} (сбоев: ${p.runs_failed})</td>
      </tr>`;
    })
    .join("");
  return `
    <table>
      <thead><tr>
        <th>Площадка</th><th>Статус</th><th>Последний обход</th>
        <th>Получено/сохранено (последний)</th><th>В среднем получено/сохранено</th>
        <th>Средняя длительность</th><th>Обходов всего</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function loadPlatformStats() {
  const view = document.getElementById("view-monitoring");
  if (!view) return;
  try {
    const search = $("#mon-platform-search")?.value.trim() || undefined;
    const onlyFailed = $("#mon-platform-only-failed")?.checked || undefined;
    const data = await api("devops/platform-stats", {
      search,
      only_failed: onlyFailed,
      limit: PLATFORM_STATS_LIMIT,
      offset: platformStatsOffset,
    });
    platformStatsTotal = data.total || 0;
    $("#mon-platform-stats").innerHTML = renderPlatformStats(data.items);
    const from = platformStatsTotal ? platformStatsOffset + 1 : 0;
    const to = Math.min(platformStatsOffset + PLATFORM_STATS_LIMIT, platformStatsTotal);
    $("#mon-platform-summary").textContent = `${platformStatsTotal} площадок`;
    $("#mon-platform-page").textContent = `${from}–${to} из ${platformStatsTotal}`;
    $("#mon-platform-prev").disabled = platformStatsOffset <= 0;
    $("#mon-platform-next").disabled = platformStatsOffset + PLATFORM_STATS_LIMIT >= platformStatsTotal;
  } catch (e) {
    const msg = e && e.message ? e.message : String(e);
    $("#mon-platform-stats").innerHTML = `<div class="muted">ошибка загрузки: ${escapeHtml(msg)}</div>`;
  }
}

async function loadMonitoring() {
  const view = document.getElementById("view-monitoring");
  if (!view) return;
  try {
    const data = await api("devops/monitoring");
    $("#mon-error").textContent = "";
    $("#mon-queues").innerHTML = renderQueues(data.queues);
    $("#mon-index").innerHTML = renderIndex(data.index);
    $("#mon-cycles").innerHTML = renderCycles(data.cycles);
    $("#mon-storage").innerHTML = renderStorage(data.storage);
    $("#mon-resources").innerHTML = renderResources(data.resources);
    const dlq = await api("devops/index-dead-letter");
    $("#mon-dlq").innerHTML = renderDeadLetter(dlq.entries);
  } catch (e) {
    const msg = e && e.message ? e.message : String(e);
    $("#mon-error").textContent = `ошибка загрузки: ${msg}`;
  }
  if (!indexingConfigLoaded) {
    await indexingConfigView.load();
    indexingConfigLoaded = true;
  }
  if (!platformStatsLoaded) {
    await loadPlatformStats();
    platformStatsLoaded = true;
  }
  if (!monitoringTimerReady) {
    monitoringTimerReady = true;
    restartMonitoringTimer();
  }
}

// Делегирование клика с #mon-dlq (контейнер стабилен, содержимое перерисовывается
// каждые 10с — прямой listener на кнопке строки был бы потерян при пере-рендере).
document.getElementById("mon-dlq")?.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-dlq-retry]");
  if (!btn) return;
  btn.disabled = true;
  retryDeadLetterEntry(Number(btn.dataset.dlqRetry)).catch((err) => {
    $("#mon-error").textContent = `ошибка повтора: ${err && err.message ? err.message : err}`;
    btn.disabled = false;
  });
});

// Поиск/чекбокс/пагинация статистики по площадкам — только по явному действию
// пользователя (не на 10с-таймере, см. комментарий у platformStatsLoaded).
let platformSearchDebounce = null;
$("#mon-platform-search")?.addEventListener("input", () => {
  clearTimeout(platformSearchDebounce);
  platformSearchDebounce = setTimeout(() => {
    platformStatsOffset = 0;
    loadPlatformStats();
  }, 300);
});
$("#mon-platform-only-failed")?.addEventListener("change", () => {
  platformStatsOffset = 0;
  loadPlatformStats();
});
$("#mon-platform-refresh")?.addEventListener("click", () => loadPlatformStats());
$("#mon-platform-prev")?.addEventListener("click", () => {
  platformStatsOffset = Math.max(0, platformStatsOffset - PLATFORM_STATS_LIMIT);
  loadPlatformStats();
});
$("#mon-platform-next")?.addEventListener("click", () => {
  if (platformStatsOffset + PLATFORM_STATS_LIMIT < platformStatsTotal) {
    platformStatsOffset += PLATFORM_STATS_LIMIT;
    loadPlatformStats();
  }
});

const monRefreshSelect = $("#mon-refresh-interval");
if (monRefreshSelect) {
  monRefreshSelect.value = String(readStoredRefreshMs());
  monRefreshSelect.addEventListener("change", () => {
    try {
      localStorage.setItem(MON_REFRESH_STORAGE_KEY, monRefreshSelect.value);
    } catch {
      /* приватный режим/квота localStorage — не критично, просто не запомнится */
    }
    restartMonitoringTimer();
  });
}

export function monitoringDirty() {
  return indexingConfigDirty;
}

export { loadMonitoring };
