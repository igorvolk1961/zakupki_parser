"use strict";

// Вкладка «Профили»: список, редактор профиля (слова/компетенции/вопросы),
// лицензии и подтверждённый опыт (BR-03), переключение активного клиента.
import { $, escapeHtml, fmtMoney } from "./utils.js";
import {
  CONDITION_OPS,
  REPORT_FIELD_TYPE_LABELS,
  conditionText,
  opsForType,
} from "./conditions.js";
import { api, apiJSON, apiErrorDetail } from "./api.js";
import { confirmDialog, confirmDialogAsync } from "./dialogs.js";
import { loadProc, loadPlatforms } from "./procurements.js";
import { loadCustomers } from "./customers.js";
import { switchTo } from "./roles.js";

let profileEditorId = null;
let profileEditorName = "";
// Общее число профилей пользователя (для запрета удаления последнего).
let profilesTotal = 0;
let profileKeywords = [];
let profileExcl = [];
let profileOkpd = [];
let profileRegions = [];
let profileKeywordsLoaded = 0;
let profileExclLoaded = 0;
// Площадки, используемые профилем (target_etp): выбираются из активных.
// ALL_PLATFORMS_SENTINEL — значение «все площадки» (см. storage/db/profile.py,
// тот же литерал): устойчиво к появлению новых площадок в системе, в отличие
// от перечисления всех id. ПУСТОЙ список target_etp означает «ни одной
// площадки» — чтобы получить прежнее поведение «все», нужно явно выбрать
// «Все площадки» в форме (чекбокс ниже), которая по умолчанию включена для
// новых профилей.
const ALL_PLATFORMS_SENTINEL = "__all__";
let profilePlatforms = [];
let platformCatalog = [];
let platformsLoaded = false;
// Компетенции профиля: структурированная форма (JSON, модель scoring Profile)
// либо ручная правка того же JSON (кнопка «Режим текста»).
let compStructured = {
  positioning: "",
  breadth: "broad",
  competencies: [],
  exclusions: [],
  uncovered_penalty: 1.5,
  ambiguous_range: [4, 6],
};
let compMode = "structured"; // "structured" | "raw"
let compExclusions = [];
// Слепок формы профиля (включая лицензии/опыт) на момент загрузки/сохранения:
// индикатор несохранённых изменений на кнопке «Сохранить профиль».
let profileSavedSnapshot = "";
function snapshotProfile() {
  profileSavedSnapshot = JSON.stringify(profileFormData());
}
function isProfileDirty() {
  return JSON.stringify(profileFormData()) !== profileSavedSnapshot;
}
// Лицензии и подтверждённый опыт профиля (вложенные списки, BR-03).
let profileLicenses = [];
let profileExperience = [];
let licenseTypes = [];
let confirmationTypes = [];
let licenseEditorId = null;
let experienceEditorId = null;
// Фильтр поиска вида лицензии по подстроке (выбор в форме лицензии профиля).
let licenseTypeFilter = "";
// Временные id для новых записей лицензий/опыта в форме (до сохранения профиля).
let localEntrySeq = 0;
// Конструктор отчётных полей (FR-12.1): произвольные поля профиля, значение
// каждого извлекается RAG-анализом по документам закупки. Хранятся прямо в
// профиле (JSONB), без отдельного API/сохранения по клику.
let profileReportFields = [];
let reportFieldSeq = 1;
let reportFieldEditorId = null;
// Кэш профилей для выпадающего списка выбора активного профиля (вкладки
// «Закупки» и «В работе»). Активным может быть и выключенный профиль.
let profilesCache = [];

function activeProfileFrom(items) {
  // Порядок совпадает с get_active_profile: активный -> default -> первый по id.
  return (
    items.find((p) => p.is_active) ||
    items.find((p) => p.name === "default") ||
    items[0] ||
    null
  );
}

// Заполняет все селекторы .active-profile-select текущим списком профилей.
// Активный профиль — выбранный (инвариант FR-1.3: активный есть всегда);
// запасной порядок совпадает с get_active_profile на бэкенде.
function renderActiveProfileSelectors() {
  const selects = document.querySelectorAll("select.active-profile-select");
  if (!selects.length) return;
  const active = activeProfileFrom(profilesCache);
  selects.forEach((sel) => {
    sel.innerHTML = "";
    if (!profilesCache.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "— нет профилей —";
      sel.appendChild(opt);
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    profilesCache.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = String(p.id);
      // Выключенные профили доступны для выбора: активность не зависит от
      // участия в постоянном мониторинге.
      opt.textContent = p.enabled ? p.name : `${p.name} (выключен)`;
      if (active && p.id === active.id) opt.selected = true;
      sel.appendChild(opt);
    });
  });
}

async function loadActiveProfileSelector() {
  try {
    const list = await api("clients", { limit: 500 });
    profilesCache = list.items || [];
    renderActiveProfileSelectors();
  } catch (err) {
    /* базовая вкладка недоступна/нет сети — оставляем текущее состояние */
  }
}

async function onActiveProfileChange(event) {
  const sel = event.target;
  const id = Number(sel.value);
  if (!id) return;
  sel.disabled = true;
  try {
    await switchClient(id);
  } finally {
    sel.disabled = false;
  }
}

