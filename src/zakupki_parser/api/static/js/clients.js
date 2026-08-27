"use strict";

// Вкладка «Профили»: список, редактор профиля (слова/компетенции/вопросы),
// лицензии и подтверждённый опыт (BR-03), переключение активного клиента.
import { $, escapeHtml, fmtMoney, splitWords } from "./utils.js";
import { api, apiJSON } from "./api.js";
import { confirmDialog, confirmDialogAsync } from "./dialogs.js";
import { loadProc, loadPlatforms } from "./procurements.js";
import { loadCustomers } from "./customers.js";

let profileEditorId = null;
let profileEditorName = "";
// Общее число профилей пользователя (для запрета удаления последнего).
let profilesTotal = 0;
let profileKeywords = [];
let profileExcl = [];
let profileQuestions = [];
let profileKeywordsLoaded = 0;
let profileExclLoaded = 0;
let profileQuestionsLoaded = 0;
let questionSeq = 1;
// Площадки, используемые профилем (target_etp): выбираются из активных.
let profilePlatforms = [];
let platformCatalog = [];
let platformsLoaded = false;
// Компетенции профиля: структурированная форма (JSON, модель scoring Profile)
// либо режим сырого текста (legacy markdown/JSON).
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
// Обязательные системные проверки ТЗ (read-only; источник — analysis_service).
const SYSTEM_QUESTIONS_UI = [
  { id: "sys:exp_2571", text: "Опыт исполнения контрактов (ПП РФ 2571)" },
  { id: "sys:minprom_registry", text: "Реестр Минпромторга" },
  { id: "sys:license_sro", text: "Лицензии / СРО / допуски" },
];
function renderSystemQuestions() {
  const box = $("#pf-system-questions");
  if (!box) return;
  const tagsEl = box.querySelector(".tags");
  tagsEl.innerHTML = "";
  SYSTEM_QUESTIONS_UI.forEach((q) => {
    const tag = document.createElement("span");
    tag.className = "tag tag-system";
    tag.textContent = q.text;
    tag.title = "Обязательная системная проверка (не редактируется)";
    tagsEl.appendChild(tag);
  });
}
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
// Временные id для новых записей лицензий/опыта в форме (до сохранения профиля).
let localEntrySeq = 0;

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
      else if (btn.dataset.action === "delete") confirmDeleteProfile(id, tr.dataset.name);
    });
  });
}

