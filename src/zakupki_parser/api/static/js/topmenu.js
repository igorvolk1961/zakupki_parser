"use strict";

// Верхнее меню главной страницы: «Документация», «Техподдержка», «Контакты»,
// «Вход». «Документация» показывает руководство пользователя (docs/user-guide.md
// отдаётся публичным эндпоинтом /api/docs/user-guide и рендерится клиентом);
// «Техподдержка»/«Контакты» — модальные заглушки.
import { $ } from "./utils.js";
import { hideLogin, showLogin } from "./auth.js";
import { renderMarkdown } from "./markdown.js";

let guideLoaded = false;

export function closeDocsModal() {
  $("#docs-modal-bg").classList.remove("open");
}

export function closeInfoModal() {
  $("#info-modal-bg").classList.remove("open");
}

export async function openDocs() {
  const bg = $("#docs-modal-bg");
  const content = $("#docs-content");
  bg.classList.add("open");
  if (guideLoaded) return;
  content.innerHTML = '<div class="muted">Загрузка…</div>';
  try {
    const r = await fetch("/api/docs/user-guide", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    content.innerHTML = renderMarkdown(await r.text());
    guideLoaded = true;
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

$("#menu-docs").addEventListener("click", openDocs);
$("#menu-support").addEventListener("click", () =>
  openInfo("Техподдержка", "Раздел «Техподдержка» находится в разработке.")
);
$("#menu-contacts").addEventListener("click", () =>
  openInfo("Контакты", "Раздел «Контакты» находится в разработке.")
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
$("#guest-docs").addEventListener("click", openDocs);

$("#docs-close").addEventListener("click", closeDocsModal);
$("#docs-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "docs-modal-bg") closeDocsModal();
});
$("#info-close").addEventListener("click", closeInfoModal);
$("#info-ok").addEventListener("click", closeInfoModal);
$("#info-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "info-modal-bg") closeInfoModal();
});
