"use strict";

// Вкладки «Параметры» (config_service.yaml) и «Промпты» (редактор промптов).
import { $ } from "./utils.js";
import { api, apiJSON } from "./api.js";

let cfgDirty = false;

async function loadConfig() {
  const cfg = await api("config");
  $("#cfg-editor").value = JSON.stringify(cfg, null, 2);
  $("#cfg-status").textContent = "config_service.yaml";
  cfgDirty = false;
}

async function saveConfig() {
  let payload;
  try {
    payload = JSON.parse($("#cfg-editor").value);
  } catch (err) {
    $("#cfg-status").textContent = "Ошибка JSON: " + err.message;
    return;
  }
  $("#cfg-status").textContent = "Сохранение…";
  try {
    const r = await apiJSON("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const detail = await r.json();
      $("#cfg-status").textContent = "Ошибка валидации: " + JSON.stringify(detail.detail || detail);
      return;
    }
    const saved = await r.json();
    $("#cfg-editor").value = JSON.stringify(saved, null, 2);
    $("#cfg-status").textContent = "Сохранено ✓ (применится при следующем запуске парсера)";
    cfgDirty = false;
  } catch (err) {
    $("#cfg-status").textContent = "Ошибка: " + err.message;
  }
}

// --- Промпты ----------------------------------------------------------
// Человекочитаемые подписи для известных файлов промптов; неизвестные
// показываются по имени файла.
const PROMPT_LABELS = {
  "fit_system.md": "Fit — системный промпт",
  "judge_system.md": "Judge — системный промпт",
  "truncated_note.md": "Заметка: обрезанное описание",
  "full_text_note.md": "Заметка: полный текст ТЗ",
  "few_shot.json": "Примеры few-shot",
  "verdict_system.md": "Вердикт — системный промпт",
  "verdict_user.md": "Вердикт — шаблон запроса",
};
let promptsMeta = [];
let promptDirty = false;

function promptEndpoint(path) {
  const service = $("#prompt-service").value;
  return "/api/" + service + (path ? "/" + path : "");
}

async function loadPromptList() {
  const data = await api(promptEndpoint(""));
  promptsMeta = data.files;
  $("#prompt-dir").textContent = data.dir ? "каталог: " + data.dir : "каталог промптов не найден";
  const sel = $("#prompt-sel");
  const cur = sel.value;
  sel.innerHTML = "";
  data.files.forEach((f) => {
    const o = document.createElement("option");
    o.value = f.name;
    const label = PROMPT_LABELS[f.name] || f.name;
    o.textContent = label + " (" + f.name + ")";
    sel.appendChild(o);
  });
  if (cur && data.files.some((f) => f.name === cur)) sel.value = cur;
  if (sel.options.length) await loadPrompt();
  else {
    $("#prompt-editor").value = "";
    $("#prompt-kind").style.display = "none";
  }
}

async function loadPrompt() {
  const name = $("#prompt-sel").value;
  if (!name) return;
  const data = await api(promptEndpoint(encodeURIComponent(name)));
  $("#prompt-editor").value = data.content;
  const kindEl = $("#prompt-kind");
  kindEl.textContent = data.kind === "json" ? "JSON" : "markdown";
  kindEl.style.display = "inline-block";
  $("#prompt-status").textContent = "";
  promptDirty = false;
}

async function savePrompt() {
  const name = $("#prompt-sel").value;
  if (!name) return;
  const meta = promptsMeta.find((f) => f.name === name);
  const kind = meta ? meta.kind : "";
  const content = $("#prompt-editor").value;
  // Корректность JSON проверяем до отправки на сервер (сервер валидирует повторно).
  if (kind === "json") {
    try {
      JSON.parse(content);
    } catch (err) {
      $("#prompt-status").textContent = "Ошибка JSON: " + err.message;
      return;
    }
  }
  $("#prompt-status").textContent = "Сохранение…";
  try {
    const r = await apiJSON(promptEndpoint(encodeURIComponent(name)), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!r.ok) {
      let msg = "не удалось сохранить";
      try {
        const d = await r.json();
        if (d && d.detail) msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
      } catch (e) {
        /* ignore */
      }
      $("#prompt-status").textContent = "Ошибка: " + msg;
      return;
    }
    $("#prompt-status").textContent = "Сохранено ✓ (применится при следующем старте сервиса)";
    promptDirty = false;
  } catch (err) {
    $("#prompt-status").textContent = "Ошибка: " + err.message;
  }
}

export { loadConfig, saveConfig, loadPromptList, loadPrompt, savePrompt, cfgDirty, promptDirty };

$("#cfg-editor").addEventListener("input", () => {
  cfgDirty = true;
  $("#cfg-status").textContent = "несохранённые изменения";
});
$("#cfg-save").addEventListener("click", saveConfig);
$("#cfg-reload").addEventListener("click", loadConfig);
$("#prompt-editor").addEventListener("input", () => {
  promptDirty = true;
  $("#prompt-status").textContent = "несохранённые изменения";
});
$("#prompt-save").addEventListener("click", savePrompt);
$("#prompt-reload").addEventListener("click", loadPrompt);
$("#prompt-sel").addEventListener("change", () => {
  if (promptDirty && !confirm("Есть несохранённые изменения — перезагрузить промпт?")) {
    const sel = $("#prompt-sel");
    sel.value = sel.dataset.last || sel.options[0].value;
    return;
  }
  $("#prompt-sel").dataset.last = $("#prompt-sel").value;
  loadPrompt();
});
$("#prompt-service").addEventListener("change", () => {
  if (promptDirty && !confirm("Есть несохранённые изменения — переключить сервис?")) {
    const svc = $("#prompt-service");
    svc.value = svc.dataset.last || "prompts";
    return;
  }
  $("#prompt-service").dataset.last = $("#prompt-service").value;
  $("#prompt-sel").dataset.last = "";
  loadPromptList();
});