function renderProfiles(list) {
  const wrap = $("#profiles");
  if (!list.items.length) {
    wrap.innerHTML = '<p class="muted">Профилей нет.</p>';
    return;
  }
  const rows = list.items
    .map((p) => {
      const active = p.is_active
        ? '<span class="pill active">активный</span>'
        : '<span class="pill inactive">не активный</span>';
      const enabled = p.enabled ? "да" : "нет";
      const okpd = (p.okpd_codes || []).join(", ") || "—";
      const nmck = `${p.nmck_min ?? "—"}–${p.nmck_max ?? "—"}`;
      const words = (p.keywords || []).length;
      // Нельзя удалить активный профиль или единственный профиль пользователя.
      let delDisabled = "";
      let delTitle = "";
      if (list.total <= 1) {
        delDisabled = " disabled";
        delTitle = ' title="Нельзя удалить последний профиль"';
      } else if (p.is_active) {
        delDisabled = " disabled";
        delTitle = ' title="Нельзя удалить активный профиль — сначала активируйте другой"';
      }
      return `<tr data-id="${p.id}" data-name="${escapeHtml(p.name)}">
      <td>${escapeHtml(p.name)}</td>
      <td>${active}</td>
      <td>${enabled}</td>
      <td>${words}</td>
      <td>${escapeHtml(okpd)}</td>
      <td>${escapeHtml(nmck)}</td>
      <td>
        <button class="ghost" data-action="edit">Редактировать</button>
        <button class="ghost" data-action="export" title="Экспорт профиля в markdown-файл (файл профиля и, при желании, отдельный файл с компетенциями)">Экспорт</button>
        <button class="ghost" data-action="delete"${delDisabled}${delTitle}>Удалить профиль</button>
        ${p.is_active ? "" : `<button class="ghost" data-action="activate">Активировать</button>`}
      </td>
    </tr>`;
    })
    .join("");
  wrap.innerHTML = `<div class="table-wrap"><table class="cust">
    <thead><tr><th>Имя</th><th>Активность</th><th>Включён</th><th>Слов</th><th>ОКПД2</th><th>НМЦК</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
  wrap.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (btn.disabled) return;
      const tr = btn.closest("tr");
      const id = Number(tr.dataset.id);
      if (btn.dataset.action === "activate") await switchClient(id);
      else if (btn.dataset.action === "edit") await openProfileEditor(id);
      else if (btn.dataset.action === "export") openExportProfile(id, tr.dataset.name);
      else if (btn.dataset.action === "delete") confirmDeleteProfile(id, tr.dataset.name);
    });
  });
}

async function loadProfiles() {
  try {
    const list = await api("clients", { limit: 500 });
    profilesTotal = list.total;
    profilesCache = list.items || [];
    renderProfiles(list);
    renderActiveProfileSelectors();
  } catch (err) {
    $("#profiles").innerHTML = `<p class="muted">Не удалось загрузить профили: ${escapeHtml(err.message)}</p>`;
  }
}

async function ensurePlatformCatalog() {
  if (platformsLoaded) return;
  try {
    const data = await api("platforms");
    // Полный справочник площадок (включая деактивированные): в таблице профиля
    // показываются только активные, деактивированные скрыты, но остаются
    // в списке профиля и возвращаются в таблицу при реактивации.
    platformCatalog = data.items || [];
  } catch {
    platformCatalog = [];
  }
  platformsLoaded = true;
}

function renderPlatformAdd(sel) {
  sel.innerHTML = "";
  const used = new Set(profilePlatforms);
  const available = platformCatalog.filter((p) => p.enabled && !used.has(p.platform_id));
  if (!available.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "— нет доступных площадок —";
    sel.appendChild(o);
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  available.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.platform_id;
    o.textContent = p.name;
    sel.appendChild(o);
  });
}

function renderPlatformTable() {
  const box = $("#pf-platforms");
  if (!box) return;
  box.innerHTML = "";

  const allSelected = profilePlatforms.includes(ALL_PLATFORMS_SENTINEL);

  const allRow = document.createElement("label");
  allRow.className = "pf-platform-all";
  allRow.style.cssText = "display:flex; align-items:center; gap:8px; margin-bottom:10px; cursor:pointer;";
  const allCb = document.createElement("input");
  allCb.type = "checkbox";
  allCb.id = "pf-platform-all";
  allCb.checked = allSelected;
  allCb.title =
    "Обходятся все активные площадки, включая добавленные в систему позже. " +
    "Снимите галку, чтобы выбрать конкретные площадки — пустой список означает, что не обходится ни одна.";
  allCb.addEventListener("change", () => {
    profilePlatforms = allCb.checked ? [ALL_PLATFORMS_SENTINEL] : [];
    renderPlatformTable();
    syncEntryFormState();
    wordCounts();
  });
  allRow.appendChild(allCb);
  allRow.appendChild(document.createTextNode(" Все площадки"));
  box.appendChild(allRow);

  if (allSelected) {
    const note = document.createElement("div");
    note.className = "muted";
    note.style.marginBottom = "6px";
    note.textContent = "Обход по всем активным площадкам, включая добавленные позже.";
    box.appendChild(note);
    return;
  }

  // Добавление площадки в профиль — через выпадающий список активных
  // площадок, ещё не добавленных в профиль.
  const addRow = document.createElement("div");
  addRow.className = "pf-platform-add";
  const label = document.createElement("label");
  label.title = "Добавить площадку в профиль (только активные)";
  label.appendChild(document.createTextNode("Добавить площадку: "));
  const sel = document.createElement("select");
  sel.id = "pf-platform-add";
  renderPlatformAdd(sel);
  label.appendChild(sel);
  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "ghost btn-mini";
  addBtn.textContent = "Добавить";
  addBtn.disabled = sel.disabled;
  addBtn.addEventListener("click", () => {
    const id = sel.value;
    if (!id) return;
    if (!profilePlatforms.includes(id)) profilePlatforms.push(id);
    renderPlatformTable();
    syncEntryFormState();
    wordCounts();
  });
  label.appendChild(addBtn);
  addRow.appendChild(label);
  box.appendChild(addRow);

  const table = document.createElement("table");
  table.className = "cfg-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["Площадка", "Название", "URL", ""].forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  table.appendChild(tbody);
  box.appendChild(table);

  // В таблице — только активные площадки профиля. Деактивированные скрыты,
  // но остаются в profilePlatforms (target_etp), поэтому при реактивации
  // снова появляются в таблице.
  const map = new Map(platformCatalog.map((p) => [p.platform_id, p]));
  const visible = profilePlatforms.map((id) => map.get(id)).filter((p) => p && p.enabled);

  if (!visible.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.className = "muted";
    td.textContent = "Площадки не выбраны — обход не будет выполняться ни по одной площадке";
    tr.appendChild(td);
    tbody.appendChild(tr);
  } else {
    visible.forEach((p) => {
      const tr = document.createElement("tr");
      const keyTd = document.createElement("td");
      keyTd.textContent = p.platform_id;
      tr.appendChild(keyTd);
      const nameTd = document.createElement("td");
      nameTd.textContent = p.name;
      tr.appendChild(nameTd);
      const urlTd = document.createElement("td");
      const a = document.createElement("a");
      a.href = p.url;
      a.textContent = p.url;
      a.target = "_blank";
      a.rel = "noopener";
      urlTd.appendChild(a);
      tr.appendChild(urlTd);
      const delTd = document.createElement("td");
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "ghost btn-mini";
      delBtn.textContent = "×";
      delBtn.title = "Убрать площадку из профиля";
      delBtn.addEventListener("click", () => {
        profilePlatforms = profilePlatforms.filter((x) => x !== p.platform_id);
        renderPlatformTable();
        syncEntryFormState();
        wordCounts();
      });
      delTd.appendChild(delBtn);
      tr.appendChild(delTd);
      tbody.appendChild(tr);
    });
  }
  wordCounts();
}

function setProfileStatus(msg) {
  $("#profile-status").textContent = msg;
}

function fillProfileForm(p) {
  $("#pf-profile-url").value = p ? p.website_url || "" : "";
  setProfileUrlStatus("");
  renderUnmatchedLicenses([]);
  profileEditorName = p ? p.name : "";
  profileKeywordsLoaded = (p ? p.keywords || [] : []).length;
  profileExclLoaded = (p ? p.exclusion_words || [] : []).length;
  // Новый профиль (p отсутствует) по умолчанию — «Все площадки»; у
  // существующего показываем то, что реально сохранено (в т.ч. пустой список
  // — «ни одной», ALL_PLATFORMS_SENTINEL — «все»).
  profilePlatforms = p ? (p.target_etp || []).slice() : [ALL_PLATFORMS_SENTINEL];
  // Каталог перечитываем при каждом открытии профиля: реактивированные на
  // панели «Аналитика» площадки должны снова появиться в таблице.
  platformsLoaded = false;
  ensurePlatformCatalog().then(renderPlatformTable);
  $("#profile-editor-name").textContent = p ? `#${p.id} «${p.name}»` : "новый";
  $("#pf-name").value = p ? p.name : "";
  $("#pf-enabled").checked = p ? p.enabled : true;
  $("#pf-search-in-documents").checked = p ? !!p.search_in_documents : false;
  profileOkpd.length = 0;
  (p ? p.okpd_codes || [] : []).forEach((c) => profileOkpd.push(c));
  renderTags(profileOkpd, "#pf-okpd-tags");
  $("#pf-nmck-min").value = p && p.nmck_min != null ? p.nmck_min : "";
  $("#pf-nmck-max").value = p && p.nmck_max != null ? p.nmck_max : "";
  profileRegions.length = 0;
  (p ? p.target_regions || [] : []).forEach((r) => profileRegions.push(r));
  renderTags(profileRegions, "#pf-regions-tags");
  $("#pf-region-distance").value =
    p && p.max_region_distance_km != null ? p.max_region_distance_km : "";
  profileKeywords.length = 0;
  (p ? p.keywords || [] : []).forEach((w) => profileKeywords.push(w));
  profileExcl.length = 0;
  (p ? p.exclusion_words || [] : []).forEach((w) => profileExcl.push(w));
  renderTags(profileKeywords, "#pf-keywords-tags");
  renderTags(profileExcl, "#pf-excl-tags");
  profileReportFields.length = 0;
  (p ? p.report_fields || [] : []).forEach((f) =>
    profileReportFields.push({
      id: f.id || `f${reportFieldSeq++}`,
      name: f.name || "",
      hint: f.hint || "",
      type: f.type || "string",
      unit: f.unit || null,
      value_mode: f.value_mode || "auto",
      extend_list: f.extend_list !== false,
      condition: f.condition || null,
      blocking: !!f.blocking,
    })
  );
  reportFieldSeq = Math.max(
    reportFieldSeq,
    ...profileReportFields.map((f) => (parseInt(String(f.id).replace(/\D/g, ""), 10) || 0) + 1)
  );
  renderReportFields();
  // Блокирующие фиксированные категории требований (единый отчёт, без LLM,
  // scoring_common.requirements) — {"licenses": bool, ...}, отсутствующий
  // ключ = не блокирует (дефолт нового профиля).
  const reqBlocking = p ? p.requirement_blocking || {} : {};
  $("#rb-licenses").checked = !!reqBlocking.licenses;
  $("#rb-experience").checked = !!reqBlocking.experience;
  $("#rb-minprom").checked = !!reqBlocking.minprom;
  $("#rb-subcontractors").checked = !!reqBlocking.subcontractors;
  // Компетенции: сервер хранит только каноническую JSON-схему (проверка при записи).
  compStructured = parseComp(p ? p.competencies || "" : "") || defaultComp();
  compMode = "structured";
  renderCompForm();
  const delBtn = $("#profile-delete");
  delBtn.style.display = p ? "inline-block" : "none";
  if (p) {
    // Нельзя удалить активный профиль или единственный профиль пользователя.
    if (profilesTotal <= 1) {
      delBtn.disabled = true;
      delBtn.title = "Нельзя удалить последний профиль";
    } else if (p.is_active) {
      delBtn.disabled = true;
      delBtn.title = "Нельзя удалить активный профиль — сначала активируйте другой";
    } else {
      delBtn.disabled = false;
      delBtn.title = "";
    }
  }
  switchProfileTab("keywords");
  wordCounts();
  snapshotProfile();
  syncEntryFormState();
}

function setWordCount(el, n) {
  el.textContent = n ? String(n) : "";
}

