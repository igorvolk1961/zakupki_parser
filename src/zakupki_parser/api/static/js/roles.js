"use strict";

// Ролевая модель web-интерфейса: наборы вкладок по ролям.
// Видимые вкладки пользователя — объединение наборов его ролей; при выключенной
// авторизации (state.authUser == null, dev-режим) видны все вкладки.
// Базовые вкладки (Закупки/В работе/Заказчики/Профили) — слева, ролевые — справа.
import { state } from "./store.js";

export const TAB_BASE = ["proc", "work", "cust", "profiles"];
export const TAB_ACCOUNT = "account";
export const TAB_METRICS = "metrics";
export const TAB_USERS = "users";
export const TAB_MONITOR = "monitor";
export const TAB_PROMPTS = "prompts";
export const TAB_REFS = "refs";
export const TAB_SERVICES = "services";
export const TAB_CFGOPS = "cfgops";
export const TAB_LOGCFG = "logcfg";
export const TAB_LOGS = "logs";
export const TAB_PARSER = "parser";

export const TAB_SETS = {
  user: [...TAB_BASE, TAB_ACCOUNT],
  admin: [TAB_USERS, TAB_ACCOUNT],
  analyst: [...TAB_BASE, TAB_METRICS, TAB_MONITOR, TAB_PROMPTS, TAB_REFS, TAB_ACCOUNT],
  devops: [TAB_PARSER, TAB_SERVICES, TAB_CFGOPS, TAB_LOGCFG, TAB_LOGS, TAB_ACCOUNT],
};

export const ALL_TABS = [
  "proc",
  "work",
  "cust",
  "profiles",
  TAB_ACCOUNT,
  TAB_PARSER,
  TAB_METRICS,
  TAB_USERS,
  TAB_MONITOR,
  TAB_PROMPTS,
  TAB_REFS,
  TAB_SERVICES,
  TAB_CFGOPS,
  TAB_LOGCFG,
  TAB_LOGS,
];

export const ROLE_LABELS = {
  user: "Пользователь",
  admin: "Администратор",
  analyst: "Аналитик",
  devops: "DevOps",
};

export function userRoles() {
  if (!state.authUser) return Object.keys(TAB_SETS);
  return state.authUser.roles || [];
}

export function hasRole(role) {
  return userRoles().includes(role);
}

export function isDevops() {
  return hasRole("devops");
}

// Базовые вкладки (Закупки/Заказчики/Профили) доступны ролям user/analyst.
// Для devops/admin-only аккаунтов их не грузим вовсе — иначе каждая такая
// загрузка падает 403 («Требуется одна из ролей: user, analyst»).
export function canAccessBase() {
  return hasRole("user") || hasRole("analyst");
}

export function visibleTabs() {
  const set = new Set();
  userRoles().forEach((r) => {
    (TAB_SETS[r] || []).forEach((t) => set.add(t));
  });
  return ALL_TABS.filter((t) => set.has(t));
}

export function roleLabelList() {
  return userRoles().map((r) => ROLE_LABELS[r] || r);
}

export function switchTo(name) {
  ALL_TABS.forEach((t) => {
    const btn = document.getElementById("tab-" + t);
    if (btn) btn.classList.toggle("active", t === name);
    const view = document.getElementById("view-" + t);
    if (view) view.style.display = t === name ? "block" : "none";
  });
  // Один путь активации вкладки: и клики по кнопкам, и программное переключение
  // (updateRolesUI при скрытии активной вкладки ролью) должны загружать содержимое.
  document.dispatchEvent(new CustomEvent("tab:active", { detail: { name } }));
}

export function updateRolesUI() {
  const visible = visibleTabs();
  ALL_TABS.forEach((t) => {
    const btn = document.getElementById("tab-" + t);
    if (btn) btn.style.display = visible.includes(t) ? "" : "none";
  });
  // Панель парсера (Запустить/Остановить/Очистить БД) — только devops.
  const panel = document.getElementById("parser-panel");
  if (panel) panel.style.display = isDevops() ? "" : "none";
  // Клиентская выгрузка CSV — базовые вкладки (роли user/analyst).
  const exportBtn = document.getElementById("db-export");
  if (exportBtn) exportBtn.style.display = hasRole("user") || hasRole("analyst") ? "" : "none";
  // Активная вкладка скрыта ролью — переключаемся на первую видимую.
  const active = document.querySelector(".tabs .active");
  if (active && active.style.display === "none") {
    const first = visible[0];
    if (first) switchTo(first);
  }
}
