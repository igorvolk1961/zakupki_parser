"use strict";

// Вкладка «Личный кабинет»: аккаунты (наборы платных опций), каталог опций,
// триал-статус и смена пароля. Аккаунтов у пользователя может быть несколько,
// активен один; активный аккаунт определяет доступ после окончания триала.
import { $, escapeHtml, fmtDate } from "./utils.js";
import { api, apiJSON, apiErrorDetail } from "./api.js";

let cabinet = null; // { trial, active_account_id, accounts, catalog }

async function loadAccount() {
  try {
    cabinet = await api("account/cabinet");
  } catch (err) {
    cabinet = null;
    $("#acct-trial").innerHTML = `<div class="acct-banner warn">Не удалось загрузить данные кабинета: ${escapeHtml(err.message)}</div>`;
    $("#acct-accounts").innerHTML = "";
    $("#acct-options").innerHTML = "";
    return;
  }
  renderTrial();
  renderAccounts();
  renderOptions();
}

function activeAccount() {
  if (!cabinet) return null;
  return cabinet.accounts.find((a) => a.id === cabinet.active_account_id) || null;
}

function setAcctStatus(msg) {
  const el = $("#acct-accounts-status");
  if (el) el.textContent = msg;
  if (msg) setTimeout(() => { el.textContent = ""; }, 4000);
}

function renderTrial() {
  const box = $("#acct-trial");
  const t = cabinet.trial;
  if (!box) return;
  if (t.enabled) {
    box.innerHTML = `<div class="acct-banner trial" title="В триал-режиме все опции поиска и скоринга доступны бесплатно">
      <b>Триал-режим</b>: до ${fmtDate(t.trial_end_at)} (осталось ${t.days_left} дн.).
      Все опции поиска и скоринга доступны бесплатно.</div>`;
  } else if (t.trial_end_at) {
    box.innerHTML = `<div class="acct-banner off" title="Триал завершён — действует выбранный аккаунт">
      Триал-режим завершён ${fmtDate(t.trial_end_at)}: действуют опции выбранного аккаунта.</div>`;
  } else {
    box.innerHTML = `<div class="acct-banner off" title="Триал не задан — действует выбранный аккаунт">
      Действуют опции выбранного аккаунта.</div>`;
  }
}

function renderAccounts() {
  const box = $("#acct-accounts");
  if (!box) return;
  const accounts = cabinet.accounts;
  if (!accounts.length) {
    box.innerHTML = '<p class="muted">Аккаунтов нет — создайте первый.</p>';
    return;
  }
  box.innerHTML = accounts
    .map((a) => {
      const paidOn = Object.entries(a.options || {})
        .filter(([, v]) => v)
        .map(([k]) => k);
      const delDisabled = a.is_active || accounts.length <= 1;
      const delTitle = a.is_active
        ? "Нельзя удалить активный аккаунт"
        : accounts.length <= 1
          ? "Нельзя удалить последний аккаунт"
          : "";
      return `<div class="acct-row${a.is_active ? " active" : ""}" data-id="${a.id}">
        <button class="ghost btn-mini" data-act="activate" ${a.is_active ? "disabled" : ""}>${a.is_active ? "✓ активен" : "активировать"}</button>
        <span class="acct-name" data-field="name" title="Имя аккаунта">${escapeHtml(a.name)}</span>
        <span class="muted" style="font-size:12px">${paidOn.length ? "платные: " + escapeHtml(paidOn.join(", ")) : "только бесплатные опции"}</span>
        <button class="danger btn-mini" data-act="delete" ${delDisabled ? "disabled" : ""} title="${delTitle}">удалить</button>
      </div>`;
    })
    .join("");
}

