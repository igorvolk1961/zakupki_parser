"use strict";

// Вкладки devops: «Конфигурация» (config_ops.yaml), «Управление Логи»
// (config_log.yaml) — форма + расширенный режим; «Парсер» (config_parser.yaml) —
// только чтение.
import { $ } from "./utils.js";
import { api } from "./api.js";
import { renderSchemaForm } from "./form.js";
import { createConfigView } from "./config_view.js";

export let opsDirty = false;
export let logDirty = false;

const opsView = createConfigView("cfgops", "config/ops", {
  onDirty: (v) => {
    opsDirty = v;
  },
});
const logView = createConfigView("logcfg", "config/log", {
  onDirty: (v) => {
    logDirty = v;
  },
});

export function loadOpsConfig() {
  return opsView.load();
}

export function loadLogConfig() {
  return logView.load();
}

export async function loadParserConfig() {
  const [schemaData, cfg] = await Promise.all([
    api("config/parser/schema"),
    api("config/parser"),
  ]);
  renderSchemaForm($("#parser-form"), schemaData.schema, cfg, { readOnly: true });
}
