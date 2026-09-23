"use strict";

// Ролевая модель web-интерфейса: наборы вкладок по ролям.
// Видимые вкладки пользователя — объединение наборов его ролей; без входа
// (гость) ролевых вкладок нет — вместо них показывается главный экран
// с меню («Вход», «Документация»). Базовые вкладки (Закупки/Заказчики/
// Профили) — слева, ролевые — справа. «В работе» — не отдельная вкладка,
// а фильтр на «Закупки» (in_work).
import { state } from "./store.js";

export const TAB_BASE = ["proc", "cust", "profiles"];
export const TAB_ACCOUNT = "account";
export const TAB_METRICS = "metrics";
export const TAB_USERS = "users";
export const TAB_MONITOR = "monitor";
export const TAB_PROMPTS = "prompts";
export const TAB_REFS = "refs";
export const TAB_SERVICES = "services";
export const TAB_MONITORING = "monitoring";
export const TAB_CFGOPS = "cfgops";
export const TAB_LOGCFG = "logcfg";
export const TAB_LOGS = "logs";

export const TAB_SETS = {
  user: [...TAB_BASE, TAB_ACCOUNT],
  // Личный кабинет/аккаунты — только ролям, у которых может быть профиль
  // (user/analyst); admin/devops профилей не имеют, поэтому без кабинета.
  admin: [TAB_USERS],
  // TAB_MONITORING (devops) добавлен и аналитику: Dead Letter Queue фоновой
  // индексации (require_analyst_or_devops, monitoring.py) требует и его доступа
  // — остальные панели вкладки (очереди/циклы/диск) для аналитика read-only.
  analyst: [
    ...TAB_BASE,
    TAB_METRICS,
    TAB_MONITOR,
    TAB_MONITORING,
    TAB_PROMPTS,
    TAB_REFS,
    TAB_ACCOUNT,
  ],
  // Конфиг парсера теперь под-вкладкой «Сервисы» (svc-tab-parser), не отдельным
  // верхним табом — см. ops_config.js.
  devops: [TAB_SERVICES, TAB_MONITORING, TAB_CFGOPS, TAB_LOGCFG, TAB_LOGS],
};

export const ALL_TABS = [
  "proc",
  "cust",
  "profiles",
  TAB_ACCOUNT,
  TAB_METRICS,
  TAB_USERS,
  TAB_MONITOR,
  TAB_PROMPTS,
  TAB_REFS,
  TAB_SERVICES,
  TAB_MONITORING,
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
  if (!state.authUser) return [];
  return state.authUser.roles || [];
}

export function hasRole(role) {
  return userRoles().includes(role);
}

export function isDevops() {
  return hasRole("devops");
}

// Личный кабинет и аккаунты доступны только ролям, у которых может быть профиль
// (user/analyst): admin/devops профилей не имеют, поэтому кабинет им не показываем.
export function canAccessAccount() {
  return hasRole("user") || hasRole("analyst");
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
  // «Закупки» — единственная вкладка, растянутая на весь экран (таблица +
  // карточка, см. zakupki.css body.wide-view): вне её остальные вкладки не
  // трогаем, чтобы не менять их обычную ширину/вёрстку.
  document.body.classList.toggle("wide-view", name === "proc");
  if (name === "proc") {
    // display переключился только что — ждём кадр (reflow), чтобы
    // getBoundingClientRect в updateWideViewHeight отдал актуальную геометрию.
    requestAnimationFrame(updateWideViewHeight);
  }
  // Один путь активации вкладки: и клики по кнопкам, и программное переключение
  // (updateRolesUI при скрытии активной вкладки ролью) должны загружать содержимое.
  document.dispatchEvent(new CustomEvent("tab:active", { detail: { name } }));
}