// Реестр поиска по блокам тегов (wrapId -> текущая строка фильтра). Фильтр —
// отдельное поле над списком (bindTagFilter), не смешанное с полем добавления
// (bindTagInput внутри .tag-input); скрывает несовпадающие чипы, не меняя
// источник данных (arr).
const tagSearches = new Map();

function renderTags(arr, wrapId) {
  const box = $(wrapId);
  if (!box) return;
  const tagsEl = box.querySelector(".tags");
  const fallback = box.querySelector(".tags-fallback");
  const label = (item) => (typeof item === "object" && item != null ? item.text : item);
  const filter = (tagSearches.get(wrapId) || "").trim().toLocaleLowerCase();
  const matches = (w) => !filter || w.toLocaleLowerCase().includes(filter);
  let visible = 0;
  try {
    tagsEl.innerHTML = "";
    arr.forEach((item, i) => {
      const w = String(label(item));
      // Несовпадающие с поиском слова скрываем, но индекс i остаётся
      // корректным для arr.splice(i, 1) при удалении через «×».
      if (!matches(w)) return;
      visible++;
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = w;
      tag.title = w;
      const x = document.createElement("span");
      x.className = "x";
      x.textContent = "×";
      x.title = "Удалить словосочетание";
      x.addEventListener("click", () => {
        arr.splice(i, 1);
        renderTags(arr, wrapId);
        wordCounts();
        syncEntryFormState();
      });
      tag.appendChild(x);
      tagsEl.appendChild(tag);
    });
  } catch (err) {
    console.error("renderTags:", err);
  }
  const shown = filter ? arr.filter((it) => matches(String(label(it)))) : arr;
  // Запасной вывод: если чипы не отрисовались, показываем список словами,
  // чтобы слова никогда не «исчезали» из формы.
  if (shown.length && tagsEl.childElementCount !== shown.length) {
    fallback.textContent = shown.map(label).join("; ");
    fallback.style.display = "";
    tagsEl.style.display = "none";
  } else {
    fallback.style.display = "none";
    tagsEl.style.display = "";
  }
  // Индикатор результата поиска: «показано X из N» при активном фильтре.
  const status = box.closest("label")?.querySelector(".tag-search-status");
  if (status) {
    if (filter && arr.length) {
      status.textContent = visible ? `показано ${visible} из ${arr.length}` : "ничего не найдено";
    } else {
      status.textContent = "";
    }
  }
}

// Отдельное поле «поиск по списку» (над блоком тегов, .tag-search-row): только
// фильтрует уже введённые слова, ничего не добавляет — добавление остаётся за
// input внутри .tag-input (bindTagInput), общим для всех текстовых списков.
function bindTagFilter(wrapId, arr) {
  const box = $(wrapId);
  const label = box && box.closest("label");
  const input = label && label.querySelector(".tag-search");
  if (!input) return;
  const apply = () => {
    tagSearches.set(wrapId, input.value);
    renderTags(arr, wrapId);
  };
  input.addEventListener("input", apply);
  input.addEventListener("search", apply);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      input.value = "";
      apply();
    }
  });
}

// Добавляет слово(а) из input.value в arr (через запятую — несколько за раз),
// с дедупликацией без учёта регистра; общая логика поля добавления для всех
// текстовых списков профиля (bindTagInput — ключевые слова, слова-исключения,
// вопросы, коды ОКПД2, компетенции). Возвращает true, если что-то добавлено.
function addTagWords(input, arr, wrapId, makeItem) {
  const label = (item) => (typeof item === "object" && item != null ? item.text : item);
  const parts = input.value.split(",").map((s) => s.trim()).filter(Boolean);
  if (!parts.length) return false;
  parts.forEach((w) => {
    const item = makeItem ? makeItem(w) : w;
    const key = String(label(item)).toLocaleLowerCase();
    if (!arr.some((x) => String(label(x)).toLocaleLowerCase() === key)) arr.push(item);
  });
  input.value = "";
  renderTags(arr, wrapId);
  wordCounts();
  syncEntryFormState();
  return true;
}

function bindTagInput(wrapId, arr, makeItem) {
  const input = $(wrapId).querySelector("input");
  // Страховка: при фокусе пере-рисуем чипы, чтобы они не могли остаться скрытыми.
  input.addEventListener("focus", () => {
    renderTags(arr, wrapId);
    wordCounts();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    addTagWords(input, arr, wrapId, makeItem);
  });
}

// --- Коды ОКПД2: чипы-теги с контролем формата (#1/#2) ------------------
// Формат кода: 2-9 цифр, разделённых точками (например 62.02, 62.02.20.110).
function okpdIsValid(code) {
  const c = String(code).trim();
  if (!c) return false;
  const cleaned = c.replace(/\s+/g, ".").replace(/-/g, ".");
  const digits = cleaned.replace(/\D/g, "");
  if (digits.length < 2 || digits.length > 9) return false;
  if (/^\d+$/.test(cleaned)) return true;
  return /^\d{2}(\.\d{1,3})*$/.test(cleaned);
}

function setProfileSaveStatus(msg, isError) {
  const el = $("#profile-save-status");
  if (!el) return;
  el.textContent = msg || "";
  el.classList.toggle("error", !!isError);
}

function bindOkpdTagInput() {
  const box = $("#pf-okpd-tags");
  if (!box) return;
  const input = box.querySelector("input");
  input.addEventListener("focus", () => renderTags(profileOkpd, "#pf-okpd-tags"));
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const parts = input.value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return;
    const bad = [];
    parts.forEach((w) => {
      if (!okpdIsValid(w)) {
        bad.push(w);
        return;
      }
      if (!profileOkpd.includes(w)) profileOkpd.push(w);
    });
    if (bad.length) {
      setProfileSaveStatus(
        `Код ОКПД2 «${bad.join('», «')}» имеет неверный формат: цифры, разделённые точками (например, 62.02 или 62.02.20.110)`,
        true
      );
    } else {
      setProfileSaveStatus("");
    }
    input.value = "";
    renderTags(profileOkpd, "#pf-okpd-tags");
  });
}

// --- Структурированный редактор компетенций (JSON-модель scoring Profile) --
function defaultComp() {
  return {
    positioning: "",
    breadth: "broad",
    competencies: [],
    exclusions: [],
    uncovered_penalty: 1.5,
    ambiguous_range: [4, 6],
  };
}

function parseComp(raw) {
  if (!raw || !raw.trim()) return defaultComp();
  try {
    const obj = JSON.parse(raw);
    if (
      obj &&
      typeof obj === "object" &&
      (obj.competencies || obj.positioning || obj.exclusions || obj.breadth)
    ) {
      const policy = obj.scoring_policy || {};
      return {
        positioning: typeof obj.positioning === "string" ? obj.positioning : "",
        breadth: obj.breadth === "narrow" ? "narrow" : "broad",
        competencies: Array.isArray(obj.competencies)
          ? obj.competencies.map((c) => ({
              area: (c && c.area) || "",
              description: (c && c.description) || "",
              examples: Array.isArray(c && c.examples) ? c.examples.map(String) : [],
            }))
          : [],
        exclusions: Array.isArray(obj.exclusions) ? obj.exclusions.map(String) : [],
        uncovered_penalty:
          typeof policy.uncovered_penalty === "number" ? policy.uncovered_penalty : 1.5,
        ambiguous_range:
          Array.isArray(policy.ambiguous_range) && policy.ambiguous_range.length === 2
            ? policy.ambiguous_range.map(Number)
            : [4, 6],
      };
    }
  } catch (e) {
    /* не JSON — не структурированный профиль */
  }
  return null;
}

function collectComp() {
  const competencies = [];
  document.querySelectorAll("#pf-comp-list .comp-item").forEach((el) => {
    const area = (el.querySelector("[data-comp-area]").value || "").trim();
    const description = (el.querySelector("[data-comp-desc]").value || "").trim();
    const examples = [...el.querySelectorAll(".comp-examples .tag")].map(
      (t) => t.childNodes[0].textContent
    );
    if (area || description || examples.length) {
      competencies.push({ area, description, examples });
    }
  });
  return {
    positioning: $("#pf-comp-positioning").value.trim(),
    breadth: $("#pf-comp-breadth").value,
    competencies,
    exclusions: compExclusions.slice(),
    scoring_policy: {
      uncovered_penalty: Number($("#pf-comp-penalty").value || 1.5),
      ambiguous_range: [
        Number($("#pf-comp-range-lo").value || 4),
        Number($("#pf-comp-range-hi").value || 6),
      ],
    },
  };
}

