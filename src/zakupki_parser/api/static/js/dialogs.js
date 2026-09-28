"use strict";

// Универсальный диалог подтверждения (confirmDialog / confirmDialogAsync).
import { $ } from "./utils.js";

let confirmCallback = null;
let confirmCancelCallback = null;

// options: { okLabel, cancelLabel, defaultCancel } — defaultCancel ставит фокус на
// «Отмена» (безопасное действие по умолчанию для предупреждений, где согласие
// пользователя нужно получить явно).
export function confirmDialog(message, onOk, onCancel, options) {
  confirmCallback = onOk || null;
  confirmCancelCallback = onCancel || null;
  const opts = options || {};
  const okBtn = $("#generic-confirm-ok");
  const cancelBtn = $("#generic-confirm-cancel");
  okBtn.textContent = opts.okLabel || "Продолжить";
  cancelBtn.textContent = opts.cancelLabel || "Отмена";
  $("#generic-confirm-message").textContent = message;
  $("#generic-confirm-modal-bg").classList.add("open");
  (opts.defaultCancel ? cancelBtn : okBtn).focus();
}

export function confirmDialogAsync(message, options) {
  return new Promise((resolve) => {
    confirmDialog(message, () => resolve(true), () => resolve(false), options);
  });
}

export function closeConfirmDialog() {
  $("#generic-confirm-modal-bg").classList.remove("open");
  const cb = confirmCancelCallback;
  confirmCallback = null;
  confirmCancelCallback = null;
  if (cb) cb();
}

$("#generic-confirm-cancel").addEventListener("click", closeConfirmDialog);
$("#generic-confirm-ok").addEventListener("click", () => {
  const cb = confirmCallback;
  confirmCallback = null;
  confirmCancelCallback = null;
  $("#generic-confirm-modal-bg").classList.remove("open");
  if (cb) cb();
});
$("#generic-confirm-modal-bg").addEventListener("click", (e) => {
  if (e.target.id === "generic-confirm-modal-bg") closeConfirmDialog();
});