// Доступная высота таблицы/карточки на вкладке «Закупки» (--proc-avail-height,
// используется .proc-list/.proc-detail в zakupki.css) — «до самого низа экрана»
// без знания точных px шапки/вкладок: берём фактическую позицию #view-proc
// (учитывает topnav+header+панель парсера+бар вкладок, что бы над ним ни было)
// и вычитаем из высоты окна.
export function updateWideViewHeight() {
  if (!document.body.classList.contains("wide-view")) return;
  const proc = document.getElementById("view-proc");
  const main = document.querySelector("main");
  if (!proc || !main || proc.style.display === "none") return;
  const top = proc.getBoundingClientRect().top;
  const bottomPad = parseFloat(getComputedStyle(main).paddingBottom) || 0;
  const avail = Math.max(320, window.innerHeight - top - bottomPad - 4);
  document.documentElement.style.setProperty("--proc-avail-height", avail + "px");
}

window.addEventListener("resize", () => {
  if (document.body.classList.contains("wide-view")) updateWideViewHeight();
});

export function updateRolesUI() {
  const guestView = document.getElementById("view-guest");
  const docItems = document.querySelectorAll("#docs-dropdown .dropdown-item");
  const docRoles = state.authUser
    ? (state.authUser.roles || []).filter((r) => r in ROLE_LABELS)
    : ["user"];
  docItems.forEach((b) => {
    b.hidden = !docRoles.includes(b.dataset.guide);
  });
  if (!state.authUser) {
    // Гость (сессии нет): вкладок и ролевых панелей не показываем — вместо них
    // главный экран-приглашение (вход доступен из верхнего меню).
    ALL_TABS.forEach((t) => {
      const btn = document.getElementById("tab-" + t);
      if (btn) {
        btn.classList.remove("active");
        btn.style.display = "none";
      }
      const view = document.getElementById("view-" + t);
      if (view) view.style.display = "none";
    });
    const panel = document.getElementById("parser-panel");
    if (panel) panel.style.display = "none";
    const openAccountBtn = document.getElementById("open-account");
    if (openAccountBtn) openAccountBtn.style.display = "none";
    const trialPill = document.getElementById("user-trial");
    if (trialPill) trialPill.style.display = "none";
    if (guestView) guestView.style.display = "block";
    return;
  }
  if (guestView) guestView.style.display = "none";
  const visible = visibleTabs();
  ALL_TABS.forEach((t) => {
    const btn = document.getElementById("tab-" + t);
    if (btn) btn.style.display = visible.includes(t) ? "" : "none";
  });
  // Кнопка «Кабинет» в шапке и пилюля триала — только ролям с профилем
  // (user/analyst); admin/devops личного кабинета не имеют.
  const openAccountBtn = document.getElementById("open-account");
  if (openAccountBtn) openAccountBtn.style.display = canAccessAccount() ? "" : "none";
  const trialPill = document.getElementById("user-trial");
  if (trialPill && !canAccessAccount()) trialPill.style.display = "none";
  // Панель парсера (Запустить/Остановить/Очистить БД) — только devops.
  const panel = document.getElementById("parser-panel");
  if (panel) panel.style.display = isDevops() ? "" : "none";
  // Клиентская выгрузка CSV — базовые вкладки (роли user/analyst).
  const exportBtn = document.getElementById("db-export");
  if (exportBtn) exportBtn.style.display = hasRole("user") || hasRole("analyst") ? "" : "none";
  // Активная вкладка скрыта ролью (или не выбрана после входа) —
  // переключаемся на первую видимую. Проверяем только основные вкладки
  // (внутри экранов есть свои под-вкладки с тем же классом .active).
  const activeBtn = ALL_TABS.map((t) => document.getElementById("tab-" + t)).find(
    (b) => b && b.classList.contains("active")
  );
  if ((!activeBtn || activeBtn.style.display === "none") && visible.length) {
    switchTo(visible[0]);
  } else {
    // switchTo выше не вызывался (активная вкладка не менялась) — но именно
    // здесь впервые известно, какая вкладка активна при первой отрисовке
    // после входа («Закупки» активна в статичном HTML по умолчанию, switchTo
    // для неё ни разу не вызывается). Синхронизируем wide-view и пересчитываем
    // высоту сами — панель парсера (devops) выше тоже могла только что
    // появиться/исчезнуть, сдвинув #view-proc по вертикали.
    const activeName = activeBtn ? activeBtn.id.slice("tab-".length) : null;
    document.body.classList.toggle("wide-view", activeName === "proc");
    if (activeName === "proc") requestAnimationFrame(updateWideViewHeight);
  }
}
