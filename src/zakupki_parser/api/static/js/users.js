"use strict";

// Вкладка «Пользователи» (роль admin): список пользователей, создание (роли
// admin/analyst/devops), смена ролей (кроме простых и себя), блокировка/
// разблокировка и удаление (нельзя — себя и последнего admin).
import { $, escapeHtml, fmtDT } from "./utils.js";
import { state } from "./store.js";
import { api, apiJSON, apiErrorDetail } from "./api.js";

const ROLE_LABELS = {
  user: "Пользователь",
  admin: "Администратор",
  analyst: "Аналитик",
  devops: "DevOps",
};

const ASSIGNABLE_ROLES = ["admin", "analyst", "devops"];

let users = [];
let userModalMode = "create"; // "create" | "roles"
let userModalTarget = null;
// Админ-редактор аккаунтов другого пользователя (модалка «Опции»).
let uaData = null; // { user, accounts, active_account_id, trial, catalog }
let uaOptions = {}; // текущие значения чекбоксов активного аккаунта

function closeUserModal() {
  $("#user-modal-bg").classList.remove("open");
  $("#user-error").textContent = "";
}

async function loadUsers() {
  const data = await api("users");
  users = data.items;
  renderUsers();
}

function rolePills(roles) {
  return roles.map((r) => `<span class="role-pill">${escapeHtml(ROLE_LABELS[r] || r)}</span>`).join(" ");
}

function renderUsers() {
  const tbody = $("#users-rows");
  if (!users.length) {
    tbody.innerHTML = "";
    $("#users-empty").style.display = "";
    return;
  }
  $("#users-empty").style.display = "none";
  tbody.innerHTML = users
    .map((u) => {
      const self = state.authUser && u.id === state.authUser.id;
      const simple = u.roles.length === 1 && u.roles[0] === "user";
      const statusPill =
        u.status === "blocked"
          ? '<span class="status-pill blocked">заблокирован</span>'
          : '<span class="status-pill active">активен</span>';
      let actionsCell;
      if (self) {
        // Свой аккаунт: роли/блокировку/удаление менять себе нельзя — одна общая метка
        // (аккаунты и опции админ меняет в собственном личном кабинете).
        actionsCell = '<span class="muted" style="font-size:12px">Это Вы</span>';
      } else {
        const roleBtn = simple
          ? '<span class="muted" style="font-size:12px">—</span>'
          : `<button class="ghost btn-mini" data-act="roles" data-id="${u.id}">Роли</button>`;
        const accountsBtn = `<button class="ghost btn-mini" data-act="accounts" data-id="${u.id}" title="Аккаунты, платные опции и триал пользователя">Опции</button>`;
        const statusBtn = `<button class="ghost btn-mini" data-act="status" data-id="${u.id}">${
            u.status === "blocked" ? "Разблокировать" : "Заблокировать"
          }</button>`;
        const delBtn = `<button class="danger btn-mini" data-act="delete" data-id="${u.id}">Удалить</button>`;
        actionsCell = `${roleBtn} ${accountsBtn} ${statusBtn} ${delBtn}`;
      }
      return `<tr>
        <td class="muted">#${u.id}</td>
        <td>${escapeHtml(u.username)}</td>
        <td class="muted">${escapeHtml(u.email || "—")}</td>
        <td>${rolePills(u.roles)}</td>
        <td>${statusPill}</td>
        <td class="muted">${fmtDT(u.created_at)}</td>
        <td class="actions">${actionsCell}</td>
      </tr>`;
    })
    .join("");
}

function openCreateUser() {
  userModalMode = "create";
  userModalTarget = null;
  $("#user-modal-title").textContent = "Создать пользователя";
  $("#user-password-field").style.display = "";
  $("#user-username").value = "";
  $("#user-email").value = "";
  $("#user-password").value = "";
  $("#user-error").textContent = "";
  ASSIGNABLE_ROLES.forEach((r) => {
    document.getElementById("user-role-" + r).checked = false;
  });
  $("#user-save").textContent = "Создать";
  $("#user-modal-bg").classList.add("open");
  $("#user-username").focus();
}

