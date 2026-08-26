"use strict";

// Вкладка «Справочники» (только для администратора): инлайн-редактор справочных
// таблиц. Список таблиц и колонок приходит с бэкенда (/api/reference), поэтому
// подключение новой справочной таблицы не требует правки фронтенда.
import { $, escapeHtml } from "./utils.js";
import { api, apiJSON } from "./api.js";

let refTables = [];
let current = null; // { key, title, columns }
let rows = []; // [{ id, origin, values }]

function emptyValues() {
  const values = {};
  current.columns.forEach((c) => {
    values[c.key] = c.type === "boolean" ? false : c.type === "integer" ? 0 : "";
  });
  return values;
}

function rowHtml(r, i) {
  const cells = current.columns
    .map((c) => {
      const v = r.values[c.key] ?? "";
      if (c.type === "integer") {
        return `<td><input type="number" step="1" data-col="${c.key}" data-idx="${i}" value="${escapeHtml(v)}"></td>`;
      }
      if (c.type === "boolean") {
        const checked = r.values[c.key] ? " checked" : "";
        return `<td><input type="checkbox" data-col="${c.key}" data-idx="${i}"${checked}></td>`;
      }
      return `<td><input type="text" data-col="${c.key}" data-idx="${i}" value="${escapeHtml(v)}"></td>`;
    })
    .join("");
  const idCell =
    r.id == null
      ? '<td class="muted">— <span style="font-size:11px">новое</span></td>'
      : `<td class="muted">#${r.id}</td>`;
  return `<tr data-rowidx="${i}" class="${r.id == null ? "ref-new" : ""}">
    <td><input type="checkbox" class="ref-row-sel" data-id="${r.id == null ? "" : r.id}"></td>
    ${idCell}
    ${cells}
  </tr>`;
}

function renderRef() {
  if (!current) return;
  const ths = [
    '<th><input type="checkbox" id="ref-sel-all" title="Выбрать все"></th>',
    "<th>ID</th>",
  ]
    .concat(current.columns.map((c) => `<th>${escapeHtml(c.label)}</th>`))
    .join("");
  $("#ref-head").innerHTML = `<tr>${ths}</tr>`;
  const tbody = $("#ref-rows");
  if (!rows.length) {
    tbody.innerHTML = "";
    $("#ref-empty").style.display = "";
    updateDeleteBtn();
    return;
  }
  $("#ref-empty").style.display = "none";
  tbody.innerHTML = rows.map((r, i) => rowHtml(r, i)).join("");
  const selAll = $("#ref-sel-all");
  if (selAll) {
    selAll.onchange = () => {
      document
        .querySelectorAll("#ref-rows .ref-row-sel")
        .forEach((cb) => {
          cb.checked = selAll.checked;
        });
      updateDeleteBtn();
    };
  }
  updateDeleteBtn();
}

function collectRow(i) {
  const tr = $("#ref-rows").querySelector(`tr[data-rowidx="${i}"]`);
  const values = {};
  current.columns.forEach((c) => {
    const input = tr.querySelector(`input[data-col="${c.key}"]`);
    if (!input) return;
    if (c.type === "boolean") values[c.key] = input.checked;
    else if (c.type === "integer") {
      const s = String(input.value).trim();
      const n = s === "" ? 0 : Number(s);
      values[c.key] = Number.isInteger(n) ? n : NaN;
    } else values[c.key] = input.value;
  });
  return values;
}

// Сравнение через String() безвредно для всех типов колонок (числа, bool, текст).
function isChanged(r, values) {
  return current.columns.some(
    (c) => String(r.origin[c.key] ?? "") !== String(values[c.key] ?? "")
  );
}

// Валидность строки: целые колонки должны быть целыми числами (не NaN).
function rowInvalid(values) {
  for (const c of current.columns) {
    if (c.type === "integer" && !Number.isInteger(values[c.key])) {
      return `Поле «${c.label}» должно быть целым числом`;
    }
  }
  return null;
}

function refDirty() {
  if (!current) return false;
  return rows.some((r, i) => {
    if (r.id == null) return true;
    return isChanged(r, collectRow(i));
  });
}

function updateDeleteBtn() {
  const any = document.querySelector("#ref-rows .ref-row-sel:checked");
  $("#ref-delete").disabled = !any;
  const selAll = $("#ref-sel-all");
  if (selAll) {
    const boxes = [...document.querySelectorAll("#ref-rows .ref-row-sel")];
    selAll.checked = boxes.length > 0 && boxes.every((cb) => cb.checked);
  }
}

async function loadRefTables() {
  refTables = await api("reference");
  const sel = $("#ref-table");
  const prev = sel.value;
  sel.innerHTML = "";
  refTables.forEach((t) => {
    const o = document.createElement("option");
    o.value = t.key;
    o.textContent = t.title;
    sel.appendChild(o);
  });
  current = refTables.find((t) => t.key === prev) || refTables[0] || null;
  $("#ref-status").textContent = current ? current.title : "справочники не настроены";
  await loadRefRows();
}

