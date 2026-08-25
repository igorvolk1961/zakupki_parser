"use strict";

// Вкладка «Логи» (devops): хвост файла лога с автообновлением, текстовым
// поиском, фильтром по уровню (ошибки/предупреждения) и диапазону дат.
import { $, escapeHtml } from "./utils.js";
import { api } from "./api.js";

let logsTimer = null;

function logLevel(line) {
  const head = line.slice(0, 40);
  if (/\b(ERROR|CRITICAL)\b/.test(head)) return "error";
  if (/\bWARNING\b/.test(head)) return "warning";
  return "";
}

async function loadLogs() {
  const params = {
    lines: $("#logs-lines").value,
    level: $("#logs-level").value,
    q: $("#logs-q").value.trim() || undefined,
  };
  const fromVal = $("#logs-from").value;
  const toVal = $("#logs-to").value;
  if (fromVal) params.from = new Date(fromVal).toISOString();
  if (toVal) params.to = new Date(toVal).toISOString();

  const data = await api("logs/tail", params);
  const view = $("#logs-view");
  const lines = data.lines || [];
  view.innerHTML = "";
  if (!data.file_exists) {
    view.innerHTML = `<div class="muted">файл лога не найден${data.path ? ": " + escapeHtml(data.path) : ""}</div>`;
  } else if (!lines.length) {
    view.innerHTML = '<div class="muted">строк, соответствующих фильтру, нет</div>';
  } else {
    lines.forEach((l) => {
      const span = document.createElement("span");
      span.className = "log-line " + logLevel(l);
      span.textContent = l;
      view.appendChild(span);
    });
  }
  $("#logs-status").textContent = data.file_exists
    ? `${data.path} · строк: ${data.count}${data.truncated ? " (обрезано)" : ""}`
    : "файл лога не найден";
}

function setupAutoRefresh() {
  if (logsTimer) {
    clearInterval(logsTimer);
    logsTimer = null;
  }
  if ($("#logs-auto").checked) {
    logsTimer = setInterval(async () => {
      // Обновляем только пока вкладка открыта.
      const view = document.getElementById("view-logs");
      if (!view || view.style.display === "none") return;
      try {
        await loadLogs();
      } catch (e) {
        /* сервис недоступен — повторим на следующем тике */
      }
    }, 5000);
  }
}

export { loadLogs };

$("#logs-refresh").addEventListener("click", loadLogs);
$("#logs-auto").addEventListener("change", setupAutoRefresh);
// Автообновление включено по умолчанию (чекбокс отмечен в HTML) — запускаем сразу.
setupAutoRefresh();
$("#logs-q").addEventListener("input", () => {
  if (!$("#logs-auto").checked) return;
  clearTimeout(window.__logsDebounce);
  window.__logsDebounce = setTimeout(loadLogs, 500);
});
["#logs-level", "#logs-from", "#logs-to", "#logs-lines"].forEach((sel) => {
  document.querySelector(sel).addEventListener("change", loadLogs);
});