function renderCompList() {
  const box = $("#pf-comp-list");
  box.innerHTML = "";
  compStructured.competencies.forEach((comp, i) => {
    const el = document.createElement("div");
    el.className = "comp-item";
    el.style.cssText = "border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:8px;";
    const exId = "pf-comp-examples-" + i;
    el.innerHTML = `
      <label>Кейс <input data-comp-area value="${escapeHtml(comp.area)}"></label>
      <label>Описание <textarea data-comp-desc rows="2" spellcheck="false">${escapeHtml(comp.description)}</textarea></label>
      <label>Примеры
        <div class="tag-input comp-examples" id="${exId}">
          <div class="tags"></div>
          <div class="tags-fallback" style="display:none;"></div>
          <input type="text" placeholder="пример, Enter" spellcheck="false">
        </div>
      </label>
      <button type="button" class="ghost btn-mini comp-del" title="Удалить компетенцию">×</button>`;
    box.appendChild(el);
    renderTags(comp.examples, "#" + exId);
    bindTagInput("#" + exId, comp.examples, null);
  });
}

function setProfileUrlStatus(msg, isError) {
  const el = $("#pf-profile-url-status");
  el.textContent = msg || "";
  el.classList.toggle("error", !!isError);
}

// Заполняет профиль по сайту поставщика: сервер скачивает страницу и просит LLM
// собрать компетенции (та же каноническая схема, что у ручного ввода/импорта) и
// лицензии (сопоставленные со справочником license_types — несуществующие типы
// сервер уже отфильтровал). Результат только подставляется в форму (вкладки
// «Компетенции»/«Лицензии») — профиль не сохраняется автоматически.
// Лицензии, упомянутые на сайте, но без соответствия в справочнике видов
// лицензий (справочник не исчерпывающий): сохранить их как запись профиля
// нельзя (license_type_id обязателен), но и терять информацию молча нельзя —
// показываем как есть, пользователь решает сам (завести тип в «Справочниках»
// и добавить лицензию вручную, или проигнорировать).
function renderUnmatchedLicenses(items) {
  const box = $("#pf-profile-unmatched-licenses");
  const list = $("#pf-profile-unmatched-licenses-list");
  list.innerHTML = "";
  if (!items || !items.length) {
    box.style.display = "none";
    return;
  }
  items.forEach((lic) => {
    const li = document.createElement("li");
    const parts = [lic.name];
    if (lic.number) parts.push(`№ ${lic.number}`);
    if (lic.authority) parts.push(lic.authority);
    if (lic.issue_date) parts.push(`от ${lic.issue_date}`);
    li.textContent = parts.join(", ");
    list.appendChild(li);
  });
  box.style.display = "";
}

async function fillProfileFromUrl() {
  const url = $("#pf-profile-url").value.trim();
  if (!url) {
    setProfileUrlStatus("Укажите URL сайта", true);
    return;
  }
  const btn = $("#pf-profile-from-url");
  btn.disabled = true;
  setProfileUrlStatus("Скачиваю сайт и формирую профиль…");
  renderUnmatchedLicenses([]);
  try {
    const r = await apiJSON("/api/clients/profile/from-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!r.ok) throw new Error(await apiErrorDetail(r));
    const data = await r.json();
    const parsed = parseComp(data.competencies);
    if (!parsed) throw new Error("Сервер вернул компетенции в неожиданном формате");
    compStructured = parsed;
    compMode = "structured";
    renderCompForm();

    await ensureLicenseTypes();
    const incoming = Array.isArray(data.licenses) ? data.licenses : [];
    let added = 0;
    incoming.forEach((lic) => {
      const isDup = profileLicenses.some(
        (x) =>
          x.license_type_id === lic.license_type_id && (x.number || "") === (lic.number || "")
      );
      if (isDup) return;
      profileLicenses.push({
        license_type_id: lic.license_type_id,
        number: lic.number || null,
        authority: lic.authority || null,
        issue_date: lic.issue_date || null,
        expiry_date: lic.expiry_date || null,
        notes: lic.notes || null,
        id: "local-" + String(++localEntrySeq),
      });
      added++;
    });
    renderLicenses();
    const unmatched = Array.isArray(data.unmatched_licenses) ? data.unmatched_licenses : [];
    renderUnmatchedLicenses(unmatched);

    wordCounts();
    syncEntryFormState();
    const licPart = added ? `, лицензий добавлено: ${added}` : "";
    const unmatchedPart = unmatched.length
      ? `; не сопоставлено со справочником: ${unmatched.length} (см. список ниже)`
      : "";
    setProfileUrlStatus(
      `Профиль сформирован по сайту${licPart}${unmatchedPart} — проверьте вкладки ` +
        "«Компетенции»/«Лицензии» и сохраните профиль"
    );
  } catch (e) {
    setProfileUrlStatus("Ошибка: " + e.message, true);
  } finally {
    btn.disabled = false;
  }
}

function renderCompForm() {
  const structured = compMode === "structured";
  $("#pf-comp-structured").style.display = structured ? "" : "none";
  $("#pf-competencies").style.display = structured ? "none" : "";
  $("#pf-comp-mode").textContent = structured ? "Режим текста" : "Режим структуры";
  if (!structured) {
    wordCounts();
    return;
  }
  $("#pf-comp-positioning").value = compStructured.positioning || "";
  $("#pf-comp-breadth").value = compStructured.breadth === "narrow" ? "narrow" : "broad";
  compExclusions.length = 0;
  (compStructured.exclusions || []).forEach((e) => compExclusions.push(e));
  $("#pf-comp-penalty").value = compStructured.uncovered_penalty ?? 1.5;
  $("#pf-comp-range-lo").value = (compStructured.ambiguous_range || [4, 6])[0];
  $("#pf-comp-range-hi").value = (compStructured.ambiguous_range || [4, 6])[1];
  renderCompList();
  renderTags(compExclusions, "#pf-comp-exclusions");
  bindTagInput("#pf-comp-exclusions", compExclusions, null);
  wordCounts();
}

function switchCompMode() {
  if (compMode === "structured") {
    // → сырой текст: показываем текущую структуру как JSON.
    $("#pf-competencies").value = JSON.stringify(collectComp(), null, 2);
    compMode = "raw";
  } else {
    const parsed = parseComp($("#pf-competencies").value);
    if (parsed) {
      compStructured = parsed;
      compMode = "structured";
    } else {
      setProfileStatus("Текст не является структурированным профилем (JSON) — остаёмся в текстовом режиме");
      return;
    }
  }
  renderCompForm();
}

function compCount() {
  // Счётчик «направлений»: количество карточек компетенций (не слов).
  if (compMode !== "structured") {
    const parsed = parseComp($("#pf-competencies").value);
    return parsed && parsed.competencies ? parsed.competencies.length : 0;
  }
  return collectComp().competencies.length;
}

// Профиль без компетенций сохранить можно (запрет снят) — но скоринг по нему
// не выполняется (Scheduler._profile_has_valid_competencies), предупреждаем
// об этом прямо в редакторе вместо блокировки сохранения.
function compIsEmpty() {
  const c = compMode === "structured" ? collectComp() : parseComp($("#pf-competencies").value);
  if (!c) return true;
  return !((c.positioning || "").trim() || c.competencies.length || (c.exclusions || []).length);
}

function wordCounts() {
  setWordCount($("#pf-cnt-keywords"), profileKeywords.length);
  setWordCount($("#pf-cnt-excl"), profileExcl.length);
  setWordCount($("#pf-cnt-comp"), compCount());
  $("#pf-comp-empty-warning").style.display = compIsEmpty() ? "" : "none";
  // Счётчик площадок — только видимые (активные) строки таблицы профиля;
  // «Все площадки» показываем текстом, а не 0 (пустой список от «выбрано
  // 0 конкретных» не отличить иначе).
  if (profilePlatforms.includes(ALL_PLATFORMS_SENTINEL)) {
    $("#pf-cnt-platforms").textContent = "Все";
  } else {
    const catalogMap = new Map(platformCatalog.map((p) => [p.platform_id, p]));
    setWordCount(
      $("#pf-cnt-platforms"),
      profilePlatforms.filter((id) => {
        const p = catalogMap.get(id);
        return p && p.enabled;
      }).length
    );
  }
  setWordCount($("#pf-cnt-licenses"), profileLicenses.length);
  setWordCount($("#pf-cnt-experience"), profileExperience.length);
  setWordCount($("#pf-cnt-report-fields"), profileReportFields.length);
}

function switchProfileTab(name) {
  [
    "keywords",
    "excl",
    "comp",
    "platforms",
    "licenses",
    "experience",
    "report-fields",
  ].forEach((k) => {
    $("#pf-tab-" + k).classList.toggle("active", k === name);
    $("#pf-pane-" + k).style.display = k === name ? "" : "none";
  });
}

// --- Лицензии и подтверждённый опыт профиля (BR-03) ---------------------
function setLicenseStatus(msg) {
  $("#license-status").textContent = msg;
}
function setExperienceStatus(msg) {
  $("#experience-status").textContent = msg;
}

function updateProfileExtrasVisibility() {
  // Новый профиль ещё не сохранён: дочерние списки недоступны (нет profile_id).
  const hidden = !profileEditorId;
  ["licenses", "experience"].forEach((k) => {
    $("#pf-tab-" + k).style.display = hidden ? "none" : "";
    if (hidden) $("#pf-cnt-" + k).textContent = "";
  });
}

// Пока открыта форма добавления/редактирования лицензии или опыта — остальные
// операции с записями и «Сохранить профиль» недоступны (незаписанная запись
// потерялась бы при сохранении профиля).
function entryFormOpen() {
  return (
    $("#license-form").style.display === "block" ||
    $("#experience-form").style.display === "block" ||
    $("#report-field-form").style.display === "block"
  );
}
function syncEntryFormState() {
  const formOpen = entryFormOpen();
  [$("#license-new"), $("#experience-new"), $("#report-field-new")].forEach((b) => {
    b.disabled = formOpen;
    b.title = "";
  });
  document
    .querySelectorAll(
      "#licenses-table button[data-action], #experience-table button[data-action], #report-fields-table button[data-action]"
    )
    .forEach((b) => {
      b.disabled = formOpen;
    });
  // Пока открыта форма записи — «Сохранить профиль» недоступен; при несохранённых
  // изменениях на кнопке показывается маркер.
  const saveBtn = $("#profile-save");
  saveBtn.disabled = formOpen;
  const dirty = !!profileEditorId && isProfileDirty();
  saveBtn.classList.toggle("dirty", dirty);
  saveBtn.title = dirty ? "Есть несохранённые изменения" : "";
}

async function ensureLicenseTypes() {
  if (licenseTypes.length) return;
  try {
    licenseTypes = (await api("license-types")) || [];
  } catch {
    licenseTypes = [];
  }
}

function renderLicenseTypeOptions() {
  const sel = $("#lic-type");
  const q = licenseTypeFilter.trim().toLowerCase();
  const visible = q
    ? licenseTypes.filter((t) => t.name.toLowerCase().includes(q))
    : licenseTypes;
  sel.innerHTML = visible
    .map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`)
    .join("");
  return visible;
}

function wireLicenseTypeFilter(value) {
  const input = $("#lic-type-filter");
  input.value = value;
  // oninput (а не addEventListener) — повторное открытие формы не плодит обработчики.
  input.oninput = () => {
    licenseTypeFilter = input.value;
    renderLicenseTypeOptions();
  };
}

async function ensureConfirmationTypes() {
  if (confirmationTypes.length) return;
  try {
    confirmationTypes = (await api("confirmation-types")) || [];
  } catch {
    confirmationTypes = [];
  }
}

async function loadProfileExtras(id) {
  const [lic, exp] = await Promise.all([
    api(`clients/${id}/licenses`),
    api(`clients/${id}/experience`),
  ]);
  profileLicenses = (lic && lic.items) || [];
  profileExperience = (exp && exp.items) || [];
  renderLicenses();
  renderExperience();
  wordCounts();
}

function renderLicenses() {
  const wrap = $("#licenses-table");
  const typeName = (l) => {
    const t = licenseTypes.find((x) => x.id === l.license_type_id) || {};
    return t.name || (l.license_type && l.license_type.name) || "";
  };
  const status = (l) => {
    if (!l.expiry_date) return "бессрочная";
    const exp = new Date(l.expiry_date + "T00:00:00");
    return exp < new Date() ? "истекла" : "активна";
  };
  if (!profileLicenses.length) {
    wrap.innerHTML = `<p class="muted">Лицензий нет</p>`;
    return;
  }
  wrap.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>Тип</th><th>Номер</th><th>Орган</th><th>Выдана</th><th>Действует до</th><th>Статус</th><th></th></tr></thead>
    <tbody>${profileLicenses
      .map(
        (l) => `<tr data-id="${l.id}">
      <td>${escapeHtml(typeName(l))}</td>
      <td>${escapeHtml(l.number || "")}</td>
      <td>${escapeHtml(l.authority || "")}</td>
      <td>${l.issue_date || "—"}</td>
      <td>${l.expiry_date || "—"}</td>
      <td>${status(l)}</td>
      <td>
        <button class="ghost" data-action="edit">Редактировать</button>
        <button class="ghost" data-action="delete">Удалить лицензию</button>
      </td>
    </tr>`
      )
      .join("")}</tbody></table></div>`;
  wrap.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      // data-id — строковый идентификатор: для несохранённых записей это "local-N",
      // поэтому сравнение через String (Number("local-N") даёт NaN и ломает поиск).
      const rowId = btn.closest("tr").dataset.id;
      const lic = profileLicenses.find((x) => String(x.id) === rowId);
      if (btn.dataset.action === "edit") openLicenseForm(lic ? lic.id : null);
      else deleteLicense(lic);
    });
  });
  syncEntryFormState();
}

