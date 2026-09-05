"use strict";

// Авторизация: вход / регистрация / выход / текущий пользователь.
import { $ } from "./utils.js";
import { state } from "./store.js";
import { authToken, authHeaders, setToken, connectWS } from "./api.js";
import { loadProc, loadPlatforms } from "./procurements.js";
import { loadCustomers } from "./customers.js";
import { loadActiveClient } from "./clients.js";
import { updateControls } from "./admin.js";
import { canAccessBase, roleLabelList, updateRolesUI } from "./roles.js";

let loginMode = "login"; // "login" | "register"

// Кэш последних логинов (localStorage): дубликаты убираются, свежие — сверху.
const LOGINS_KEY = "zp_saved_logins";
const LOGINS_LIMIT = 10;

function getSavedLogins() {
  try {
    const arr = JSON.parse(localStorage.getItem(LOGINS_KEY) || "[]");
    return Array.isArray(arr) ? arr.filter((u) => typeof u === "string" && u.trim()) : [];
  } catch {
    return [];
  }
}

function saveLoginToCache(username) {
  const name = username.trim();
  if (!name) return;
  const list = getSavedLogins().filter((u) => u !== name);
  list.unshift(name);
  localStorage.setItem(LOGINS_KEY, JSON.stringify(list.slice(0, LOGINS_LIMIT)));
  renderLoginOptions();
}

function renderLoginOptions() {
  const d = document.getElementById("login-usernames");
  if (!d) return;
  d.innerHTML = "";
  for (const name of getSavedLogins()) {
    const opt = document.createElement("option");
    opt.value = name;
    d.appendChild(opt);
  }
}

function showLogin() {
  loginMode = "login";
  $("#login-title").textContent = "Вход";
  $("#login-switch").textContent = "Зарегистрироваться";
  $("#login-submit").textContent = "Вход";
  const sub = $("#login-modal .login-sub");
  if (sub) sub.textContent = "Рабочее пространство тендеролога";
  $("#login-error").textContent = "";
  $("#login-password").type = "password";
  $("#login-password").autocomplete = "new-password";
  $("#login-confirm-field").style.display = "none";
  $("#login-password-confirm").value = "";
  $("#login-modal-bg").classList.add("open");
  renderLoginOptions();
  $("#login-username").focus();
}

function hideLogin() {
  $("#login-modal-bg").classList.remove("open");
}

async function doLogin(register) {
  const username = $("#login-username").value.trim();
  const password = $("#login-password").value;
  const confirm = $("#login-password-confirm").value;
  const err = $("#login-error");
  err.textContent = "";
  if (!username || !password) { err.textContent = "Укажите логин и пароль"; return; }
  if (register) {
    if (!confirm) { err.textContent = "Повторите пароль"; return; }
    if (password !== confirm) { err.textContent = "Пароли не совпадают"; return; }
  }
  $("#login-submit").disabled = true;
  try {
    const body = register ? { username, password, password_confirm: confirm } : { username, password };
    const r = await fetch(register ? "/api/auth/register" : "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      // 422 — ошибка валидации: detail — массив ошибок полей.
      let detail = data.detail;
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
      err.textContent = detail || "Не удалось выполнить вход";
      return;
    }
    setToken(data.access_token);
    saveLoginToCache(username);
    state.authUser = data.user;
    state.authRequired = true;
    hideLogin();
    renderAuth();
    connectWS();
    // Базовые вкладки грузим только тем, у кого есть доступ (user/analyst);
    // devops/admin-only аккаунтам они не положены и упали бы 403.
    if (canAccessBase()) {
      loadProc(); loadCustomers(); loadPlatforms(); loadActiveClient();
    }
  } catch (e) {
    err.textContent = "Ошибка: " + e.message;
  } finally {
    $("#login-submit").disabled = false;
  }
}

function renderAuth() {
  const bar = $("#user-bar");
  if (state.authUser) {
    bar.style.display = "flex";
    $("#user-name").textContent = state.authUser.username;
    $("#user-role").textContent = roleLabelList().join(", ");
    renderTrialPill(state.authUser.trial_end_at);
  } else {
    bar.style.display = "none";
    $("#user-trial").style.display = "none";
  }
  updateControls();
  updateRolesUI();
}

// Пилюля «триал N дн.» в шапке: расчёт по серверной дате окончания триала.
function renderTrialPill(trialEndAt) {
  const pill = $("#user-trial");
  if (!pill) return;
  if (!trialEndAt) {
    pill.style.display = "none";
    return;
  }
  const end = new Date(trialEndAt);
  const days = Math.max(0, Math.ceil((end - Date.now()) / 86400000));
  if (days <= 0) {
    pill.style.display = "none";
    return;
  }
  pill.textContent = `триал ${days} дн.`;
  pill.style.display = "";
}

function logout() {
  setToken(null);
  state.authUser = null;
  // Закрываем канал обновлений: при отсутствии токена onclose переподключаться не будет.
  if (state.wsSocket) {
    try {
      state.wsSocket.close();
    } catch (err) {}
    state.wsSocket = null;
  }
  renderAuth();
  showLogin();
}

// Проверяет состояние авторизации. Возвращает true, если авторизация включена
// (200 — пользователь известен, 401 — показать вход); false — отключена/сбой
// (dev-режим, загружаем данные без логина).
async function checkAuth() {
  try {
    const r = await fetch("/api/auth/me", { headers: authHeaders() });
    if (r.status === 200) {
      state.authRequired = true;
      state.authUser = await r.json();
      renderAuth();
      return true;
    }
    if (r.status === 403) {
      // Заблокированный аккаунт: вход закрыт, показываем ошибку в модалке входа.
      state.authRequired = true;
      showLogin();
      $("#login-error").textContent = "Аккаунт заблокирован";
      return true;
    }
    if (r.status === 401) {
      state.authRequired = true;
      showLogin();
      return true;
    }
  } catch (e) {
    /* сервис ещё недоступен — повторим при следующем действии */
  }
  renderAuth();
  return false;
}

export { showLogin, hideLogin, doLogin, renderAuth, logout, checkAuth };

// --- Вход / регистрация --------------------------------------------------
$("#login-submit").addEventListener("click", () => doLogin(loginMode === "register"));
$("#login-switch").addEventListener("click", () => {
  loginMode = loginMode === "login" ? "register" : "login";
  $("#login-title").textContent = loginMode === "register" ? "Регистрация" : "Вход";
  $("#login-switch").textContent = loginMode === "register" ? "Войти" : "Зарегистрироваться";
  $("#login-submit").textContent = loginMode === "register" ? "Создать аккаунт" : "Вход";
  const sub = $("#login-modal .login-sub");
  if (sub) sub.textContent = loginMode === "register"
    ? "Создайте аккаунт — роли назначает администратор"
    : "Рабочее пространство тендеролога";
  $("#login-password").type = "password";
  $("#login-password").autocomplete = "new-password";
  $("#login-confirm-field").style.display = loginMode === "register" ? "" : "none";
  $("#login-password-confirm").value = "";
  $("#login-error").textContent = "";
  $("#login-username").focus();
});
["login-username", "login-password"].forEach((id) => {
  document.getElementById(id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLogin(loginMode === "register");
  });
});
$("#login-modal-bg").addEventListener("click", (e) => {
  // Не даём закрыть модалку входа кликом мимо (нужна авторизация).
  if (e.target.id === "login-modal-bg" && state.authUser) hideLogin();
});
$("#logout").addEventListener("click", logout);
