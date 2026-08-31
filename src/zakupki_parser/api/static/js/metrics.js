"use strict";

// Вкладка «Метрики» (только для роли analyst): журнал затрат по циклам
// скоринга/анализа, сводная статистика метрик скоринга (раздельно LLM и
// эмбеддинги) и расходы на токены по датам. Все значения приходят с бэкенда
// (GET /api/metrics), агрегирует их сервер — клиент только отображает.
import { $, escapeHtml, fmtDT, fmtDateOnly } from "./utils.js";
import { api } from "./api.js";

const usd = (v) => (v == null ? "—" : "$" + Number(v).toFixed(4));
const num = (v) => (v == null ? "—" : Number(v).toLocaleString("ru-RU"));
const avgMs = (v) => (v == null ? "—" : Math.round(Number(v)).toLocaleString("ru-RU") + " мс");

// Одна ячейка статистики: среднее и разброс (мин–макс ± ско).
function statCell(st) {
  if (!st || st.count === 0) return "—";
  const fmt = (v) => Math.round(Number(v) || 0).toLocaleString("ru-RU");
  const span = (v) => (v == null || Number(v) === 0 ? "—" : Math.round(Number(v)));
  return `${fmt(st.avg)} <span class="muted">(${span(st.min)}–${span(st.max)} ± ${span(st.std)})</span>`;
}

function moneyCell(st) {
  if (!st || st.count === 0) return "—";
  return `$${Number(st.avg).toFixed(4)} <span class="muted">(${Number(st.min).toFixed(4)}–${Number(st.max).toFixed(4)})</span>`;
}

function statsBlock(data) {
  const s = data.scoring_stats || {};
  const row = (label, v) => `<tr><td>${label}</td><td>${v}</td></tr>`;
  return `<div class="panel">
    <div class="panel-title">Скоринг: средние и разброс метрик</div>
    <table class="met">
      ${row("Циклов скоринга", s.count ?? "—")}
      ${row("Стоимость (USD), среднее", moneyCell(s.cost))}
      ${row("Токены (всего), среднее", statCell(s.tokens))}
      ${row("Токены LLM, среднее", statCell(s.llm && s.llm.tokens))}
      ${row("Токены эмбеддингов, среднее", statCell(s.embeddings && s.embeddings.tokens))}
      ${row("Латенси LLM", statCell(s.llm && s.llm.latency_ms))}
      ${row("Латенси эмбеддингов", statCell(s.embeddings && s.embeddings.latency_ms))}
      ${row("Время выполнения (duration_ms)", statCell(s.duration_ms))}
    </table>
  </div>`;
}

// Журнал батчей: строка — закупки, обработанные подряд до задержки повтора
// (recovery_ttl_seconds). Внутри батча суммируются стоимости, остальные метрики
// (токены, латенси, время) — средние по закупкам батча.
function batchesBlock(batches) {
  const avg = (v) => (v == null ? "—" : Number(v).toLocaleString("ru-RU"));
  const rows = (batches || [])
    .map((b) => `<tr>
      <td>${fmtDT(b.started_at)}</td>
      <td>${b.iteration == null ? "—" : b.iteration}</td>
      <td>${escapeHtml(b.platform || "—")}</td>
      <td>${num(b.count)}</td>
      <td>${usd(b.cost_scoring)}</td>
      <td>${usd(b.cost_analysis)}</td>
      <td><b>${usd(b.cost_total)}</b></td>
      <td>${avg(b.llm ? b.llm.tokens : "—")}</td>
      <td>${avg(b.embeddings ? b.embeddings.tokens : "—")}</td>
      <td>${avgMs(b.llm ? b.llm.latency_ms : "—")}</td>
      <td>${avgMs(b.embeddings ? b.embeddings.latency_ms : "—")}</td>
      <td>${avgMs(b.duration_ms)}</td>
    </tr>`)
    .join("");
  return `<div class="panel">
    <div class="panel-title">Батчи обработки (журнал) · всего ${num(batches ? batches.length : 0)}</div>
    <div class="table-wrap" style="max-height:420px; overflow:auto;">
      <table class="met">
        <thead><tr>
          <th>Начало</th><th>Итер.</th><th>Площадка</th><th>Закупок</th>
          <th>Скоринг $ (сумма)</th><th>Анализ $ (сумма)</th><th>Всего $ (сумма)</th>
          <th>Токены LLM (сред.)</th><th>Токены Эмбеддинги (сред.)</th><th>Латенси LLM (сред.)</th><th>Латенси Эмбеддинги (сред.)</th><th>Время (сред., мс)</th>
        </tr></thead>
        <tbody>${rows || '<tr><td colspan="12" class="muted">Батчей с метриками пока нет.</td></tr>'}</tbody>
      </table>
    </div>
  </div>`;
}

function byDateBlock(byDate) {
  const rows = (byDate || [])
    .map((d) => `<tr>
      <td>${fmtDateOnly(d.date)}</td>
      <td>${usd(d.scoring_usd)}</td>
      <td>${usd(d.analysis_usd)}</td>
      <td><b>${usd(d.total_usd)}</b></td>
      <td>${num(d.scoring_tokens)}</td>
      <td>${num(d.analysis_tokens)}</td>
      <td>${num(d.total_tokens)}</td>
    </tr>`)
    .join("");
  return `<div class="panel">
    <div class="panel-title">Расходы на токены по датам (скоринг + анализ)</div>
    <div class="table-wrap" style="max-height:420px; overflow:auto;">
      <table class="met">
        <thead><tr>
          <th>Дата</th><th>Скоринг $</th><th>Анализ $</th><th>Всего $</th>
          <th>Токены скоринг</th><th>Токены анализ</th><th>Всего токенов</th>
        </tr></thead>
        <tbody>${rows || '<tr><td colspan="7" class="muted">Данных по датам пока нет.</td></tr>'}</tbody>
      </table>
    </div>
  </div>`;
}

function renderMetrics(data) {
  $("#metrics-content").innerHTML = `
    <div class="toolbar" style="margin:0;">
      <button class="primary" id="metrics-refresh">Обновить</button>
      <span class="muted">Метрики обработки закупок (доступны только аналитику).</span>
    </div>
    ${statsBlock(data)}
    ${batchesBlock(data.batches)}
    ${byDateBlock(data.by_date)}`;
  $("#metrics-refresh").addEventListener("click", loadMetrics);
}

export async function loadMetrics() {
  try {
    const data = await api("metrics");
    renderMetrics(data);
  } catch (err) {
    $("#metrics-content").innerHTML = `<div class="empty">Не удалось загрузить метрики: ${escapeHtml(String(err))}</div>`;
  }
}