function renderExperience() {
  const wrap = $("#experience-table");
  const typeName = (e) => {
    const t = confirmationTypes.find((x) => x.id === e.confirmation_type_id) || {};
    return t.name || (e.confirmation_type && e.confirmation_type.name) || "";
  };
  const importVal = (e) =>
    e.import_independent == null ? "—" : e.import_independent ? "да" : "нет";
  if (!profileExperience.length) {
    wrap.innerHTML = `<p class="muted">Записей опыта нет</p>`;
    return;
  }
  wrap.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>Работы/контракт</th><th>Заказчик</th><th>Период</th><th>Цена</th><th>Тип подтверждения</th><th>Импортонезависимость</th><th></th></tr></thead>
    <tbody>${profileExperience
      .map(
        (e) => `<tr data-id="${e.id}">
      <td>${escapeHtml(e.title)}</td>
      <td>${escapeHtml(e.customer_name || "")}</td>
      <td>${e.start_date || ""}${e.end_date ? " — " + e.end_date : ""}</td>
      <td>${fmtMoney(e.amount)}</td>
      <td>${escapeHtml(typeName(e))}</td>
      <td>${importVal(e)}</td>
      <td>
        <button class="ghost" data-action="edit">Редактировать</button>
        <button class="ghost" data-action="delete">Удалить опыт</button>
      </td>
    </tr>`
      )
      .join("")}</tbody></table></div>`;
  wrap.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rowId = btn.closest("tr").dataset.id;
      const e = profileExperience.find((x) => String(x.id) === rowId);
      if (btn.dataset.action === "edit") openExperienceForm(e ? e.id : null);
      else deleteExperience(e);
    });
  });
  syncEntryFormState();
}

async function openLicenseForm(id) {
  licenseEditorId = id || null;
  await ensureLicenseTypes();
  const l = id ? profileLicenses.find((x) => x.id === id) : null;
  licenseTypeFilter = "";
  wireLicenseTypeFilter("");
  const visible = renderLicenseTypeOptions();
  $("#lic-type").value = l ? l.license_type_id : visible.length ? visible[0].id : "";
  $("#lic-number").value = l ? l.number || "" : "";
  $("#lic-authority").value = l ? l.authority || "" : "";
  $("#lic-issue-date").value = l ? l.issue_date || "" : "";
  $("#lic-expiry-date").value = l ? l.expiry_date || "" : "";
  $("#lic-notes").value = l ? l.notes || "" : "";
  setLicenseStatus("");
  $("#license-form").style.display = "block";
  syncEntryFormState();
}

async function saveLicense() {
  const data = {
    license_type_id: Number($("#lic-type").value),
    number: $("#lic-number").value.trim() || null,
    authority: $("#lic-authority").value.trim() || null,
    issue_date: $("#lic-issue-date").value || null,
    expiry_date: $("#lic-expiry-date").value || null,
    notes: $("#lic-notes").value.trim() || null,
  };
  if (!data.license_type_id) {
    setLicenseStatus("Укажите тип лицензии");
    return;
  }
  // Лицензия редактируется в форме профиля и сохраняется на сервер только
  // кнопкой «Сохранить профиль» (единая модель: форма → «Сохранить профиль»).
  if (licenseEditorId) {
    const entry = profileLicenses.find((x) => x.id === licenseEditorId);
    if (entry) Object.assign(entry, data);
  } else {
    profileLicenses.push({ ...data, id: "local-" + String(++localEntrySeq) });
  }
  $("#license-form").style.display = "none";
  renderLicenses();
  wordCounts();
  syncEntryFormState();
  setLicenseStatus("Сохранено — будет записано вместе с профилем");
}

function deleteLicense(lic) {
  if (!lic) return;
  const type = licenseTypes.find((x) => x.id === lic.license_type_id) || null;
  const label =
    type || lic.number
      ? `Удалить лицензию${type ? " «" + type.name + "»" : ""}${lic.number ? " №" + lic.number : ""}?`
      : "Удалить лицензию?";
  confirmDialog(label, () => {
    profileLicenses = profileLicenses.filter((x) => x.id !== lic.id);
    renderLicenses();
    wordCounts();
    syncEntryFormState();
    setLicenseStatus("Лицензия удалена — будет записано вместе с профилем");
  });
}

async function openExperienceForm(id) {
  experienceEditorId = id || null;
  await ensureConfirmationTypes();
  const e = id ? profileExperience.find((x) => x.id === id) : null;
  const sel = $("#exp-confirmation");
  sel.innerHTML = confirmationTypes
    .map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`)
    .join("");
  sel.value = e ? e.confirmation_type_id : confirmationTypes.length ? confirmationTypes[0].id : "";
  $("#exp-title").value = e ? e.title : "";
  $("#exp-customer").value = e ? e.customer_name || "" : "";
  $("#exp-contract").value = e ? e.contract_number || "" : "";
  $("#exp-amount").value = e && e.amount != null ? e.amount : "";
  $("#exp-start").value = e ? e.start_date || "" : "";
  $("#exp-end").value = e ? e.end_date || "" : "";
  $("#exp-import").value = e && e.import_independent != null ? String(e.import_independent) : "";
  $("#exp-notes").value = e ? e.notes || "" : "";
  setExperienceStatus("");
  $("#experience-form").style.display = "block";
  syncEntryFormState();
}

