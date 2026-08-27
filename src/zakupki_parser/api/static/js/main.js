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
  viewTz,
  closeTz,
} from "./procurements.js";
import { loadCustomers } from "./customers.js";
import { loadProfiles, loadActiveClient, closeDeleteProfileModal, profileFormDirty } from "./clients.js";
import { loadMonitor, loadPromptList, monitorDirty, promptDirty } from "./config.js";
import {
  loadScoreopsConfig,
  loadOpsConfig,
  loadLogConfig,
  loadParserConfig,
  scoreopsDirty,
  opsDirty,
  logDirty,
  parserDirty,
} from "./ops_config.js";
import { loadUsers, closeUserModal } from "./users.js";
import { loadLogs } from "./logs.js";
import { updateControls, refreshParserStatus, closeDbModal, closeExportModal } from "./admin.js";
import { closeConfirmDialog } from "./dialogs.js";
import { loadRefTables, refDirty } from "./reference.js";
import { ALL_TABS, switchTo, updateRolesUI } from "./roles.js";

// --- Переключение верхних вкладок --------------------------------------
// При уходе с формы редактирования профиля с несохранёнными изменениями
// предупреждаем: «Отмена» — confirmDialog ниже, закрытие страницы — beforeunload.
const TAB_LOADERS = {
  proc: null,
  cust: loadCustomers,
  profiles: loadProfiles,
  users: loadUsers,
  monitor: () => {
    if (!monitorDirty) loadMonitor();
  },
  prompts: () => {
    if (!promptDirty) loadPromptList();
  },
  refs: loadRefTables,
  scoreops: () => {
    if (!scoreopsDirty) loadScoreopsConfig();
  },
  cfgops: () => {
    if (!opsDirty) loadOpsConfig();
  },
  logcfg: () => {
    if (!logDirty) loadLogConfig();
  },
  logs: loadLogs,
  parser: () => {
    if (!parserDirty) loadParserConfig();
  },
};

ALL_TABS.forEach((t) => {
  const btn = $("#tab-" + t);
  if (!btn) return;
  btn.addEventListener("click", () => {
    switchTo(t);
    const loader = TAB_LOADERS[t];
    if (loader) loader();
  });
});

window.addEventListener("beforeunload", (e) => {
  if (
    monitorDirty ||
    scoreopsDirty ||
    opsDirty ||
    logDirty ||
    parserDirty ||
    promptDirty ||
    profileFormDirty() ||
    refDirty()
  ) {
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
window.closeUserModal = closeUserModal;
window.analyzeProc = analyzeProc;
window.pwinProc = pwinProc;
window.viewTz = viewTz;
window.closeTz = closeTz;

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeDbModal();
    closeExportModal();
    closeDeleteProfileModal();
    closeUserModal();
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
  updateRolesUI();
  // Состояние переключателя «Только релевантные» и числового поля порога.
  $("#proc-relevant").checked = localStorage.getItem("zp_relevant") === "1";
  updateMinFit();
  // При включённой авторизации без входа не загружаем данные (ждём логин) —
  // WebSocket подключится после успешного входа (см. doLogin).
  const authActive = await checkAuth();
  updateRolesUI();
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
