"use strict";

// Вкладки аналитика: «Параметры мониторинга» (config_service.yaml: форма по схеме
// + «Текстовый режим» с сырым YAML) и «Промпты» (редактор промптов).
import { $ } from "./utils.js";
import { api, apiJSON } from "./api.js";
import { createConfigView } from "./config_view.js";
import { renderMarkdown } from "./markdown.js";

export let monitorDirty = false;

const monitorView = createConfigView("monitor", "config", {
  schemaPath: "config/service",
  savedNote: "Сохранено ✓ (применится при следующем запуске парсера)",
  onDirty: (v) => {
    monitorDirty = v;
  },
});

export function loadMonitor() {
  return monitorView.load();
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
let promptPreview = false;

// --- Предпросмотр Markdown (только для .md-промптов) --------------------
function setPromptView(preview) {
  promptPreview = preview;
  const editor = $("#prompt-editor");
  const view = $("#prompt-preview");
  const toggle = $("#prompt-view-toggle");
  if (preview) {
    view.innerHTML = renderMarkdown(editor.value);
    view.style.display = "block";
    editor.style.display = "none";
    toggle.textContent = "Редактор";
  } else {
    view.style.display = "none";
    editor.style.display = "";
    toggle.textContent = "Просмотр";
  }
}

function updatePromptViewToggle(kind) {
  const toggle = $("#prompt-view-toggle");
  const md = kind === "markdown";
  toggle.style.display = md ? "inline-block" : "none";
  if (!md) setPromptView(false);
}

function promptEndpoint(path) {
  const service = $("#prompt-service").value;
  return "/api/" + service + (path ? "/" + path : "");
}

async function loadPromptList() {
  const data = await api(promptEndpoint(""));
  // Промпты (markdown) впереди, файлы данных (json, few_shot) — в конец списка.
  promptsMeta = (data.files || [])
    .slice()
    .sort((a, b) => (a.kind === "json" ? 1 : 0) - (b.kind === "json" ? 1 : 0));
  $("#prompt-dir").textContent = data.dir ? "каталог: " + data.dir : "каталог промптов не найден";
  const sel = $("#prompt-sel");
  const cur = sel.value;
  sel.innerHTML = "";
  promptsMeta.forEach((f) => {
    const o = document.createElement("option");
    o.value = f.name;
    const label = PROMPT_LABELS[f.name] || f.name;
    o.textContent = label + " (" + f.name + ")";
    sel.appendChild(o);
  });
  if (cur && promptsMeta.some((f) => f.name === cur)) sel.value = cur;
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
  setPromptView(false);
  $("#prompt-editor").value = data.content;
  const kindEl = $("#prompt-kind");
  kindEl.textContent = data.kind === "json" ? "JSON" : "markdown";
  kindEl.style.display = "inline-block";
  updatePromptViewToggle(data.kind);
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

export { loadPromptList, loadPrompt, savePrompt, promptDirty };

// --- Слушатели --------------------------------------------------------
$("#prompt-editor").addEventListener("input", () => {
  promptDirty = true;
  $("#prompt-status").textContent = "несохранённые изменения";
});
$("#prompt-save").addEventListener("click", savePrompt);
$("#prompt-view-toggle").addEventListener("click", () => setPromptView(!promptPreview));
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
