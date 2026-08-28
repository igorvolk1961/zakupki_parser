"use strict";

// Общий контроллер вкладок конфигурации: форма по схеме (см. /api/config/*/schema)
// + «Текстовый режим» с сырым YAML, сохранение, статус и грязный флаг.
// Используется вкладками «Параметры мониторинга», «Конфигурация» и «Управление логами».
import { api, apiJSON } from "./api.js";
import { renderSchemaForm, collectSchemaValues } from "./form.js";

export function createConfigView(prefix, configPath, opts) {
  opts = opts || {};
  const formEl = document.getElementById(prefix + "-form");
  const rawEl = document.getElementById(prefix + "-raw");
  const statusEl = document.getElementById(prefix + "-status");
  const saveBtn = document.getElementById(prefix + "-save");
  const toggleBtn = document.getElementById(prefix + "-raw-toggle");
  const reloadBtn = document.getElementById(prefix + "-reload");

  let raw = false;
  let schema = [];

  function setDirty(v) {
    if (opts.onDirty) opts.onDirty(v);
    if (v && statusEl) statusEl.textContent = "несохранённые изменения";
  }

  // Кнопка «Текстовый режим» должна отражать состояние: название и подсветка.
  const RAW_LABEL = "Текстовый режим";
  const FORM_LABEL = "Обычный режим";

  function syncToggleLabel() {
    if (!toggleBtn) return;
    toggleBtn.textContent = raw ? FORM_LABEL : RAW_LABEL;
    toggleBtn.classList.toggle("active", raw);
  }

  async function load() {
    const [schemaData, cfg] = await Promise.all([
      api((opts.schemaPath || configPath) + "/schema"),
      api(configPath),
    ]);
    schema = schemaData.schema;
    renderSchemaForm(formEl, schema, cfg);
    raw = false;
    syncToggleLabel();
    rawEl.style.display = "none";
    formEl.style.display = "";
    if (statusEl) statusEl.textContent = "";
    setDirty(false);
  }

  async function toggleRaw() {
    raw = !raw;
    syncToggleLabel();
    if (raw) {
      try {
        const data = await api((opts.schemaPath || configPath) + "/raw");
        rawEl.value = data.yaml;
      } catch (err) {
        raw = false;
        syncToggleLabel();
        if (statusEl) statusEl.textContent = "Ошибка: " + err.message;
        return;
      }
      rawEl.style.display = "";
      formEl.style.display = "none";
    } else {
      rawEl.style.display = "none";
      formEl.style.display = "";
    }
  }

  async function save() {
    if (statusEl) statusEl.textContent = "Сохранение…";
    try {
      const isRaw = raw;
      let payload;
      let contentType = "application/json";
      if (isRaw) {
        payload = rawEl.value;
        contentType = "text/plain";
      } else {
        payload = JSON.stringify(collectSchemaValues(formEl));
      }
      const r = await apiJSON("/api/" + configPath, {
        method: "PUT",
        headers: { "Content-Type": contentType },
        body: payload,
      });
      if (r.status === 401) return;
      if (!r.ok) {
        let msg = "не удалось сохранить";
        try {
          const d = await r.json();
          msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
        } catch (e) {}
        if (statusEl) statusEl.textContent = "Ошибка валидации: " + msg;
        return;
      }
      const saved = await r.json();
      if (!isRaw) renderSchemaForm(formEl, schema, saved);
      if (statusEl) {
        statusEl.textContent =
          opts.savedNote || "Сохранено ✓ (применится при следующем старте сервиса)";
      }
      setDirty(false);
    } catch (err) {
      if (statusEl) statusEl.textContent = "Ошибка: " + err.message;
    }
  }

  if (saveBtn) saveBtn.addEventListener("click", save);
  if (toggleBtn) toggleBtn.addEventListener("click", toggleRaw);
  if (reloadBtn) reloadBtn.addEventListener("click", load);
  formEl.addEventListener("input", () => setDirty(true));
  rawEl.addEventListener("input", () => setDirty(true));

  return { load };
}
