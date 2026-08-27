"use strict";

// Вкладки devops: «Конфигурация» (config_ops.yaml), «Управление логами»
// (config_log.yaml) и «Парсер» (config_parser.yaml) — форма + расширенный режим.
import { createConfigView } from "./config_view.js";

export let opsDirty = false;
export let logDirty = false;
export let parserDirty = false;

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
const parserView = createConfigView("parser", "config/parser", {
  onDirty: (v) => {
    parserDirty = v;
  },
});

export function loadOpsConfig() {
  return opsView.load();
}

export function loadLogConfig() {
  return logView.load();
}

export function loadParserConfig() {
  return parserView.load();
}
