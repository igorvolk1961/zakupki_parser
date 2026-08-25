"use strict";

// Вкладка «Пользователи» (роль admin): список пользователей, создание (роли
// admin/analyst/devops), смена ролей (кроме простых и себя), блокировка/
// разблокировка и удаление (нельзя — себя и последнего admin).
import { $, escapeHtml, fmtDT } from "./utils.js";
import { state } from "./store.js";
import { api, apiJSON } from "./api.js";

const ROLE_LABELS = {
  user: "Простой пользователь",
  admin: "Администратор",
  analyst: "Аналитик",
  devops: "DevOps",
};

const ASSIGNABLE_ROLES = ["admin", "analyst", "devops"];

let users = [];
let userModalMode = "create"; // "create" | "roles"
let userModalTarget = null;

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
      const roleBtn = simple || self
        ? '<span class="muted" style="font-size:12px">' + (simple ? "—" : "вы") + "</span>"
        : `<button class="ghost btn-mini" data-act="roles" data-id="${u.id}">Роли</button>`;
      const statusBtn = self
        ? '<span class="muted" style="font-size:12px">вы</span>'
        : `<button class="ghost btn-mini" data-act="status" data-id="${u.id}">${
            u.status === "blocked" ? "Разблокировать" : "Заблокировать"
          }</button>`;
      const delBtn = self
        ? '<span class="muted" style="font-size:12px">вы</span>'
        : `<button class="danger btn-mini" data-act="delete" data-id="${u.id}">Удалить</button>`;
      return `<tr>
        <td class="muted">#${u.id}</td>
        <td>${escapeHtml(u.username)}</td>
        <td class="muted">${escapeHtml(u.email || "—")}</td>
        <td>${rolePills(u.roles)}</td>
        <td>${statusPill}</td>
        <td class="muted">${fmtDT(u.created_at)}</td>
        <td class="actions">${roleBtn} ${statusBtn} ${delBtn}</td>
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

export { loadUsers, closeUserModal };

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
  else if (act === "status") await setStatus(user);
  else if (act === "delete") await removeUser(user);
});
