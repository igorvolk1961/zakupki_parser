"use strict";

// Вкладки devops: «Сервисы» (конфиг + секреты .env каждого фонового сервиса),
// «Конфигурация» (config_ops.yaml), «Управление логами» (config_log.yaml)
// и «Парсер» (config_parser.yaml) — форма + текстовый режим.
import { api, apiJSON } from "./api.js";
import { createConfigView } from "./config_view.js";

export let opsDirty = false;
export let logDirty = false;
export let parserDirty = false;

const opsView = createConfigView("cfgops", "config/ops", {
  onDirty: (v) => {
    opsDirty = v;
  },
});
const logView = createConfigView("logcfg", "config/log", {
  onDirty: (v) => {
    logDirty = v;
  },
});
const parserView = createConfigView("parser", "config/parser", {
  onDirty: (v) => {
    parserDirty = v;
  },
});

// --- Вкладка «Сервисы»: конфиг (форма + Текстовый режим) + секреты .env ---
// Для каждого сервиса — контроллер формы по образцу инфраструктурных вкладок.
// Секреты (.env) редактируются в отдельном модальном окне.
const SERVICES = [
  {
    key: "scoring",
    label: "Скоринг",
    configPath: "services/scoring/config",
    schemaPath: "services/scoring",
  },
  {
    key: "analysis",
    label: "Анализ ТЗ",
    configPath: "services/analysis/config",
    schemaPath: "services/analysis",
  },
  {
    key: "pwin",
    label: "P(win)",
    configPath: "services/pwin/config",
    schemaPath: "services/pwin",
  },
  {
    key: "margin",
    label: "Margin",
    configPath: "services/margin/config",
    schemaPath: "services/margin",
  },
];

// Модальное окно секретов (.env): закрывается и при «Сохранить», и при «Отмена».
const envModal = {
  service: null,

  async open(service) {
    this.service = service;
    const title = document.getElementById("env-modal-title");
    if (title) title.textContent = "Секреты (" + service.label + ")";
    const status = document.getElementById("env-modal-status");
    if (status) status.textContent = "";
    const area = document.getElementById("env-modal-area");
    try {
      const res = await api(service.schemaPath + "/env");
      area.value = res.content;
    } catch (err) {
      area.value = "";
      if (status) status.textContent = "Ошибка: " + err.message;
    }
    document.getElementById("env-modal-bg").classList.add("open");
  },

  close() {
    this.service = null;
    document.getElementById("env-modal-bg").classList.remove("open");
  },

  async save() {
    if (!this.service) return;
    const status = document.getElementById("env-modal-status");
    if (status) status.textContent = "Сохранение…";
    try {
      const r = await apiJSON("/api/" + this.service.schemaPath + "/env", {
        method: "PUT",
        headers: { "Content-Type": "text/plain" },
        body: document.getElementById("env-modal-area").value,
      });
      if (r.status === 401) return;
      if (!r.ok) {
        let msg = "не удалось сохранить";
        try {
          const d = await r.json();
          msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
        } catch (e) {}
        if (status) status.textContent = "Ошибка: " + msg;
        return;
      }
      this.close();
    } catch (err) {
      if (status) status.textContent = "Ошибка: " + err.message;
    }
  },
};

export function closeEnvModal() {
  envModal.close();
}
window.closeEnvModal = closeEnvModal;

// Привязка модалки секретов.
const envSaveBtn = document.getElementById("env-modal-save");
const envCancelBtn = document.getElementById("env-modal-cancel");
const envBg = document.getElementById("env-modal-bg");
if (envSaveBtn) envSaveBtn.addEventListener("click", () => envModal.save());
if (envCancelBtn) envCancelBtn.addEventListener("click", () => envModal.close());
if (envBg) {
  envBg.addEventListener("click", (e) => {
    if (e.target.id === "env-modal-bg") envModal.close();
  });
}

// Состояние каждого сервиса: контроллер формы.
const serviceUIs = SERVICES.map((s) => {
  let viewDirty = false;
  const view = createConfigView("svc-" + s.key, s.configPath, {
    schemaPath: s.schemaPath,
    onDirty: (v) => {
      viewDirty = v;
    },
  });
  const envToggle = document.getElementById("svc-" + s.key + "-env-toggle");
  if (envToggle) envToggle.addEventListener("click", () => envModal.open(s));
  return {
    ...s,
    view,
    isDirty: () => viewDirty,
  };
});

export function servicesDirty() {
  return serviceUIs.some((s) => s.isDirty());
}

let subTabsBound = false;

export async function loadServicesConfig() {
  // Переключение под-вкладок: клик по сервису (привязываем один раз).
  if (!subTabsBound) {
    serviceUIs.forEach((s) => {
      const tabBtn = document.getElementById("svc-tab-" + s.key);
      if (!tabBtn) return;
      tabBtn.addEventListener("click", () => activateService(s.key));
    });
    subTabsBound = true;
  }
  // Первичная загрузка активной под-вкладки (Скоринг).
  return activateService("scoring", true);
}

async function activateService(key, forceReload = false) {
  serviceUIs.forEach((s) => {
    const active = s.key === key;
    const tabBtn = document.getElementById("svc-tab-" + s.key);
    if (tabBtn) tabBtn.classList.toggle("active", active);
    const pane = document.getElementById("svc-pane-" + s.key);
    if (pane) pane.style.display = active ? "block" : "none";
  });
  const target = serviceUIs.find((s) => s.key === key);
  if (!target) return;
  if (forceReload || !target._loaded) {
    if (!target.isDirty()) await target.view.load();
    target._loaded = true;
  }
}

export function loadOpsConfig() {
  return opsView.load();
}

export function loadLogConfig() {
  return logView.load();
}

export function loadParserConfig() {
  return parserView.load();
}
