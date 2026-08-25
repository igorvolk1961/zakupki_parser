"use strict";

// Сетевой слой: fetch-обёртки с авторизацией, токен и канал живых обновлений (WS).
import { $ } from "./utils.js";
import { state } from "./store.js";
import { showLogin } from "./auth.js";
import { pollProc } from "./procurements.js";
import { pollCustomers } from "./customers.js";

export const TOKEN_KEY = "zp_token";

export function authToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(t) {
  t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY);
}

export function authHeaders(extra) {
  const h = extra || {};
  const t = authToken();
  if (t) h["Authorization"] = "Bearer " + t;
  return h;
}

export async function api(path, params) {
  const url =
    (path.startsWith("/") ? path : "/api/" + path) +
    (params ? "?" + new URLSearchParams(params) : "");
  const r = await fetch(url, { headers: authHeaders() });
  if (r.status === 401) {
    showLogin();
    throw new Error("Требуется авторизация");
  }
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function apiJSON(url, opts) {
  const r = await fetch(
    url,
    Object.assign({}, opts, { headers: authHeaders((opts && opts.headers) || {}) })
  );
  if (r.status === 401) {
    showLogin();
    throw new Error("Требуется авторизация");
  }
  return r;
}

// Живые обновления через WebSocket (вместо кнопки «Обновить» и опроса).
let refreshScheduled = false;
export function scheduleRefresh() {
  if (refreshScheduled) return;
  refreshScheduled = true;
  setTimeout(() => {
    refreshScheduled = false;
    pollProc();
    pollCustomers();
  }, 500);
}

export function connectWS() {
  // При включённой авторизации без токена не подключаемся: иначе сервер
  // отклоняет каждый запрос (403) и клиент спамит в лог.
  const t = authToken();
  if (state.authRequired && !t) return;
  // Не плодим дубликаты соединений (повторный вход, переподключение).
  if (
    state.wsSocket &&
    (state.wsSocket.readyState === WebSocket.CONNECTING ||
      state.wsSocket.readyState === WebSocket.OPEN)
  )
    return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws` + (t ? "?token=" + encodeURIComponent(t) : "");
  const ws = new WebSocket(url);
  state.wsSocket = ws;
  ws.onmessage = scheduleRefresh;
  ws.onclose = () => {
    state.wsSocket = null;
    // Переподключаемся только если всё ещё есть токен (вход выполнен).
    if (authToken()) setTimeout(connectWS, 3000);
  };
  ws.onerror = () => {
    try {
      ws.close();
    } catch (err) {}
  };
}
