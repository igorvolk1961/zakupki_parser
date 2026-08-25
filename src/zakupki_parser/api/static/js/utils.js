"use strict";

// Общие чистые хелперы UI: селекторы, форматирование, экранирование.

export const $ = (s) => document.querySelector(s);

export const fmtMoney = (v) =>
  v == null ? "–" : new Intl.NumberFormat("ru-RU").format(v) + " ₽";

export const fmtDate = (v) => (v ? new Date(v).toLocaleString("ru-RU") : "–");

// Дата и время без секунд.
export const fmtDT = (v) =>
  v
    ? new Date(v).toLocaleString("ru-RU", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "–";

// Только дата (для таблицы закупок).
export const fmtDateOnly = (v) => (v ? new Date(v).toLocaleDateString("ru-RU") : "–");

// Фит-скор считается только после обработки внешним скорингом
// (score_method=fit/pwin/margin/sim); до этого (дефолтный/просроченный)
// возвращаем null → показываем прочерк. sim — предварительная фильтрация
// по векторной близости: LLM не выполнялся, fit_score=0 (ADR-8).
export const realFit = (row) =>
  ["fit", "pwin", "margin", "sim", "manual", "reject"].includes(row.score_method)
    ? row.fit_score
    : null;

// Значение fit-скора с пометкой метода скоринга (кроме обычной стадии fit):
// «0 (sim)» — отсечка по векторной близости, «0.5 (pwin)» — стадия каскада.
export function fitCell(row) {
  const v = realFit(row);
  if (v == null) return "—";
  const method =
    row.score_method && row.score_method !== "fit" ? escapeHtml(row.score_method) : "";
  return method ? `${v} <span class="muted" style="font-size:11px">(${method})</span>` : String(v);
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

export function splitWords(text) {
  return text.split(",").map((s) => s.trim()).filter(Boolean);
}