function openRolesModal(user) {
  userModalMode = "roles";
  userModalTarget = user;
  $("#user-modal-title").textContent = "Роли: " + user.username;
  $("#user-password-field").style.display = "none";
  $("#user-username").value = user.username;
  $("#user-email").value = user.email || "";
  $("#user-error").textContent = "";
  ASSIGNABLE_ROLES.forEach((r) => {
    document.getElementById("user-role-" + r).checked = user.roles.includes(r);
  });
  $("#user-save").textContent = "Сохранить";
  $("#user-modal-bg").classList.add("open");
}

async function saveUserModal() {
  const err = $("#user-error");
  err.textContent = "";
  const username = $("#user-username").value.trim();
  const email = $("#user-email").value.trim() || null;
  const password = $("#user-password").value;
  const roles = ASSIGNABLE_ROLES.filter(
    (r) => document.getElementById("user-role-" + r).checked
  );
  if (!username) { err.textContent = "Укажите логин"; return; }
  if (!roles.length) { err.textContent = "Выберите хотя бы одну роль"; return; }
  if (userModalMode === "create" && password.length < 8) {
    err.textContent = "Пароль должен быть не короче 8 символов";
    return;
  }
  const saveBtn = $("#user-save");
  saveBtn.disabled = true;
  try {
    if (userModalMode === "create") {
      const r = await apiJSON("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, email, password, roles }),
      });
      if (r.status === 401) return;
      if (!r.ok) {
        let msg = "не удалось создать";
        try {
          const d = await r.json();
          msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
        } catch (e) {}
        err.textContent = msg;
        return;
      }
      closeUserModal();
    } else {
      if (!userModalTarget) return;
      const r = await apiJSON(`/api/users/${userModalTarget.id}/roles`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ roles }),
      });
      if (r.status === 401) return;
      if (!r.ok) {
        let msg = "не удалось изменить роли";
        try {
          const d = await r.json();
          msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
        } catch (e) {}
        err.textContent = msg;
        return;
      }
      closeUserModal();
    }
    await loadUsers();
    $("#users-status").textContent = "Сохранено ✓";
    setTimeout(() => { $("#users-status").textContent = ""; }, 3000);
  } finally {
    saveBtn.disabled = false;
  }
}

function statusText(msg) {
  $("#users-status").textContent = msg;
}