async function saveExperience() {
  const importVal = $("#exp-import").value;
  const data = {
    title: $("#exp-title").value.trim(),
    customer_name: $("#exp-customer").value.trim() || null,
    contract_number: $("#exp-contract").value.trim() || null,
    start_date: $("#exp-start").value || null,
    end_date: $("#exp-end").value || null,
    amount: $("#exp-amount").value === "" ? null : Number($("#exp-amount").value),
    confirmation_type_id: Number($("#exp-confirmation").value),
    import_independent: importVal === "" ? null : importVal === "true",
    notes: $("#exp-notes").value.trim() || null,
  };
  if (!data.confirmation_type_id) {
    setExperienceStatus("Укажите тип подтверждения");
    return;
  }
  if (!data.title) {
    setExperienceStatus("Укажите работы/контракт");
    return;
  }
  // Опыт редактируется в форме профиля и сохраняется на сервер только
  // кнопкой «Сохранить профиль» (единая модель: форма → «Сохранить профиль»).
  if (experienceEditorId) {
    const entry = profileExperience.find((x) => x.id === experienceEditorId);
    if (entry) Object.assign(entry, data);
  } else {
    profileExperience.push({ ...data, id: "local-" + String(++localEntrySeq) });
  }
  $("#experience-form").style.display = "none";
  renderExperience();
  wordCounts();
  syncEntryFormState();
  setExperienceStatus("Сохранено — будет записано вместе с профилем");
}

function deleteExperience(exp) {
  if (!exp) return;
  const label = exp.title ? `Удалить запись опыта «${exp.title}»?` : "Удалить запись опыта?";
  confirmDialog(label, () => {
    profileExperience = profileExperience.filter((x) => x.id !== exp.id);
    renderExperience();
    wordCounts();
    syncEntryFormState();
    setExperienceStatus("Опыт удалён — будет записано вместе с профилем");
  });
}

// --- Конструктор отчётных полей (FR-12.1) --------------------------------
function setReportFieldStatus(msg) {
  $("#report-field-status").textContent = msg;
}

