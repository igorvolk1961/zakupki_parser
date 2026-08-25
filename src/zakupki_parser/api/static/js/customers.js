"use strict";

// Вкладка «Заказчики»: справочник и рейтинги (ADR-4).
import { $, escapeHtml } from "./utils.js";
import { api } from "./api.js";

let lastCustSig = "";
function sigOfCust(data) {
  return (
    data.total +
    ":" +
    data.items.map((c) => c.id + "|" + (c.inn || "") + "|" + (c.rating ?? "")).join(",")
  );
}

function renderCustomers(data) {
  $("#cnt-cust").textContent = data.total;
  const rows =
    data.items
      .map(
        (c) => `
    <tr>
      <td>#${c.id}</td>
      <td>${escapeHtml(c.name)}</td>
      <td><span class="muted">${escapeHtml(c.inn || "—")}</span></td>
      <td>${c.rating == null ? "–" : c.rating}</td>
    </tr>`
      )
      .join("");
  $("#customers").innerHTML = `
    <table class="cust">
      <thead><tr><th>ID</th><th>Заказчик</th><th>ИНН</th><th>Рейтинг</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="muted">Заказчиков нет</td></tr>'}</tbody>
    </table>`;
}

async function loadCustomers() {
  const data = await api("customers", { limit: 100 });
  lastCustSig = sigOfCust(data);
  renderCustomers(data);
}

// Обновляем заказчиков, только если они изменились.
async function pollCustomers() {
  try {
    const data = await api("customers", { limit: 100 });
    const sig = sigOfCust(data);
    if (sig !== lastCustSig) {
      lastCustSig = sig;
      renderCustomers(data);
    }
  } catch (err) {
    /* временные сбои игнорируем */
  }
}

export { loadCustomers, pollCustomers };