async function setStatus(u) {
  const next = u.status === "blocked" ? "active" : "blocked";
  const r = await apiJSON(`/api/users/${u.id}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: next }),
  });
  if (r.status === 401) return;
  if (!r.ok) {
    let msg = "не удалось изменить статус";
    try {
      const d = await r.json();
      msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
    } catch (e) {}
    statusText(msg);
    return;
  }
  await loadUsers();
}

async function removeUser(u) {
  if (!confirm(`Удалить пользователя «${u.username}»? Профили и оценки удалятся безвозвратно.`)) return;
  const r = await apiJSON(`/api/users/${u.id}`, { method: "DELETE" });
  if (r.status === 401) return;
  if (!r.ok) {
    let msg = "не удалось удалить";
    try {
      const d = await r.json();
      msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
    } catch (e) {}
    statusText(msg);
    return;
  }
  await loadUsers();
}

// --- Модалка «Опции» (аккаунты и триал пользователя) ---------------------
async function openUserAccounts(user) {
  uaData = null;
  $("#user-accounts-title").textContent = "Аккаунты и опции: " + user.username;
  $("#user-accounts-body").innerHTML = '<p class="muted">Загрузка…</p>';
  $("#user-accounts-modal-bg").classList.add("open");
  try {
    const [acc, catalog] = await Promise.all([
      api(`users/${user.id}/accounts`),
      api("account/catalog"),
    ]);
    uaData = { user, ...acc, catalog };
    uaOptions = {};
    renderUserAccounts();
  } catch (err) {
    $("#user-accounts-body").innerHTML =
      `<p class="muted">Не удалось загрузить аккаунты: ${escapeHtml(err.message)}</p>`;
  }
}

function closeUserAccountsModal() {
  $("#user-accounts-modal-bg").classList.remove("open");
  uaData = null;
}

function uaActiveAccount() {
  if (!uaData) return null;
  return uaData.accounts.find((a) => a.id === uaData.active_account_id) || null;
}

function uaStatus(msg) {
  const el = $("#user-accounts-body").querySelector(".ua-status");
  if (el) el.textContent = msg || "";
}

function renderUserAccounts() {
  const body = $("#user-accounts-body");
  if (!uaData) return;
  const t = uaData.trial || {};
  const active = uaActiveAccount();
  const trialHtml = `<div class="ua-trial">
      ${t.enabled
        ? `<b>Триал активен</b> до ${fmtDT(t.trial_end_at)} (осталось ${t.days_left} дн.) — все опции поиска и скоринга бесплатны.`
        : t.trial_end_at
          ? `Триал завершён ${fmtDT(t.trial_end_at)} — действует выбранный аккаунт.`
          : "Триал не задан — действует выбранный аккаунт."}
    </div>
    <div class="toolbar" style="margin:8px 0 0;">
      <label>Триал на <input type="number" id="ua-trial-days" value="14" min="1" max="365" style="width:70px;"> дн.
        <button class="ghost btn-mini" data-act="trial-set">Установить</button>
      </label>
      <button class="ghost btn-mini" data-act="trial-off" ${t.enabled ? "" : "disabled"}>Снять триал</button>
      <span class="ua-status muted"></span>
    </div>`;
  const rows = uaData.accounts
    .map((a) => {
      const paidOn = Object.entries(a.options || {})
        .filter(([, v]) => v)
        .map(([k]) => k);
      const delDisabled = a.is_active || uaData.accounts.length <= 1;
      return `<div class="acct-row${a.is_active ? " active" : ""}" data-id="${a.id}">
        <button class="ghost btn-mini" data-act="activate" ${a.is_active ? "disabled" : ""}>${a.is_active ? "✓ активен" : "активировать"}</button>
        <span class="acct-name">${escapeHtml(a.name)}</span>
        <span class="muted" style="font-size:12px">${paidOn.length ? "платные: " + escapeHtml(paidOn.join(", ")) : "только бесплатные"}</span>
        <button class="danger btn-mini" data-act="delete" ${delDisabled ? "disabled" : ""}>удалить</button>
      </div>`;
    })
    .join("");
  const createRow = `<div class="toolbar" style="margin:8px 0;">
      <input id="ua-account-name" placeholder="имя нового аккаунта" style="min-width:200px;">
      <button class="ghost btn-mini" data-act="create">Создать аккаунт</button>
    </div>`;
  const catalogRows = uaData.catalog
    .map((o) => {
      if (o.group === "free") {
        return `<div class="opt-row opt-free"><span class="opt-check">✓</span>
          <div><b>${escapeHtml(o.title)}</b> — бесплатно
            <div class="muted" style="font-size:12px">${escapeHtml(o.description)}</div></div></div>`;
      }
      if (!active) return "";
      const checked = uaOptions[o.key] === undefined ? active.options[o.key] === true : uaOptions[o.key];
      const note = [];
      if (!o.available) note.push("недоступно (сервис не подключён)");
      if (o.requires_competencies) note.push("требует компетенций в профиле");
      return `<div class="opt-row">
        <label class="opt-check"><input type="checkbox" data-opt="${o.key}" ${checked ? "checked" : ""} ${o.available ? "" : "disabled"}></label>
        <div><b>${escapeHtml(o.title)}</b> — платная опция
          ${note.length ? `<span class="opt-note">${escapeHtml(note.join(" · "))}</span>` : ""}
          <div class="muted" style="font-size:12px">${escapeHtml(o.description)}</div></div>
      </div>`;
    })
    .join("");
  body.innerHTML = `
    <p class="muted" style="margin-top:0; font-size:13px;">Пользователь: <b>${escapeHtml(uaData.user.username)}</b> (id ${uaData.user.id})</p>
    <div class="panel-sub" style="border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:10px;">
      <div class="panel-title" style="font-size:13px;">Триал-режим</div>
      ${trialHtml}
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
      <div>
        <div class="panel-title" style="font-size:13px;">Аккаунты</div>
        ${rows}
        ${createRow}
      </div>
      <div>
        <div class="panel-title" style="font-size:13px;">Опции активного аккаунта</div>
        ${active ? `<p class="muted" style="font-size:12px;margin-top:0;">Активный: <b>${escapeHtml(active.name)}</b></p>` : '<p class="muted">Нет активного аккаунта</p>'}
        ${catalogRows}
        <div class="toolbar" style="margin:8px 0 0;">
          <button class="primary btn-mini" data-act="save-options" ${active ? "" : "disabled"}>Сохранить опции</button>
        </div>
      </div>
    </div>`;
}

async function onUserAccountsClick(e) {
  const btn = e.target.closest("button[data-act]");
  if (!btn || btn.disabled) return;
  const act = btn.dataset.act;
  const errMsg = (msg) => uaStatus("Ошибка: " + msg);
  const reload = async () => {
    const acc = await api(`users/${uaData.user.id}/accounts`);
    Object.assign(uaData, acc);
    uaOptions = {};
    renderUserAccounts();
  };
  try {
    if (act === "trial-set") {
      const days = Number($("#ua-trial-days").value || 14);
      const r = await apiJSON(`/api/users/${uaData.user.id}/trial`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days }),
      });
      if (!r.ok) throw new Error(await apiErrorDetail(r));
      await reload();
      uaStatus("Триал установлен ✓");
    } else if (act === "trial-off") {
      const r = await apiJSON(`/api/users/${uaData.user.id}/trial`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trial_end_at: null }),
      });
      if (!r.ok) throw new Error(await apiErrorDetail(r));
      await reload();
      uaStatus("Триал снят");
    } else if (act === "activate") {
      const id = Number(btn.closest(".acct-row").dataset.id);
      const r = await apiJSON(`/api/users/${uaData.user.id}/accounts/${id}/activate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!r.ok) throw new Error(await apiErrorDetail(r));
      await reload();
    } else if (act === "delete") {
      const id = Number(btn.closest(".acct-row").dataset.id);
      if (!confirm("Удалить аккаунт? Опции этого набора будут потеряны.")) return;
      const r = await apiJSON(`/api/users/${uaData.user.id}/accounts/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await apiErrorDetail(r));
      await reload();
    } else if (act === "create") {
      const name = ($("#ua-account-name").value || "").trim();
      if (!name) { uaStatus("Укажите имя аккаунта"); return; }
      const r = await apiJSON(`/api/users/${uaData.user.id}/accounts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) throw new Error(await apiErrorDetail(r));
      await reload();
      uaStatus("Аккаунт создан ✓");
    } else if (act === "save-options") {
      const active = uaActiveAccount();
      if (!active) return;
      const options = {};
      uaData.catalog
        .filter((o) => o.group === "paid" && o.available)
        .forEach((o) => {
          const cb = document.querySelector(`#user-accounts-body input[data-opt="${o.key}"]`);
          options[o.key] = !!(cb && cb.checked);
        });
      const r = await apiJSON(`/api/users/${uaData.user.id}/accounts/${active.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ options }),
      });
      if (!r.ok) throw new Error(await apiErrorDetail(r));
      await reload();
      uaStatus("Опции сохранены ✓");
    }
  } catch (err) {
    errMsg(err.message);
  }
}

export { loadUsers, closeUserModal, closeUserAccountsModal };

$("#user-new").addEventListener("click", openCreateUser);
$("#user-save").addEventListener("click", saveUserModal);
$("#user-cancel").addEventListener("click", closeUserModal);
$("#user-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "user-modal-bg") closeUserModal();
});
$("#users-rows").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const user = users.find((u) => u.id === Number(btn.dataset.id));
  if (!user) return;
  const act = btn.dataset.act;
  if (act === "roles") openRolesModal(user);
  else if (act === "accounts") await openUserAccounts(user);
  else if (act === "status") await setStatus(user);
  else if (act === "delete") await removeUser(user);
});
// Админ-редактор аккаунтов пользователя.
$("#user-accounts-close").addEventListener("click", closeUserAccountsModal);
$("#user-accounts-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "user-accounts-modal-bg") closeUserAccountsModal();
});
$("#user-accounts-body").addEventListener("click", onUserAccountsClick);
// Изменение чекбоксов опций в модалке копим в uaOptions (сохраняются кнопкой).
$("#user-accounts-body").addEventListener("change", (e) => {
  const cb = e.target.closest("input[data-opt]");
  if (!cb || !uaData) return;
  uaOptions[cb.dataset.opt] = cb.checked;
});
// Inline onclick в шапке модалки (<span class="close" onclick="closeUserAccountsModal()">).
window.closeUserAccountsModal = closeUserAccountsModal;