function renderReportFields() {
  const wrap = $("#report-fields-table");
  if (!profileReportFields.length) {
    wrap.innerHTML = `<p class="muted">Отчётных полей нет</p>`;
    return;
  }
  wrap.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>Название</th><th>Тип</th><th>Подсказка</th><th>Единица</th><th>Условие</th><th></th></tr></thead>
    <tbody>${profileReportFields
      .map(
        (f) => `<tr data-id="${f.id}">
      <td>${escapeHtml(f.name)}</td>
      <td>${escapeHtml(REPORT_FIELD_TYPE_LABELS[f.type] || f.type)}</td>
      <td>${escapeHtml(f.hint || "")}</td>
      <td>${escapeHtml(f.unit || "")}</td>
      <td>${f.condition ? escapeHtml(conditionText(f.condition)) + (f.blocking ? ' <span class="pill inactive">блокирует</span>' : "") : "—"}</td>
      <td>
        <button class="ghost" data-action="edit">Редактировать</button>
        <button class="ghost" data-action="delete">Удалить поле</button>
      </td>
    </tr>`
      )
      .join("")}</tbody></table></div>`;
  wrap.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rowId = btn.closest("tr").dataset.id;
      const field = profileReportFields.find((x) => String(x.id) === rowId);
      if (btn.dataset.action === "edit") openReportFieldForm(field ? field.id : null);
      else deleteReportField(field);
    });
  });
  syncEntryFormState();
}

// Список операторов зависит от типа поля; выбранный сохраняется, если применим.
function renderConditionOps(selected) {
  const type = $("#rf-type").value;
  const ops = opsForType(type);
  const current = ops.includes(selected) ? selected : "";
  $("#rf-cond-op").innerHTML =
    `<option value="">— без условия —</option>` +
    ops
      .map(
        (op) =>
          `<option value="${op}"${op === current ? " selected" : ""}>${escapeHtml(CONDITION_OPS[op].label)}</option>`
      )
      .join("");
}

function updateReportFieldUnitVisibility() {
  const type = $("#rf-type").value;
  $("#rf-unit-row").style.display = type === "number" ? "" : "none";
  $("#rf-value-mode-row").style.display = type === "string" || type === "list" ? "" : "none";
  $("#rf-extend-row").style.display = type === "list" ? "" : "none";
  renderConditionOps($("#rf-cond-op").value);
  updateConditionValueVisibility();
}

function updateConditionValueVisibility() {
  const op = $("#rf-cond-op").value;
  const kind = op ? CONDITION_OPS[op].kind : null;
  $("#rf-cond-value-row").style.display = kind === "scalar" ? "" : "none";
  $("#rf-cond-list-row").style.display = kind === "list" ? "" : "none";
  $("#rf-blocking-row").style.display = op ? "" : "none";
  const placeholders = {
    number: "например, 500000",
    date: "например, 2026-12-31",
    boolean: "да или нет",
    string: "например, 1 11 010 21 49 2",
    list: "например, 1 11 010 21 49 2",
  };
  $("#rf-cond-value").placeholder =
    op === "llm" ? "например, не менее 500000" : placeholders[$("#rf-type").value] || "";
}

function openReportFieldForm(id) {
  reportFieldEditorId = id || null;
  const f = id ? profileReportFields.find((x) => x.id === id) : null;
  $("#rf-name").value = f ? f.name : "";
  $("#rf-type").value = f ? f.type : "string";
  $("#rf-hint").value = f ? f.hint || "" : "";
  $("#rf-unit").value = f ? f.unit || "" : "";
  $("#rf-value-mode").value = f ? f.value_mode || "auto" : "auto";
  $("#rf-extend-list").checked = f ? f.extend_list !== false : true;
  const cond = f ? f.condition : null;
  renderConditionOps(cond ? cond.op : "");
  $("#rf-cond-value").value = cond && !Array.isArray(cond.value) ? cond.value || "" : "";
  $("#rf-cond-list").value = cond && Array.isArray(cond.value) ? cond.value.join("\n") : "";
  $("#rf-blocking").checked = f ? !!f.blocking : false;
  updateReportFieldUnitVisibility();
  setReportFieldStatus("");
  $("#report-field-form").style.display = "block";
  syncEntryFormState();
}

function saveReportField() {
  const name = $("#rf-name").value.trim();
  if (!name) {
    setReportFieldStatus("Укажите название поля");
    return;
  }
  const type = $("#rf-type").value;
  const op = $("#rf-cond-op").value;
  let condition = null;
  if (op) {
    const kind = CONDITION_OPS[op].kind;
    if (kind === "list") {
      const items = $("#rf-cond-list")
        .value.split("\n")
        .map((x) => x.trim())
        .filter(Boolean);
      if (!items.length) {
        setReportFieldStatus("Укажите хотя бы одно значение списка условия");
        return;
      }
      condition = { op, value_kind: "list", value: items };
    } else {
      const value = $("#rf-cond-value").value.trim();
      if (!value) {
        setReportFieldStatus("Укажите значение условия");
        return;
      }
      condition = { op, value_kind: "scalar", value };
    }
  }
  const data = {
    name,
    type,
    hint: $("#rf-hint").value.trim() || null,
    unit: type === "number" ? $("#rf-unit").value.trim() || null : null,
    value_mode: type === "string" || type === "list" ? $("#rf-value-mode").value : "auto",
    extend_list: type === "list" ? $("#rf-extend-list").checked : true,
    condition,
    // Блокировка без условия бессмысленна (нечего нарушать).
    blocking: condition ? $("#rf-blocking").checked : false,
  };
  // Поле редактируется в форме профиля и сохраняется на сервер только кнопкой
  // «Сохранить профиль» (та же модель, что у лицензий/опыта).
  if (reportFieldEditorId) {
    const entry = profileReportFields.find((x) => x.id === reportFieldEditorId);
    if (entry) Object.assign(entry, data);
  } else {
    profileReportFields.push({ ...data, id: `f${reportFieldSeq++}` });
  }
  $("#report-field-form").style.display = "none";
  renderReportFields();
  wordCounts();
  syncEntryFormState();
  setReportFieldStatus("Сохранено — будет записано вместе с профилем");
}

function deleteReportField(field) {
  if (!field) return;
  confirmDialog(`Удалить отчётное поле «${field.name}»?`, () => {
    profileReportFields = profileReportFields.filter((x) => x.id !== field.id);
    renderReportFields();
    wordCounts();
    syncEntryFormState();
    setReportFieldStatus("Поле удалено — будет записано вместе с профилем");
  });
}

function profileFormData() {
  return {
    name: $("#pf-name").value.trim(),
    enabled: $("#pf-enabled").checked,
    search_in_documents: $("#pf-search-in-documents").checked,
    website_url: $("#pf-profile-url").value.trim() || null,
    okpd_codes: profileOkpd.slice(),
    nmck_min: $("#pf-nmck-min").value === "" ? null : Number($("#pf-nmck-min").value),
    nmck_max: $("#pf-nmck-max").value === "" ? null : Number($("#pf-nmck-max").value),
    target_regions: profileRegions.slice(),
    max_region_distance_km:
      $("#pf-region-distance").value === "" ? null : Number($("#pf-region-distance").value),
    target_etp: profilePlatforms.slice(),
    keywords: profileKeywords.slice(),
    exclusion_words: profileExcl.slice(),
    report_fields: profileReportFields.map((f) => ({
      id: f.id,
      name: f.name,
      hint: f.hint || null,
      type: f.type,
      unit: f.unit || null,
      value_mode: f.value_mode || "auto",
      extend_list: f.extend_list !== false,
      condition: f.condition || null,
      blocking: !!f.blocking,
    })),
    requirement_blocking: {
      licenses: $("#rb-licenses").checked,
      experience: $("#rb-experience").checked,
      minprom: $("#rb-minprom").checked,
      subcontractors: $("#rb-subcontractors").checked,
    },
    competencies:
      compMode === "structured" ? JSON.stringify(collectComp(), null, 2) : $("#pf-competencies").value,
    // Лицензии/опыт — часть формы профиля (BR-03): сохраняются только вместе
    // с профилем; лишние поля (id, license_type, …) сервер игнорирует.
    licenses: profileLicenses.map((l) => ({
      license_type_id: l.license_type_id,
      number: l.number,
      authority: l.authority,
      issue_date: l.issue_date,
      expiry_date: l.expiry_date,
      notes: l.notes,
    })),
    experience: profileExperience.map((e) => ({
      title: e.title,
      customer_name: e.customer_name,
      contract_number: e.contract_number,
      start_date: e.start_date,
      end_date: e.end_date,
      amount: e.amount,
      confirmation_type_id: e.confirmation_type_id,
      import_independent: e.import_independent,
      notes: e.notes,
    })),
  };
}

// Открыт ли редактор профиля кнопкой «Редактировать профиль» со вкладки
// «Закупки» (а не изнутри вкладки «Профили»): если да, закрытие редактора
// (сохранение ИЛИ отмена — оба пути идут через closeProfileEditor) должно
// вернуть пользователя обратно на «Закупки», а не оставлять на «Профили».
let returnToProcAfterEdit = false;

function closeProfileEditor() {
  $("#profile-editor").style.display = "none";
  $("#profiles").style.display = "";
  if (returnToProcAfterEdit) {
    returnToProcAfterEdit = false;
    switchTo("proc");
  }
}

async function openProfileEditor(id) {
  profileEditorId = id || null;
  let p = null;
  if (id) {
    try {
      p = await api(`clients/${id}`);
    } catch (e) {
      setProfileStatus("Ошибка загрузки профиля: " + e.message);
      return;
    }
    try {
      await loadProfileExtras(id);
    } catch (e) {
      setProfileStatus("Ошибка загрузки лицензий/опыта: " + e.message);
    }
  } else {
    profileLicenses = [];
    profileExperience = [];
  }
  fillProfileForm(p);
  updateProfileExtrasVisibility();
  $("#profiles").style.display = "none";
  $("#profile-editor").style.display = "block";
}

async function saveProfile() {
  const data = profileFormData();
  if (!data.name) {
    setProfileSaveStatus("Укажите имя профиля", true);
    setProfileStatus("Укажите имя профиля");
    return false;
  }
  const badOkpd = data.okpd_codes.filter((c) => !okpdIsValid(c));
  if (badOkpd.length) {
    const msg = `Код ОКПД2 «${badOkpd.join('», «')}» имеет неверный формат: цифры, разделённые точками (например, 62.02 или 62.02.20.110)`;
    setProfileSaveStatus(msg, true);
    setProfileStatus("Ошибка сохранения: " + msg);
    return false;
  }
  if (data.max_region_distance_km != null && data.max_region_distance_km < 0) {
    const msg = "Максимальное расстояние от центра региона не может быть отрицательным";
    setProfileSaveStatus(msg, true);
    setProfileStatus("Ошибка сохранения: " + msg);
    return false;
  }
  setProfileSaveStatus("");
  // Защита от случайной потери: в профиле были слова, а форма пустая.
  if (profileExclLoaded > 0 && data.exclusion_words.length === 0) {
    if (
      !(await confirmDialogAsync(
        `В профиле было слов-исключений: ${profileExclLoaded}, а сейчас список пуст. Сохранить пустой список (все исключения будут удалены)?`
      ))
    )
      return false;
  }
  if (profileKeywordsLoaded > 0 && data.keywords.length === 0) {
    if (
      !(await confirmDialogAsync(
        `В профиле было ключевых слов: ${profileKeywordsLoaded}, а сейчас список пуст. Сохранить пустой список (все слова будут удалены)?`
      ))
    )
      return false;
  }
  return doSaveProfile(data);
}

async function doSaveProfile(data) {
  try {
    const url = profileEditorId ? `/api/clients/${profileEditorId}` : "/api/clients";
    const method = profileEditorId ? "PUT" : "POST";
    const r = await apiJSON(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!r.ok) throw new Error(await apiErrorDetail(r));
    let notice = null;
    try {
      const saved = await r.json();
      notice = saved && saved.notice ? saved.notice : null;
    } catch (e) {
      // Тело может отсутствовать/не парситься — показываем общий текст.
    }
    snapshotProfile();
    const baseMsg = profileEditorId ? "Профиль сохранён" : "Профиль создан";
    setProfileSaveStatus(notice || baseMsg);
    setProfileStatus(notice || baseMsg);
    closeProfileEditor();
    await loadProfiles();
    return true;
  } catch (e) {
    const msg = "Ошибка сохранения: " + e.message;
    setProfileSaveStatus(msg, true);
    setProfileStatus(msg);
    return false;
  }
}

let deleteProfileId = null;

function confirmDeleteProfile(id, name) {
  deleteProfileId = id;
  $("#delete-profile-message").textContent = name
    ? `Удалить профиль «${name}»? Профиль будет удалён вместе со словами и оценками.`
    : "Удалить профиль?";
  $("#delete-profile-modal-bg").classList.add("open");
}

function closeDeleteProfileModal() {
  $("#delete-profile-modal-bg").classList.remove("open");
}

async function doDeleteProfile() {
  const id = deleteProfileId;
  if (id == null) return;
  closeDeleteProfileModal();
  try {
    const r = await apiJSON(`/api/clients/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(await r.text());
    setProfileStatus("Профиль удалён");
    if (profileEditorId === id) closeProfileEditor();
    await loadProfiles();
  } catch (e) {
    setProfileStatus("Ошибка удаления: " + e.message);
  }
}

async function importProfileFile() {
  const fileInput = $("#profile-import-file");
  const file = fileInput.files && fileInput.files[0];
  fileInput.value = "";
  if (!file) return;
  try {
    const text = await file.text();
    const r = await apiJSON("/api/clients/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text }),
    });
    if (!r.ok) {
      let msg = "не удалось загрузить";
      try {
        const d = await r.json();
        if (d && d.detail) msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
      } catch (e) {
        /* ignore */
      }
      setProfileStatus("Ошибка загрузки: " + msg);
      return;
    }
    let notice = null;
    try {
      const saved = await r.json();
      notice = saved && saved.notice ? saved.notice : null;
    } catch (e) {
      /* ignore */
    }
    setProfileStatus(notice || "Профиль загружен из файла «" + file.name + "»");
    await loadProfiles();
  } catch (e) {
    setProfileStatus("Ошибка загрузки: " + e.message);
  }
}

