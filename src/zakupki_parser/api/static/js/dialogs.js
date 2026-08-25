"use strict";

// Универсальный диалог подтверждения (confirmDialog / confirmDialogAsync).
import { $ } from "./utils.js";

let confirmCallback = null;
let confirmCancelCallback = null;

export function confirmDialog(message, onOk, onCancel) {
  confirmCallback = onOk || null;
  confirmCancelCallback = onCancel || null;
  $("#generic-confirm-message").textContent = message;
  $("#generic-confirm-modal-bg").classList.add("open");
}

export function confirmDialogAsync(message) {
  return new Promise((resolve) => {
    confirmDialog(message, () => resolve(true), () => resolve(false));
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