async function loadRefRows() {
  if (!current) {
    $("#ref-head").innerHTML = "";
    $("#ref-rows").innerHTML = "";
    $("#ref-empty").style.display = "";
    return;
  }
  const data = await api("reference/" + encodeURIComponent(current.key));
  rows = data.items.map((r) => ({ id: r.id, origin: r, values: Object.assign({}, r) }));
  renderRef();
}

function addRow() {
  if (!current) return;
  rows.push({ id: null, origin: {}, values: emptyValues() });
  renderRef();
  const last = $("#ref-rows").querySelector("tr:last-child input[data-col]");
  if (last) last.focus();
}

function statusText(msg) {
  $("#ref-actions-status").textContent = msg;
}

async function saveChanges() {
  if (!current || !rows.length) return;
  const saveBtn = $("#ref-save");
  const delBtn = $("#ref-delete");
  saveBtn.disabled = true;
  delBtn.disabled = true;
  try {
    const ops = [];
    let invalidMsg = null;
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      const values = collectRow(i);
      const bad = rowInvalid(values);
      if (bad) {
        invalidMsg = bad;
        break;
      }
      if (r.id == null) ops.push({ method: "POST", i, values });
      else if (isChanged(r, values)) ops.push({ method: "PUT", i, id: r.id, values });
    }
    if (invalidMsg) {
      statusText(invalidMsg);
      return;
    }
    if (!ops.length) {
      statusText("Изменений нет");
      return;
    }
    statusText("Сохранение…");
    let ok = 0;
    for (const op of ops) {
      const url =
        "/api/reference/" +
        encodeURIComponent(current.key) +
        (op.id != null ? "/" + op.id : "");
      const r = await apiJSON(url, {
        method: op.method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(op.values),
      });
      if (r.status === 401) return;
      if (!r.ok) {
        let msg = "ошибка";
        try {
          const d = await r.json();
          if (d && d.detail)
            msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
        } catch (e) {}
        statusText("Не сохранено: " + msg);
        return;
      }
      // Синхронизируем id/origin успешно сохранённых строк: повторный клик
      // не переотправит их и не создаст дубликат после частичного сбоя.
      const saved = await r.json();
      rows[op.i].id = saved.id;
      rows[op.i].origin = saved;
      rows[op.i].values = Object.assign({}, saved);
      ok++;
    }
    statusText(ok ? `Сохранено: ${ok}` : "Изменений нет");
    await loadRefRows();
  } finally {
    saveBtn.disabled = false;
    updateDeleteBtn();
  }
}

async function deleteSelected() {
  if (!current) return;
  const checked = [...document.querySelectorAll("#ref-rows .ref-row-sel:checked")];
  if (!checked.length) return;
  if (!confirm(`Удалить ${checked.length} запис(ь/и)?`)) return;
  const saveBtn = $("#ref-save");
  const delBtn = $("#ref-delete");
  saveBtn.disabled = true;
  delBtn.disabled = true;
  try {
    statusText("Удаление…");
    const removeIds = new Set();
    const removeNewIdx = new Set();
    let removed = 0;
    for (const cb of checked) {
      const tr = cb.closest("tr");
      const id = cb.dataset.id;
      if (!id) {
        // Несохранённая (новая) строка — убираем из редактора без запроса.
        if (tr) removeNewIdx.add(Number(tr.dataset.rowidx));
        removed++;
        continue;
      }
      const r = await apiJSON(
        "/api/reference/" + encodeURIComponent(current.key) + "/" + id,
        { method: "DELETE" }
      );
      if (r.status === 401) return;
      if (!r.ok) {
        let msg = "ошибка";
        try {
          const d = await r.json();
          if (d && d.detail)
            msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
        } catch (e) {}
        statusText("Не удалено: " + msg);
        // Ресинхронизация с сервером: уже удалённые строки не должны попасть
        // в повторную попытку (иначе 404).
        await loadRefRows();
        return;
      }
      removeIds.add(Number(id));
      removed++;
    }
    rows = rows.filter((row, idx) =>
      row.id == null ? !removeNewIdx.has(idx) : !removeIds.has(row.id)
    );
    statusText(removed ? `Удалено: ${removed}` : "Ничего не выбрано");
    updateDeleteBtn();
    await loadRefRows();
  } finally {
    saveBtn.disabled = false;
    updateDeleteBtn();
  }
}

async function reloadChanges() {
  if (!current) return;
  if (refDirty() && !confirm("Есть несохранённые изменения — отменить их?")) return;
  statusText("");
  await loadRefRows();
}

export { loadRefTables, refDirty };

$("#ref-table").addEventListener("change", async () => {
  const key = $("#ref-table").value;
  if (refDirty() && !confirm("Есть несохранённые изменения — переключить таблицу?")) {
    $("#ref-table").value = current ? current.key : "";
    return;
  }
  current = refTables.find((t) => t.key === key) || null;
  if (current) await loadRefRows();
});
$("#ref-add").addEventListener("click", addRow);
$("#ref-save").addEventListener("click", saveChanges);
$("#ref-reload").addEventListener("click", reloadChanges);
$("#ref-delete").addEventListener("click", deleteSelected);
$("#ref-rows").addEventListener("change", updateDeleteBtn);