// Экспорт профиля в markdown-файл: выбор папки (File System Access) или
// обычное скачивание, если браузер не поддерживает выбор папки.
let exportProfileId = null;
let exportProfileName = "";

function openExportProfile(id, name) {
  exportProfileId = id;
  exportProfileName = name || "";
  $("#export-profile-name").textContent = exportProfileName
    ? `Профиль «${exportProfileName}»`
    : "Профиль";
  $("#export-profile-modal-bg").classList.add("open");
}

function closeExportProfileModal() {
  $("#export-profile-modal-bg").classList.remove("open");
  exportProfileId = null;
}

async function writeToDir(dirHandle, filename, content) {
  const fileHandle = await dirHandle.getFileHandle(filename, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(content);
  await writable.close();
}

function downloadProfileFile(content, filename) {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function doExportProfile() {
  const id = exportProfileId;
  if (id == null) return;
  closeExportProfileModal();
  setProfileStatus("Экспорт профиля…");
  // Папку выбираем до сетевого запроса — выбор папки требует свежего жеста
  // пользователя (transient activation), который не переживает await fetch.
  let dirHandle = null;
  let usePicker = false;
  if (window.showDirectoryPicker) {
    try {
      dirHandle = await window.showDirectoryPicker();
      usePicker = true;
    } catch (err) {
      if (err && err.name === "AbortError") {
        setProfileStatus("Экспорт отменён");
        return;
      }
      // Иначе браузер не дал доступ — падаем в обычное скачивание.
    }
  }
  try {
    const data = await api(`clients/${id}/export`);
    if (usePicker && dirHandle) {
      await writeToDir(dirHandle, data.profile_filename, data.profile_content);
      setProfileStatus(`Профиль сохранён в выбранную папку: ${data.profile_filename}`);
      return;
    }
    downloadProfileFile(data.profile_content, data.profile_filename);
    setProfileStatus("Профиль выгружен ✓");
  } catch (e) {
    setProfileStatus("Ошибка экспорта: " + e.message);
  }
}

async function switchClient(profileId) {
  try {
    const r = await apiJSON(`/api/clients/${profileId}/activate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!r.ok) throw new Error(await apiErrorDetail(r));
    await loadProfiles();
    await loadProc();
    await loadCustomers();
    await loadPlatforms();
  } catch (e) {
    alert("Не удалось переключить профиль: " + e.message);
    renderActiveProfileSelectors();
  }
}

function profileFormDirty() {
  return $("#profile-editor").style.display === "block" && isProfileDirty();
}

export {
  loadProfiles,
  loadActiveProfileSelector,
  switchClient,
  openProfileEditor,
  closeProfileEditor,
  isProfileDirty,
  profileFormDirty,
  confirmDeleteProfile,
  closeDeleteProfileModal,
  doDeleteProfile,
  closeExportProfileModal,
};

// Кнопка «Редактировать профиль» на вкладке «Закупки» (рядом с селектором
// активного профиля): открывает карточку ТЕКУЩЕГО активного профиля на
// вкладке «Профили» и запоминает, что нужно вернуться на «Закупки» после
// закрытия редактора (см. closeProfileEditor/returnToProcAfterEdit).
$("#proc-edit-profile")?.addEventListener("click", () => {
  const id = Number($("#proc-profile").value);
  if (!id) return;
  returnToProcAfterEdit = true;
  switchTo("profiles");
  openProfileEditor(id);
});

// Кнопка «Обновить сейчас» на вкладке «Закупки»: принудительный внеочередной
// обход активного профиля БЕЗ изменения самого профиля (POST /api/clients/
// {id}/refresh) — тот же throttle-путь и та же обратная связь (notice), что
// и у сохранения профиля с изменением критериев сбора; повторные нажатия до
// истечения throttle просто продлевают текст ожидания, не запускают новый обход.
$("#proc-refresh-profile")?.addEventListener("click", async () => {
  const id = Number($("#proc-profile").value);
  if (!id) return;
  const btn = $("#proc-refresh-profile");
  const status = $("#proc-refresh-status");
  btn.disabled = true;
  status.textContent = "";
  try {
    const r = await apiJSON(`/api/clients/${id}/refresh`, { method: "POST" });
    if (!r.ok) throw new Error(await apiErrorDetail(r));
    const saved = await r.json();
    status.textContent = (saved && saved.notice) || "Обновление запрошено";
  } catch (e) {
    status.textContent = "Ошибка: " + e.message;
  } finally {
    btn.disabled = false;
  }
});

$("#profile-new").addEventListener("click", () => openProfileEditor(null));
$("#profile-import").addEventListener("click", () => $("#profile-import-file").click());
$("#profile-import-file").addEventListener("change", importProfileFile);
$("#profile-save").addEventListener("click", saveProfile);
$("#profile-cancel").addEventListener("click", () => {
  if (isProfileDirty()) {
    confirmDialog(
      "В форме профиля есть несохранённые изменения. Покинуть форму? Изменения будут потеряны.",
      closeProfileEditor
    );
    return;
  }
  closeProfileEditor();
});
$("#profile-delete").addEventListener("click", () =>
  confirmDeleteProfile(profileEditorId, profileEditorName)
);
$("#delete-profile-cancel").addEventListener("click", closeDeleteProfileModal);
$("#delete-profile-confirm").addEventListener("click", doDeleteProfile);
$("#delete-profile-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "delete-profile-modal-bg") closeDeleteProfileModal();
});
$("#export-profile-cancel").addEventListener("click", closeExportProfileModal);
$("#export-profile-confirm").addEventListener("click", doExportProfile);
$("#export-profile-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "export-profile-modal-bg") closeExportProfileModal();
});
[
  "pf-tab-keywords",
  "pf-tab-excl",
  "pf-tab-comp",
  "pf-tab-platforms",
  "pf-tab-licenses",
  "pf-tab-experience",
  "pf-tab-report-fields",
].forEach(
  (id) => {
    document.getElementById(id).addEventListener("click", () =>
      switchProfileTab(id.replace("pf-tab-", ""))
    );
  }
);
$("#pf-competencies").addEventListener("input", wordCounts);
$("#pf-comp-mode").addEventListener("click", switchCompMode);
$("#pf-profile-from-url").addEventListener("click", fillProfileFromUrl);
$("#pf-comp-add").addEventListener("click", () => {
  compStructured.competencies.push({ area: "", description: "", examples: [] });
  renderCompList();
  wordCounts();
  syncEntryFormState();
});
$("#pf-comp-list").addEventListener("click", (e) => {
  const del = e.target.closest(".comp-del");
  if (!del) return;
  const item = del.closest(".comp-item");
  const idx = [...item.parentNode.children].indexOf(item);
  compStructured.competencies.splice(idx, 1);
  renderCompList();
  wordCounts();
  syncEntryFormState();
});
$("#pf-comp-structured").addEventListener("input", wordCounts);
$("#pf-comp-structured").addEventListener("change", syncEntryFormState);
$("#pf-enabled").addEventListener("change", () => {
  syncEntryFormState();
});
// Выбор активного профиля на вкладках «Закупки» и «В работе» (единый класс).
document.querySelectorAll("select.active-profile-select").forEach((sel) => {
  sel.addEventListener("change", onActiveProfileChange);
});
// Изменения полей профиля (имя, ОКПД2, НМЦК, чекбоксы, чипы слов/вопросов)
// пересчитывают доступность кнопок «Добавить лицензию/опыт».
$("#profile-editor").addEventListener("input", syncEntryFormState);
$("#profile-editor").addEventListener("change", syncEntryFormState);
$("#license-new").addEventListener("click", () => openLicenseForm(null));
$("#license-save").addEventListener("click", saveLicense);
$("#license-cancel").addEventListener("click", () => {
  $("#license-form").style.display = "none";
  syncEntryFormState();
});
$("#report-field-new").addEventListener("click", () => openReportFieldForm(null));
$("#report-field-save").addEventListener("click", saveReportField);
$("#report-field-cancel").addEventListener("click", () => {
  $("#report-field-form").style.display = "none";
  syncEntryFormState();
});
$("#rf-type").addEventListener("change", updateReportFieldUnitVisibility);
$("#rf-cond-op").addEventListener("change", updateConditionValueVisibility);
$("#experience-new").addEventListener("click", () => openExperienceForm(null));
$("#experience-save").addEventListener("click", saveExperience);
$("#experience-cancel").addEventListener("click", () => {
  $("#experience-form").style.display = "none";
  syncEntryFormState();
});
bindTagInput("#pf-keywords-tags", profileKeywords);
bindTagInput("#pf-excl-tags", profileExcl);
bindTagFilter("#pf-keywords-tags", profileKeywords);
bindTagFilter("#pf-excl-tags", profileExcl);
bindOkpdTagInput();
bindTagInput("#pf-regions-tags", profileRegions);
