"use strict";

// Универсальный рендер форм по схеме конфигурации (см. /api/config/*/schema).
// Поддерживает типы: bool, int, float, str, text, select, tags (list[str]),
// object (вложенные секции), list (таблица записей модели).

function checkbox(value, readOnly) {
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = Boolean(value);
  cb.disabled = Boolean(readOnly);
  return cb;
}

function numberInput(value, step, readOnly) {
  const input = document.createElement("input");
  input.type = "number";
  if (step) input.step = step;
  input.value = value == null ? "" : String(value);
  input.disabled = Boolean(readOnly);
  return input;
}

function textInput(value, readOnly) {
  const input = document.createElement("input");
  input.type = "text";
  input.value = value == null ? "" : String(value);
  input.disabled = Boolean(readOnly);
  return input;
}

function selectInput(value, options, readOnly) {
  const sel = document.createElement("select");
  options.forEach((opt) => {
    const o = document.createElement("option");
    if (typeof opt === "object" && opt !== null) {
      o.value = String(opt.value);
      o.textContent = String(opt.label);
    } else {
      o.value = String(opt);
      o.textContent = String(opt);
    }
    sel.appendChild(o);
  });
  if (value != null) sel.value = String(value);
  sel.disabled = Boolean(readOnly);
  return sel;
}

// --- tags: list[str] (Enter — добавить, × — удалить) --------------------
function renderTags(value, readOnly) {
  const wrap = document.createElement("div");
  wrap.className = "tag-input";
  wrap.style.alignItems = "stretch";
  const tags = document.createElement("div");
  tags.className = "tags";
  const addChip = (word) => {
    const chip = document.createElement("span");
    chip.className = "tag";
    chip.textContent = word;
    if (!readOnly) {
      const x = document.createElement("span");
      x.className = "x";
      x.textContent = "×";
      x.onclick = () => chip.remove();
      chip.appendChild(x);
    }
    tags.appendChild(chip);
  };
  (value || []).forEach(addChip);
  wrap.appendChild(tags);
  if (!readOnly) {
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "значение, Enter";
    input.onkeydown = (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const v = input.value.trim();
      if (!v) return;
      addChip(v);
      input.value = "";
    };
    wrap.appendChild(input);
  }
  return wrap;
}

// --- control: сам элемент ввода по типу поля ----------------------------
function makeControl(field, value, readOnly) {
  let input;
  switch (field.kind) {
    case "bool":
      input = checkbox(value, readOnly);
      break;
    case "int":
      input = numberInput(value, "", readOnly);
      break;
    case "float":
      input = numberInput(value, "any", readOnly);
      break;
    case "select":
      input = selectInput(value, field.options || [], readOnly);
      break;
    case "text": {
      input = document.createElement("textarea");
      input.value = value == null ? "" : String(value);
      input.disabled = Boolean(readOnly);
      break;
    }
    case "tags":
      input = renderTags(value, readOnly);
      break;
    default: // str
      input = textInput(value, readOnly);
  }
  return input;
}

// --- list: таблица записей модели (sites и т.п.) -------------------------
// Поле списка может содержать «производные» колонки (``derived``) — значения,
// вычисляемые из выбранного значения другой колонки (например, name/url из
// платформы): рендерятся как текст/ссылка и не попадают в сохраняемый объект.
function renderDerived(sub, opt) {
  const value = opt ? String(opt[sub.field] ?? "") : "";
  if (sub.field === "url") {
    const a = document.createElement("a");
    a.href = value;
    a.textContent = value;
    a.target = "_blank";
    a.rel = "noopener";
    return a;
  }
  const span = document.createElement("span");
  span.className = "muted";
  span.textContent = value;
  return span;
}

