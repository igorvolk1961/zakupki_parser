"use strict";

// Панель администратора: статус/управление парсером, очистка БД, выгрузка CSV.
import { $, fmtDT } from "./utils.js";
import { state } from "./store.js";
import { api, apiJSON, authToken } from "./api.js";
import { loadProc, renderProc } from "./procurements.js";
import { loadCustomers } from "./customers.js";

let parserTimer = null;
let prevRunning = false;
let stopping = false;

function updateControls() {
  $("#parser-start").disabled = state.parserRunning;
  $("#parser-stop").disabled = !state.parserRunning || stopping;
  // Элементы администратора видны автоматически для роли admin (или в dev-режиме
  // без авторизации — всем). Роль регистрацией не выдаётся; задаётся env-сидом
  // администратора или в таблице БД.
  const adminOn = !state.authUser || state.authUser.role === "admin";
  $("#db-clear").style.display = adminOn ? "" : "none";
  $("#db-clear").disabled = state.parserRunning;
  $("#db-export").style.display = adminOn ? "" : "none";
  $("#tab-cfg").style.display = adminOn ? "" : "none";
  $("#tab-prompts").style.display = adminOn ? "" : "none";
}

async function refreshParserStatus() {
  // До входа (при включённой авторизации) статус не запрашиваем — иначе лишний 401.
  if (state.authRequired && !authToken()) return;
  let s;
  try {
    s = await api("parser/status");
  } catch {
    return;
  }
  state.parserRunning = s.running;
  if (!s.running) stopping = false;
  updateControls();
  const el = $("#parser-status");
  if (s.running) {
    el.textContent = "работает…" + (s.started_at ? " (с " + fmtDT(s.started_at) + ")" : "");
  } else {
    let txt = s.stopped ? "остановлен" : "не запущен";
    if (s.error) txt += " · ошибка: " + s.error;
    if (s.finished_at) txt += " · завершён " + fmtDT(s.finished_at);
    el.textContent = txt;
  }
  return s;
}

function pollParser() {
  if (parserTimer) clearInterval(parserTimer);
  parserTimer = setInterval(async () => {
    const s = await refreshParserStatus();
    if (s.running) {
      prevRunning = true;
      return;
    }
    if (prevRunning) {
      // Проход завершился — обновляем список (без сброса выбора).
      prevRunning = false;
      clearInterval(parserTimer);
      parserTimer = null;
      await loadProc();
    } else {
      clearInterval(parserTimer);
      parserTimer = null;
    }
  }, 3000);
}

function closeDbModal() {
  $("#db-modal-bg").classList.remove("open");
}

async function dbClearRequest(url, body) {
  closeDbModal();
  $("#parser-status").textContent = "очистка БД…";
  const opts = { method: "POST" };
  if (body) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const r = await apiJSON(url, opts);
  if (!r.ok) {
    $("#parser-status").textContent = "не удалось очистить БД (возможно, идёт сбор)";
    return;
  }
  const res = await r.json();
  await loadProc();
  await loadCustomers();
  const d = res.deleted;
  const cnt =
    typeof d === "number"
      ? d
      : d && typeof d === "object"
        ? `закупок ${d.procurements ?? 0}, заказчиков ${d.customers ?? 0}`
        : "";
  $("#parser-status").textContent = "БД очищена" + (cnt !== "" ? ` (удалено: ${cnt})` : "");
}

function stepDbFit(d) {
  const input = $("#db-min-fit");
  let v = parseFloat(input.value);
  if (isNaN(v)) v = 0.4;
  v = Math.min(0.9, Math.max(0, Math.round((v + d) * 10) / 10));
  input.value = v;
}

function closeExportModal() {
  $("#export-modal-bg").classList.remove("open");
}

function stepExportFit(d) {
  const input = $("#export-min-fit");
  let v = parseFloat(input.value);
  if (isNaN(v)) v = 0.4;
  v = Math.min(0.9, Math.max(0, Math.round((v + d) * 10) / 10));
  input.value = v;
}

export { updateControls, refreshParserStatus, pollParser, closeDbModal, closeExportModal };

$("#parser-start").addEventListener("click", async () => {
  $("#parser-status").textContent = "запуск…";
  stopping = false;
  state.parserRunning = true;
  updateControls();
  renderProc(); // мгновенно блокируем «Запустить» и убираем подсказку
  const r = await apiJSON("/api/parser/start", { method: "POST" });
  if (!r.ok) {
    state.parserRunning = false;
    updateControls();
    renderProc();
    $("#parser-status").textContent = "не удалось запустить (возможно, уже работает)";
    return;
  }
  await refreshParserStatus();
  pollParser();
});
$("#parser-stop").addEventListener("click", async () => {
  $("#parser-status").textContent = "остановка…";
  stopping = true;
  state.parserRunning = false;
  updateControls(); // мгновенно блокируем «Остановить» до фактической остановки
  await apiJSON("/api/parser/stop", { method: "POST" });
  await refreshParserStatus();
  pollParser();
});
$("#db-clear").addEventListener("click", () => {
  // Порог релевантности в диалоге — как в фильтре таблицы закупок.
  $("#db-min-fit").value = $("#proc-min-fit").value;
  $("#db-modal-bg").classList.add("open");
});
$("#db-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "db-modal-bg") closeDbModal();
});
$("#db-clear-cancel").addEventListener("click", closeDbModal);
$("#db-clear-confirm").addEventListener("click", () => {
  const mode = document.querySelector('input[name="db-clear-mode"]:checked').value;
  if (mode === "all") dbClearRequest("/api/db/clear");
  else if (mode === "inactive") dbClearRequest("/api/db/clear-inactive");
  else dbClearRequest("/api/db/clear-irrelevant", { min_fit_score: parseFloat($("#db-min-fit").value) });
});
$("#db-fit-up").addEventListener("click", () => stepDbFit(0.1));
$("#db-fit-dn").addEventListener("click", () => stepDbFit(-0.1));
$("#db-export").addEventListener("click", () => {
  // Порог в диалоге — как в фильтре таблицы закупок.
  $("#export-min-fit").value = $("#proc-min-fit").value;
  $("#export-modal-bg").classList.add("open");
});
$("#export-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "export-modal-bg") closeExportModal();
});
$("#export-cancel").addEventListener("click", closeExportModal);
$("#export-fit-up").addEventListener("click", () => stepExportFit(0.1));
$("#export-fit-dn").addEventListener("click", () => stepExportFit(-0.1));
$("#export-confirm").addEventListener("click", async () => {
  closeExportModal();
  $("#parser-status").textContent = "выгрузка CSV…";
  const r = await apiJSON("/api/procurements/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ min_fit_score: parseFloat($("#export-min-fit").value) }),
  });
  if (!r.ok) {
    $("#parser-status").textContent = "не удалось выгрузить CSV";
    return;
  }
  const body = await r.json();
  $("#parser-status").textContent = `CSV выгружен на сервер: ${body.path} (закупок: ${body.count})`;
});
