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
  viewTrace,
  setCardTab,
} from "./procurements.js";
import { loadCustomers } from "./customers.js";
import { loadProfiles, loadActiveClient, closeDeleteProfileModal, closeExportProfileModal, profileFormDirty } from "./clients.js";
import { loadMonitor, loadPromptList, monitorDirty, promptDirty } from "./config.js";
import {
  loadServicesConfig,
  loadOpsConfig,
  loadLogConfig,
  loadParserConfig,
  servicesDirty,
  opsDirty,
  logDirty,
  parserDirty,
  closeEnvModal,
} from "./ops_config.js";
import { loadUsers, closeUserModal } from "./users.js";
import { loadLogs, loadLogFiles } from "./logs.js";
import { updateControls, refreshParserStatus, closeDbModal, closeExportModal } from "./admin.js";
import { closeConfirmDialog } from "./dialogs.js";
import { loadRefTables, refDirty } from "./reference.js";
import { ALL_TABS, canAccessBase, switchTo, updateRolesUI } from "./roles.js";

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
  services: () => {
    if (!servicesDirty()) loadServicesConfig();
  },
  cfgops: () => {
    if (!opsDirty) loadOpsConfig();
  },
  logcfg: () => {
    if (!logDirty) loadLogConfig();
  },
  logs: () => {
    // Сначала загружаем список файлов логов, затем хвост выбранного
    // (loadLogFiles сам вызывает loadLogs после заполнения селектора).
    loadLogFiles();
  },
  parser: () => {
    if (!parserDirty) loadParserConfig();
  },
};

ALL_TABS.forEach((t) => {
  const btn = $("#tab-" + t);
  if (!btn) return;
  btn.addEventListener("click", () => switchTo(t));
});

// Активация вкладки = переключение + загрузка содержимого. Слушаем событие от
// switchTo: оно приходит и при клике по кнопке вкладки, и при программном
// автопереключении из updateRolesUI, поэтому загрузка происходит в обоих случаях.
document.addEventListener("tab:active", (e) => {
  const loader = TAB_LOADERS[e.detail.name];
  if (loader) loader();
});

window.addEventListener("beforeunload", (e) => {
  if (
    monitorDirty ||
    servicesDirty() ||
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
window.closeExportProfileModal = closeExportProfileModal;
window.closeUserModal = closeUserModal;
window.closeEnvModal = closeEnvModal;
window.analyzeProc = analyzeProc;
window.pwinProc = pwinProc;
window.viewTz = viewTz;
window.closeTz = closeTz;
window.viewTrace = viewTrace;
window.setCardTab = setCardTab;

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeDbModal();
    closeExportModal();
    closeDeleteProfileModal();
    closeExportProfileModal();
    closeUserModal();
    closeConfirmDialog();
    closeEnvModal();
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
  // Базовые вкладки грузим только аккаунтам с доступом (user/analyst).
  if (canAccessBase()) {
    try {
      await loadPlatforms();
      await loadProc();
      await loadCustomers();
      await loadActiveClient();
    } catch (err) {
      $("#proc-rows").innerHTML = `<tr><td colspan="4" class="muted">Не удалось загрузить данные: ${escapeHtml(String(err))}</td></tr>`;
    }
  }
})();