async function loadProfiles() {
  try {
    const list = await api("clients", { limit: 500 });
    profilesTotal = list.total;
    renderProfiles(list);
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
    td.textContent = "Площадки не выбраны";
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

// Отключённый профиль не может быть активным: снимаем и блокируем «активный».
function syncEnabledActiveState() {
  const enabled = $("#pf-enabled").checked;
  $("#pf-active").disabled = !enabled;
  if (!enabled) $("#pf-active").checked = false;
}

function fillProfileForm(p) {
  profileEditorName = p ? p.name : "";
  profileKeywordsLoaded = (p ? p.keywords || [] : []).length;
  profileExclLoaded = (p ? p.exclusion_words || [] : []).length;
  profileQuestionsLoaded = (p ? p.questions || [] : []).length;
  profilePlatforms = (p ? p.target_etp || [] : []).slice();
  // Каталог перечитываем при каждом открытии профиля: реактивированные на
  // панели «Аналитика» площадки должны снова появиться в таблице.
  platformsLoaded = false;
  ensurePlatformCatalog().then(renderPlatformTable);
  $("#profile-editor-name").textContent = p ? `#${p.id} «${p.name}»` : "новый";
  $("#pf-name").value = p ? p.name : "";
  $("#pf-enabled").checked = p ? p.enabled : true;
  $("#pf-active").checked = p ? p.is_active : false;
  syncEnabledActiveState();
  $("#pf-okpd").value = (p ? p.okpd_codes || [] : []).join(", ");
  $("#pf-nmck-min").value = p && p.nmck_min != null ? p.nmck_min : "";
  $("#pf-nmck-max").value = p && p.nmck_max != null ? p.nmck_max : "";
  profileKeywords.length = 0;
  (p ? p.keywords || [] : []).forEach((w) => profileKeywords.push(w));
  profileExcl.length = 0;
  (p ? p.exclusion_words || [] : []).forEach((w) => profileExcl.push(w));
  profileQuestions.length = 0;
  (p ? p.questions || [] : []).forEach((q) =>
    profileQuestions.push({ id: q.id || `q${questionSeq++}`, text: q.text || "" })
  );
  questionSeq = Math.max(
    questionSeq,
    ...profileQuestions.map((q) => (parseInt(String(q.id).replace(/\D/g, ""), 10) || 0) + 1)
  );
  renderTags(profileKeywords, "#pf-keywords-tags");
  renderTags(profileExcl, "#pf-excl-tags");
  renderTags(profileQuestions, "#pf-questions-tags");
  renderSystemQuestions();
  // Компетенции: структурированная JSON-форма либо legacy-текст.
  const rawComp = p ? p.competencies || "" : "";
  const parsedComp = parseComp(rawComp);
  if (parsedComp) {
    compStructured = parsedComp;
    compMode = "structured";
  } else {
    compMode = "raw";
    $("#pf-competencies").value = rawComp;
  }
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

function renderTags(arr, wrapId) {
  const box = $(wrapId);
  if (!box) return;
  const tagsEl = box.querySelector(".tags");
  const fallback = box.querySelector(".tags-fallback");
  const label = (item) => (typeof item === "object" && item != null ? item.text : item);
  try {
    tagsEl.innerHTML = "";
    arr.forEach((item, i) => {
      const w = String(label(item));
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
  // Запасной вывод: если чипы не отрисовались, показываем список словами,
  // чтобы слова никогда не «исчезали» из формы.
  if (arr.length && tagsEl.childElementCount !== arr.length) {
    fallback.textContent = arr.map(label).join("; ");
    fallback.style.display = "";
    tagsEl.style.display = "none";
  } else {
    fallback.style.display = "none";
    tagsEl.style.display = "";
  }
}

function bindTagInput(wrapId, arr, makeItem) {
  const input = $(wrapId).querySelector("input");
  const label = (item) => (typeof item === "object" && item != null ? item.text : item);
  // Страховка: при фокусе пере-рисуем чипы, чтобы они не могли остаться скрытыми.
  input.addEventListener("focus", () => {
    renderTags(arr, wrapId);
    wordCounts();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const parts = input.value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return;
    parts.forEach((w) => {
      const item = makeItem ? makeItem(w) : w;
      const key = String(label(item)).toLocaleLowerCase();
      if (!arr.some((x) => String(label(x)).toLocaleLowerCase() === key)) arr.push(item);
    });
    input.value = "";
    renderTags(arr, wrapId);
    wordCounts();
    syncEntryFormState();
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

function wordCounts() {
  setWordCount($("#pf-cnt-keywords"), profileKeywords.length);
  setWordCount($("#pf-cnt-excl"), profileExcl.length);
  setWordCount($("#pf-cnt-questions"), profileQuestions.length);
  setWordCount($("#pf-cnt-comp"), compCount());
  // Счётчик площадок — только видимые (активные) строки таблицы профиля.
  const catalogMap = new Map(platformCatalog.map((p) => [p.platform_id, p]));
  setWordCount(
    $("#pf-cnt-platforms"),
    profilePlatforms.filter((id) => {
      const p = catalogMap.get(id);
      return p && p.enabled;
    }).length
  );
  setWordCount($("#pf-cnt-licenses"), profileLicenses.length);
  setWordCount($("#pf-cnt-experience"), profileExperience.length);
}

function switchProfileTab(name) {
  ["keywords", "excl", "questions", "comp", "platforms", "licenses", "experience"].forEach((k) => {
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
    $("#experience-form").style.display === "block"
  );
}
function syncEntryFormState() {
  const formOpen = entryFormOpen();
  [$("#license-new"), $("#experience-new")].forEach((b) => {
    b.disabled = formOpen;
    b.title = "";
  });
  document
    .querySelectorAll("#licenses-table button[data-action], #experience-table button[data-action]")
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
      const id = Number(btn.closest("tr").dataset.id);
      if (btn.dataset.action === "edit") openLicenseForm(id);
      else deleteLicense(profileLicenses.find((x) => x.id === id));
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
      const id = Number(btn.closest("tr").dataset.id);
      if (btn.dataset.action === "edit") openExperienceForm(id);
      else deleteExperience(profileExperience.find((x) => x.id === id));
    });
  });
  syncEntryFormState();
}

async function openLicenseForm(id) {
  licenseEditorId = id || null;
  await ensureLicenseTypes();
  const l = id ? profileLicenses.find((x) => x.id === id) : null;
  const sel = $("#lic-type");
  sel.innerHTML = licenseTypes
    .map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`)
    .join("");
  sel.value = l ? l.license_type_id : licenseTypes.length ? licenseTypes[0].id : "";
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
  const type = lic ? licenseTypes.find((x) => x.id === lic.license_type_id) || null : null;
  const label =
    lic && (type || lic.number)
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
  const label = exp && exp.title ? `Удалить запись опыта «${exp.title}»?` : "Удалить запись опыта?";
  confirmDialog(label, () => {
    profileExperience = profileExperience.filter((x) => x.id !== exp.id);
    renderExperience();
    wordCounts();
    syncEntryFormState();
    setExperienceStatus("Опыт удалён — будет записано вместе с профилем");
  });
}

function profileFormData() {
  return {
    name: $("#pf-name").value.trim(),
    enabled: $("#pf-enabled").checked,
    is_active: $("#pf-active").checked,
    okpd_codes: splitWords($("#pf-okpd").value),
    nmck_min: $("#pf-nmck-min").value === "" ? null : Number($("#pf-nmck-min").value),
    nmck_max: $("#pf-nmck-max").value === "" ? null : Number($("#pf-nmck-max").value),
    target_etp: profilePlatforms.slice(),
    keywords: profileKeywords.slice(),
    exclusion_words: profileExcl.slice(),
    questions: profileQuestions.slice(),
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

function closeProfileEditor() {
  $("#profile-editor").style.display = "none";
  $("#profiles").style.display = "";
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
    setProfileStatus("Укажите имя профиля");
    return false;
  }
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
  if (profileQuestionsLoaded > 0 && data.questions.length === 0) {
    if (
      !(await confirmDialogAsync(
        `В профиле было вопросов по ТЗ: ${profileQuestionsLoaded}, а сейчас список пуст. Сохранить пустой список (все вопросы будут удалены)?`
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
    if (!r.ok) throw new Error(await r.text());
    snapshotProfile();
    setProfileStatus(profileEditorId ? "Профиль сохранён" : "Профиль создан");
    closeProfileEditor();
    await loadProfiles();
    await loadActiveClient();
    return true;
  } catch (e) {
    setProfileStatus("Ошибка сохранения: " + e.message);
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
    await loadActiveClient();
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
    setProfileStatus("Профиль загружен из файла «" + file.name + "»");
    await loadProfiles();
    await loadActiveClient();
  } catch (e) {
    setProfileStatus("Ошибка загрузки: " + e.message);
  }
}

// Селектор активного клиентского профиля в шапке (мультиклиентный скоринг).
async function loadActiveClient() {
  try {
    const list = await api("clients", { limit: 500 });
    const sel = $("#client-select");
    const cur = sel.value;
    sel.innerHTML = "";
    for (const p of list.items) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.name + (p.is_active ? " (активный)" : "");
      sel.appendChild(opt);
    }
    const c = await api("clients/active");
    sel.value = String(c.id);
    $("#client-switch").style.display = "inline-flex";
    if (cur && cur !== String(c.id)) loadProfiles();
  } catch (err) {
    $("#client-switch").style.display = "none";
  }
}

async function switchClient(profileId) {
  try {
    const r = await apiJSON(`/api/clients/${profileId}/activate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!r.ok) throw new Error(await r.text());
    await loadActiveClient();
    await loadProfiles();
    await loadProc();
    await loadCustomers();
    await loadPlatforms();
  } catch (e) {
    alert("Не удалось переключить профиль: " + e.message);
  }
}

function profileFormDirty() {
  return $("#profile-editor").style.display === "block" && isProfileDirty();
}

export {
  loadProfiles,
  loadActiveClient,
  switchClient,
  openProfileEditor,
  closeProfileEditor,
  isProfileDirty,
  profileFormDirty,
  confirmDeleteProfile,
  closeDeleteProfileModal,
  doDeleteProfile,
};

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
["pf-tab-keywords", "pf-tab-excl", "pf-tab-questions", "pf-tab-comp", "pf-tab-platforms", "pf-tab-licenses", "pf-tab-experience"].forEach(
  (id) => {
    document.getElementById(id).addEventListener("click", () =>
      switchProfileTab(id.replace("pf-tab-", ""))
    );
  }
);
$("#pf-competencies").addEventListener("input", wordCounts);
$("#pf-comp-mode").addEventListener("click", switchCompMode);
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
  syncEnabledActiveState();
  syncEntryFormState();
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
$("#experience-new").addEventListener("click", () => openExperienceForm(null));
$("#experience-save").addEventListener("click", saveExperience);
$("#experience-cancel").addEventListener("click", () => {
  $("#experience-form").style.display = "none";
  syncEntryFormState();
});
bindTagInput("#pf-keywords-tags", profileKeywords);
bindTagInput("#pf-excl-tags", profileExcl);
bindTagInput("#pf-questions-tags", profileQuestions, (w) => ({ id: "q" + questionSeq++, text: w }));
$("#client-select").addEventListener("change", (ev) => {
  if (ev.target.value) switchClient(Number(ev.target.value));
});
