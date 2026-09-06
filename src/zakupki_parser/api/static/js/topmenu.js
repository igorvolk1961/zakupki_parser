"use strict";

// Верхнее меню главной страницы: «Документация» (выпадающий список руководств
// по ролям), «Техподдержка», «Контакты», «Вход». Руководства отдаются
// эндпоинтом /api/docs/guide/<role> и рендерятся клиентом; пункты меню
// соответствуют ролям вошедшего пользователя (видимость — roles.updateRolesUI;
// гостю доступно только руководство роли user).
import { $ } from "./utils.js";
import { hideLogin, showLogin } from "./auth.js";
import { authHeaders } from "./api.js";
import { renderMarkdown } from "./markdown.js";

const GUIDE_TITLES = {
  user: "Руководство пользователя",
  admin: "Руководство администратора",
  analyst: "Руководство аналитика",
  devops: "Руководство DevOps",
};

const guideCache = new Map(); // роль -> отрендеренный HTML (избегаем повторной загрузки)

export function closeDocsModal() {
  $("#docs-modal-bg").classList.remove("open");
}

export function closeInfoModal() {
  $("#info-modal-bg").classList.remove("open");
}

function setDocsMenu(open) {
  $("#docs-dropdown").hidden = !open;
  $("#menu-docs").setAttribute("aria-expanded", String(open));
}

function closeDocsMenu() {
  setDocsMenu(false);
}

export async function openDocs(role) {
  const content = $("#docs-content");
  $("#docs-title").textContent = GUIDE_TITLES[role] || "Руководство";
  $("#docs-modal-bg").classList.add("open");
  if (guideCache.has(role)) {
    content.innerHTML = guideCache.get(role);
    return;
  }
  content.innerHTML = '<div class="muted">Загрузка…</div>';
  try {
    const r = await fetch("/api/docs/guide/" + encodeURIComponent(role), {
      headers: authHeaders(),
      cache: "no-store",
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const html = renderMarkdown(await r.text());
    guideCache.set(role, html);
    content.innerHTML = html;
  } catch (err) {
    content.innerHTML =
      '<div class="muted">Не удалось загрузить руководство: ' + String(err) + "</div>";
  }
}

function openInfo(title, message) {
  $("#info-title").textContent = title;
  $("#info-message").textContent = message;
  $("#info-modal-bg").classList.add("open");
}

$("#menu-docs").addEventListener("click", (e) => {
  e.stopPropagation();
  setDocsMenu($("#docs-dropdown").hidden);
});

$("#docs-dropdown").addEventListener("click", (e) => {
  const btn = e.target.closest(".dropdown-item");
  if (!btn || btn.hidden) return;
  closeDocsMenu();
  openDocs(btn.dataset.guide);
});

document.addEventListener("click", (e) => {
  if (!e.target.closest("#docs-menu")) closeDocsMenu();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDocsMenu();
});

$("#menu-support").addEventListener("click", () =>
  openInfo("Техподдержка", "Раздел «Техподдержка» находится в разработке.")
);
$("#menu-contacts").addEventListener("click", () =>
  openInfo("Контакты", "Раздел «Контакты» находится в разработке.")
);
$("#menu-tariffs").addEventListener("click", () =>
  openInfo("Тарифы", "Раздел «Тарифы» находится в разработке.")
);
$("#menu-login").addEventListener("click", () => {
  hideLogin();
  showLogin();
});
// Гостевой главный экран: те же действия, что и в верхнем меню.
$("#guest-login").addEventListener("click", () => {
  hideLogin();
  showLogin();
});
// Гость видит только руководство роли user.
$("#guest-docs").addEventListener("click", () => openDocs("user"));

$("#docs-close").addEventListener("click", closeDocsModal);
$("#docs-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "docs-modal-bg") closeDocsModal();
});
$("#info-close").addEventListener("click", closeInfoModal);
$("#info-ok").addEventListener("click", closeInfoModal);
$("#info-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "info-modal-bg") closeInfoModal();
});
