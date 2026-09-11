"use strict";

// Сетевой слой: fetch-обёртки с авторизацией, токен и канал живых обновлений (WS).
import { $ } from "./utils.js";
import { state } from "./store.js";
import { showLogin, renderAuth } from "./auth.js";
import { canAccessBase } from "./roles.js";
import { pollProc } from "./procurements.js";
import { pollCustomers } from "./customers.js";
import { pollWork } from "./work.js";

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
  // undef/null параметры не попадают в строку запроса: иначе URLSearchParams
  // превращает их в текст "undefined" и сервер начинает фильтровать по нему
  // (например, q=undefined вырезает все строки лога — «строк: 0»).
  const query = params
    ? new URLSearchParams(
        Object.fromEntries(
          Object.entries(params).filter(([, v]) => v !== undefined && v !== null)
        )
      ).toString()
    : "";
  const url = (path.startsWith("/") ? path : "/api/" + path) + (query ? "?" + query : "");
  // cache: "no-store" — не даём браузеру отдавать устаревший ответ из кэша:
  // вкладки «Логи» и другие обновляются опросом (авто 5с) и кнопкой «Обновить»
  // и всегда должны получать свежие данные, а не кэш предыдущего ответа.
  const r = await fetch(url, { headers: authHeaders(), cache: "no-store" });
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

// Читаемый текст ошибки из HTTP-ответа: detail (строка или массив ошибок полей
// FastAPI), иначе тело ответа, иначе «HTTP <статус>». Общий для всех вкладок.
export async function apiErrorDetail(r) {
  let data = null;
  try {
    data = await r.json();
  } catch (err) {
    const text = await r.text().catch(() => "");
    return text || "HTTP " + r.status;
  }
  if (data && data.detail !== undefined) {
    if (Array.isArray(data.detail)) {
      return data.detail
        .map((d) => (d && (d.msg || d.detail)) || String(d))
        .join("; ");
    }
    return String(data.detail);
  }
  return data == null ? "HTTP " + r.status : JSON.stringify(data);
}

// Живые обновления через WebSocket (вместо кнопки «Обновить» и опроса).
let refreshScheduled = false;
export function scheduleRefresh() {
  if (refreshScheduled) return;
  refreshScheduled = true;
  setTimeout(() => {
    refreshScheduled = false;
    // Панель закупок/заказчиков обновляем только при доступе к базовым вкладкам;
    // для devops/admin-only аккаунтов это запросы-403.
    if (canAccessBase()) {
      pollProc();
      pollCustomers();
      pollWork();
    }
  }, 500);
}

// Переподключение канала обновлений после разрыва. Соединение могло закрыться
// из-за недействительного/просроченного токена (сервер отклоняет /ws с 403 без
// внятного кода — браузер видит лишь аварийное закрытие). Перед повтором
// проверяем сессию через /api/auth/me: при 401/403 сбрасываем токен и просим
// войти, иначе переподключения с тем же токеном повторяли бы 403 бесконечно.
async function revalidateThenReconnect() {
  try {
    const r = await fetch("/api/auth/me", { headers: authHeaders() });
    if (r.status === 401 || r.status === 403) {
      setToken(null);
      state.authUser = null;
      state.authRequired = true;
      renderAuth();
      showLogin();
      return;
    }
  } catch (err) {
    /* сервер ещё недоступен — повторим попытку по таймеру */
  }
  if (authToken()) setTimeout(connectWS, 3000);
}

export function connectWS() {
  // Авторизация на сервере всегда включена: без токена handshake заведомо
  // отклоняется (403/1008) и клиент шумит в лог — не подключаемся вовсе.
  const t = authToken();
  if (!t) return;
  // Не плодим дубликаты соединений (повторный вход, переподключение).
  if (
    state.wsSocket &&
    (state.wsSocket.readyState === WebSocket.CONNECTING ||
      state.wsSocket.readyState === WebSocket.OPEN)
  )
    return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws?token=` + encodeURIComponent(t);
  const ws = new WebSocket(url);
  state.wsSocket = ws;
  ws.onmessage = scheduleRefresh;
  ws.onclose = () => {
    state.wsSocket = null;
    if (authToken()) revalidateThenReconnect();
  };
  ws.onerror = () => {
    try {
      ws.close();
    } catch (err) {}
  };
}
