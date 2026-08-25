"use strict";

// Точка входа web-приложения: переключение вкладок, инициализация,
// глобальные обработчики (Escape, beforeunload, тема) и привязка функций,
// вызываемых из inline onclick (модалки/действия в сгенерированном HTML).
import { $, escapeHtml } from "./utils.js";
import { state } from "./store.js";
import { checkAuth } from "./auth.js";
import { connectWS } from "./api.js";
import {
  updateMinFit,
  loadProc,
  loadPlatforms,
  closeModal,
  analyzeProc,
  pwinProc,
} from "./procurements.js";
import { loadCustomers } from "./customers.js";
import { loadProfiles, loadActiveClient, closeDeleteProfileModal, profileFormDirty } from "./clients.js";
import { loadConfig, loadPromptList, cfgDirty, promptDirty } from "./config.js";
import { updateControls, refreshParserStatus, closeDbModal, closeExportModal } from "./admin.js";
import { closeConfirmDialog } from "./dialogs.js";

// --- Переключение верхних вкладок --------------------------------------
// При уходе с формы редактирования профиля с несохранёнными изменениями
// предупреждаем: «Отмена» — confirmDialog ниже, закрытие страницы — beforeunload.
function switchTab(name) {
  ["proc", "cust", "cfg", "prompts", "profiles"].forEach((k) => {
    $("#tab-" + k).classList.toggle("active", k === name);
    $("#view-" + k).style.display = k === name ? "block" : "none";
  });
}
$("#tab-proc").addEventListener("click", () => switchTab("proc"));
$("#tab-cust").addEventListener("click", () => {
  switchTab("cust");
  loadCustomers();
});
$("#tab-cfg").addEventListener("click", () => {
  switchTab("cfg");
  if (!cfgDirty) loadConfig();
});
$("#tab-prompts").addEventListener("click", () => {
  switchTab("prompts");
  if (!promptDirty) loadPromptList();
});
$("#tab-profiles").addEventListener("click", () => {
  switchTab("profiles");
  loadProfiles();
});

window.addEventListener("beforeunload", (e) => {
  if (cfgDirty || promptDirty || profileFormDirty()) {
    e.preventDefault();
    e.returnValue = "";
  }
});

// Inline onclick в статическом HTML и в сгенерированных модалках ссылаются на
// глобальные функции — ES-модули не создают глобалов, поэтому привязываем явно.
window.closeModal = closeModal;
window.closeDbModal = closeDbModal;
window.closeExportModal = closeExportModal;
window.closeDeleteProfileModal = closeDeleteProfileModal;
window.analyzeProc = analyzeProc;
window.pwinProc = pwinProc;

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeDbModal();
    closeExportModal();
    closeDeleteProfileModal();
    closeConfirmDialog();
  }
});

// Цветовая схема (по умолчанию светлая; выбор запоминается в localStorage).
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("zp_theme", theme);
}
const themeSel = $("#theme");
const savedTheme = localStorage.getItem("zp_theme");
themeSel.value = savedTheme === "dark" ? "dark" : "light";
applyTheme(themeSel.value);
themeSel.addEventListener("change", () => applyTheme(themeSel.value));

(async function init() {
  updateControls();
  // Состояние переключателя «Только релевантные» и числового поля порога.
  $("#proc-relevant").checked = localStorage.getItem("zp_relevant") === "1";
  updateMinFit();
  // При включённой авторизации без входа не загружаем данные (ждём логин) —
  // WebSocket подключится после успешного входа (см. doLogin).
  const authActive = await checkAuth();
  if (authActive && !state.authUser) return;
  connectWS();
  refreshParserStatus();
  try {
    await loadPlatforms();
    await loadProc();
    await loadCustomers();
    await loadActiveClient();
  } catch (err) {
    $("#proc-rows").innerHTML = `<tr><td colspan="4" class="muted">Не удалось загрузить данные: ${escapeHtml(String(err))}</td></tr>`;
  }
})();