async function onAccountsClick(e) {
  const btn = e.target.closest("button[data-act]");
  if (!btn || btn.disabled) return;
  const id = Number(btn.closest(".acct-row").dataset.id);
  const act = btn.dataset.act;
  try {
    if (act === "activate") {
      const r = await apiJSON(`/api/account/accounts/${id}/activate`, { method: "POST" });
      if (!r.ok) throw new Error(await apiErrorDetail(r));
      await loadAccount();
    } else if (act === "delete") {
      if (!confirm("Удалить аккаунт? Опции этого набора будут потеряны.")) return;
      const r = await apiJSON(`/api/account/accounts/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await apiErrorDetail(r));
      await loadAccount();
    }
  } catch (err) {
    setAcctStatus("Ошибка: " + err.message);
  }
}

function renderOptions() {
  const box = $("#acct-options");
  if (!box) return;
  const active = activeAccount();
  if (!active) {
    box.innerHTML = '<p class="muted">Нет активного аккаунта — активируйте или создайте аккаунт.</p>';
    return;
  }
  const inTrial = cabinet.trial && cabinet.trial.enabled;
  const rows = cabinet.catalog
    .map((o) => {
      if (o.group === "free") {
        return `<div class="opt-row opt-free">
          <span class="opt-check">✓</span>
          <div><b>${escapeHtml(o.title)}</b> — бесплатно, всегда доступно
            <div class="muted" style="font-size:12px">${escapeHtml(o.description)}</div>
          </div>
        </div>`;
      }
      const stored = active.options && active.options[o.key] === true;
      const effective = o.enabled === true;
      const disabled = !o.available;
      const note = [];
      if (!o.available) note.push("недоступно (сервис не подключён)");
      if (inTrial) note.push("в триале — бесплатно");
      else if (effective && !stored) note.push("активна сейчас");
      if (o.requires_competencies) note.push("требует компетенций в профиле");
      return `<div class="opt-row">
        <label class="opt-check" title="${escapeHtml(o.title)}">
          <input type="checkbox" data-opt="${o.key}" ${stored ? "checked" : ""} ${disabled ? "disabled" : ""}>
        </label>
        <div><b>${escapeHtml(o.title)}</b> — платная опция
          ${note.length ? `<span class="opt-note">${escapeHtml(note.join(" · "))}</span>` : ""}
          <div class="muted" style="font-size:12px">${escapeHtml(o.description)}</div>
        </div>
      </div>`;
    })
    .join("");
  box.innerHTML = `<div id="acct-options-list">${rows}</div>`;
  setOptionsStatus("");
}

function setOptionsStatus(msg) {
  const el = $("#acct-options-status");
  if (el) {
    el.textContent = msg;
    if (msg) setTimeout(() => { el.textContent = ""; }, 4000);
  }
}

async function saveOptions() {
  const active = activeAccount();
  if (!active) {
    setOptionsStatus("Нет активного аккаунта");
    return;
  }
  const options = {};
  cabinet.catalog
    .filter((o) => o.group === "paid" && o.available)
    .forEach((o) => {
      const cb = document.querySelector(`#acct-options input[data-opt="${o.key}"]`);
      options[o.key] = !!(cb && cb.checked);
    });
  try {
    const r = await apiJSON(`/api/account/accounts/${active.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ options }),
    });
    if (!r.ok) throw new Error(await apiErrorDetail(r));
    setOptionsStatus("Опции сохранены ✓");
    await loadAccount();
  } catch (err) {
    setOptionsStatus("Ошибка: " + err.message);
  }
}

async function createAccount() {
  const input = $("#acct-name");
  const name = (input.value || "").trim();
  if (!name) {
    setAcctStatus("Укажите имя аккаунта");
    return;
  }
  try {
    const r = await apiJSON("/api/account/accounts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!r.ok) throw new Error(await apiErrorDetail(r));
    input.value = "";
    setAcctStatus("Аккаунт создан ✓");
    await loadAccount();
  } catch (err) {
    setAcctStatus("Ошибка: " + err.message);
  }
}

// --- Смена пароля ------------------------------------------------------
function switchAccountTab(name) {
  const acc = name === "password";
  $("#acct-pane-accounts").style.display = acc ? "none" : "";
  $("#acct-pane-password").style.display = acc ? "" : "none";
  $("#acct-tab-accounts").classList.toggle("active", !acc);
  $("#acct-tab-password").classList.toggle("active", acc);
}

function setPasswordStatus(msg) {
  const err = $("#acct-password-error");
  const st = $("#acct-password-status");
  if (msg) {
    err.textContent = "";
    if (st) st.textContent = msg;
  } else {
    if (st) st.textContent = "";
  }
}

async function changePassword() {
  const current = $("#acct-password-current").value;
  const np = $("#acct-password-new").value;
  const conf = $("#acct-password-confirm").value;
  const err = $("#acct-password-error");
  err.textContent = "";
  if (!current || !np) {
    err.textContent = "Заполните все поля";
    return;
  }
  if (np.length < 8) {
    err.textContent = "Новый пароль — не менее 8 символов";
    return;
  }
  if (np !== conf) {
    err.textContent = "Пароли не совпадают";
    return;
  }
  const btn = $("#acct-password-save");
  btn.disabled = true;
  try {
    const r = await apiJSON("/api/account/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: current, new_password: np, new_password_confirm: conf }),
    });
    if (!r.ok) {
      const d = await apiErrorDetail(r);
      err.textContent = d;
      return;
    }
    $("#acct-password-current").value = "";
    $("#acct-password-new").value = "";
    $("#acct-password-confirm").value = "";
    setPasswordStatus("Пароль изменён ✓");
  } catch (e) {
    err.textContent = "Ошибка: " + e.message;
  } finally {
    btn.disabled = false;
  }
}

export { loadAccount };

// --- Привязка обработчиков ----------------------------------------------
$("#acct-create").addEventListener("click", createAccount);
$("#acct-name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") createAccount();
});
$("#acct-accounts").addEventListener("click", onAccountsClick);
$("#acct-options-save").addEventListener("click", saveOptions);
$("#acct-tab-accounts").addEventListener("click", () => switchAccountTab("accounts"));
$("#acct-tab-password").addEventListener("click", () => switchAccountTab("password"));
$("#acct-password-save").addEventListener("click", changePassword);
["acct-password-current", "acct-password-new", "acct-password-confirm"].forEach((id) => {
  document.getElementById(id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") changePassword();
  });
});