function renderList(field, value, readOnly, path) {
  const wrap = document.createElement("div");
  const table = document.createElement("table");
  table.className = "cfg-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  field.item.forEach((sub) => {
    const th = document.createElement("th");
    th.textContent = sub.label;
    headRow.appendChild(th);
  });
  if (!readOnly) {
    const th = document.createElement("th");
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  table.appendChild(tbody);

  const derived = field.item.find((s) => s.derived);
  const sourceField = derived ? field.item.find((s) => s.key === derived.derived) : null;
  const optionMap = new Map();
  if (sourceField && Array.isArray(sourceField.options)) {
    sourceField.options.forEach((o) => optionMap.set(String(o.value), o));
  }

  const addRow = (row, index) => {
    const tr = document.createElement("tr");
    tr.dataset.listRow = "1";
    field.item.forEach((sub) => {
      const td = document.createElement("td");
      const rawValue = row ? row[sub.key] : sub.default;
      if (sub.derived) {
        const source = row ? row[sub.derived] : sourceField.default;
        const el = renderDerived(sub, optionMap.get(String(source)));
        el.dataset.displayFor = sub.derived;
        el.dataset.displayField = sub.field;
        td.appendChild(el);
        tr.appendChild(td);
        return;
      }
      if (sub.plain) {
        const cellWrap = document.createElement("div");
        cellWrap.dataset.path = `${path}[${index}].${sub.key}`;
        const span = document.createElement("span");
        span.textContent = rawValue == null ? "" : String(rawValue);
        cellWrap.appendChild(span);
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.value = rawValue == null ? "" : String(rawValue);
        cellWrap.appendChild(hidden);
        td.appendChild(cellWrap);
        tr.appendChild(td);
        return;
      }
      const cellWrap = document.createElement("div");
      cellWrap.dataset.path = `${path}[${index}].${sub.key}`;
      if (sub.description) cellWrap.title = sub.description;
      const control = makeControl(sub, rawValue, readOnly);
      cellWrap.appendChild(control);
      td.appendChild(cellWrap);
      tr.appendChild(td);
      if (sub.kind === "select" && !readOnly) {
        control.addEventListener("change", () => {
          const opt = optionMap.get(control.value);
          tr.querySelectorAll("[data-display-for]").forEach((el) => {
            const v = opt ? String(opt[el.dataset.displayField] ?? "") : "";
            if (el.tagName === "A") {
              el.href = v;
              el.textContent = v;
            } else {
              el.textContent = v;
            }
          });
        });
      }
    });
    if (!readOnly) {
      const td = document.createElement("td");
      const del = document.createElement("button");
      del.className = "ghost btn-mini";
      del.textContent = "×";
      del.title = "Удалить строку";
      del.onclick = () => tr.remove();
      td.appendChild(del);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  };
  (value || []).forEach((row, i) => addRow(row, i));
  wrap.appendChild(table);
  if (!readOnly && field.addable !== false) {
    const addBtn = document.createElement("button");
    addBtn.className = "ghost btn-mini";
    addBtn.textContent = "+ добавить";
    addBtn.onclick = () => addRow(null, tbody.children.length);
    wrap.appendChild(addBtn);
  }
  return wrap;
}

// --- object: вложенная секция (fieldset) ---------------------------------
function renderObject(field, value, readOnly, path) {
  const wrap = document.createElement("div");
  wrap.className = "cfg-section";
  const title = document.createElement("div");
  title.className = "cfg-section-title";
  title.textContent = field.label;
  wrap.appendChild(title);
  const grid = document.createElement("div");
  grid.className = field.stack ? "cfg-fields cfg-fields-stack" : "cfg-fields";
  field.fields.forEach((sub) => {
    grid.appendChild(renderInput(sub, value ? value[sub.key] : sub.default, readOnly, path + "." + sub.key));
  });
  wrap.appendChild(grid);
  return wrap;
}

function renderInput(field, value, readOnly, path) {
  path = path || field.key;
  const wrap = document.createElement("div");
  wrap.className =
    "cfg-field" +
    (field.kind === "bool" ? " cfg-check" : "") +
    (field.inline ? " cfg-inline" : "");
  wrap.dataset.path = path;

  if (field.kind === "object") {
    wrap.appendChild(renderObject(field, value, readOnly, path));
    return wrap;
  }
  if (field.kind === "list") {
    wrap.appendChild(renderList(field, value, readOnly, path));
    return wrap;
  }

  const label = document.createElement("span");
  label.textContent = field.label;
  wrap.appendChild(label);
  wrap.appendChild(makeControl(field, value, readOnly));
  if (field.description) wrap.title = field.description;
  return wrap;
}

export function renderSchemaForm(container, schema, values, opts) {
  container.innerHTML = "";
  const readOnly = !!(opts && opts.readOnly);
  const form = document.createElement("div");
  form.className = "cfg-form";
  // Группировка по смыслу: последовательные поля с одинаковым ``group``
  // оборачиваются в секцию (css .cfg-section/.cfg-fields).
  let group = null;
  let sectionFields = null;
  schema.forEach((field) => {
    const g = field.group || null;
    if (g !== group) {
      group = g;
      sectionFields = null;
      if (group) {
        const section = document.createElement("div");
        section.className = "cfg-section";
        const title = document.createElement("div");
        title.className = "cfg-section-title";
        title.textContent = group;
        section.appendChild(title);
        const grid = document.createElement("div");
        grid.className = "cfg-fields";
        section.appendChild(grid);
        form.appendChild(section);
        sectionFields = grid;
      }
    }
    const target = sectionFields || form;
    target.appendChild(renderInput(field, values ? values[field.key] : undefined, readOnly));
  });
  container.appendChild(form);
}

// --- сбор значений ------------------------------------------------------
function setByPath(obj, path, value) {
  const parts = path.split(".");
  let cur = obj;
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    const arrMatch = part.match(/^(.*)\[(\d+)\]$/);
    const key = arrMatch ? arrMatch[1] : part;
    if (i === parts.length - 1) {
      cur[key] = value;
      return;
    }
    if (arrMatch) {
      const index = Number(arrMatch[2]);
      cur[key] = cur[key] || [];
      cur[key][index] = cur[key][index] || {};
      cur = cur[key][index];
    } else {
      cur[key] = cur[key] || {};
      cur = cur[key];
    }
  }
}

// Собирает значения отрендеренной формы в иерархический объект (по data-path).
export function collectSchemaValues(container) {
  const result = {};
  container.querySelectorAll("[data-path]").forEach((el) => {
    if (el.querySelector("[data-path]")) return; // контейнеры обрабатываются по листьям
    setByPath(result, el.dataset.path, readFieldValue(el));
  });
  return result;
}

function readFieldValue(el) {
  const input = el.querySelector("input[type='checkbox']");
  if (input) return input.checked;
  const number = el.querySelector("input[type='number']");
  if (number) return number.value === "" ? null : Number(number.value);
  const select = el.querySelector("select");
  if (select) return select.value;
  const textarea = el.querySelector("textarea");
  if (textarea) return textarea.value;
  const hidden = el.querySelector("input[type='hidden']");
  if (hidden) return hidden.value;
  const tags = el.querySelector(".tags");
  if (tags) {
    return [...tags.querySelectorAll(".tag")].map((t) => t.childNodes[0].textContent);
  }
  const text = el.querySelector("input[type='text']");
  if (text) return text.value;
  return null;
}
