// Условия отчётных полей профиля (scoring_common.conditions): операторы,
// подписи и человекочитаемый текст условия — общие для редактора профиля и
// карточки закупки. Проверку условия выполняет сервер (код, без LLM — кроме
// оператора «соответствует по смыслу»).

// Оператор -> вид значения условия и типы полей, к которым он применим
// (зеркало OPERATORS в scoring_common/conditions.py).
export const CONDITION_OPS = {
  eq: { label: "равно", kind: "scalar", types: ["string", "number", "date", "boolean"] },
  ne: { label: "не равно", kind: "scalar", types: ["string", "number", "date", "boolean"] },
  gt: { label: "больше", kind: "scalar", types: ["number", "date"] },
  gte: { label: "не меньше", kind: "scalar", types: ["number", "date"] },
  lt: { label: "меньше", kind: "scalar", types: ["number", "date"] },
  lte: { label: "не больше", kind: "scalar", types: ["number", "date"] },
  contains: { label: "содержит", kind: "scalar", types: ["string", "list"] },
  in: { label: "входит в список", kind: "list", types: ["string", "number"] },
  not_in: { label: "не входит в список", kind: "list", types: ["string", "number"] },
  all_in: { label: "все значения входят в список", kind: "list", types: ["list"] },
  any_in: { label: "хотя бы одно входит в список", kind: "list", types: ["list"] },
  none_in: { label: "ни одно не входит в список", kind: "list", types: ["list"] },
  llm: {
    label: "соответствует по смыслу (оценка LLM)",
    kind: "scalar",
    types: ["string", "number", "date", "boolean", "list"],
  },
};

export const REPORT_FIELD_TYPE_LABELS = {
  string: "Строка",
  number: "Число",
  date: "Дата",
  boolean: "Да/нет",
  list: "Список",
};

// Почему условие не проверено (check_status с сервера).
export const CHECK_STATUS_LABELS = {
  not_found_in_tz: "значение не найдено в документах",
  llm_failed: "сбой LLM",
  needs_reanalysis: "условие изменено — нужен повторный анализ",
  invalid_value: "значение не удалось сравнить",
};

export function opsForType(type) {
  return Object.entries(CONDITION_OPS)
    .filter(([, def]) => def.types.includes(type))
    .map(([op]) => op);
}

// Текст условия: «не меньше 500», «все значения входят в список: a; b; c … (всего 12)».
export function conditionText(cond, maxItems = 3) {
  if (!cond || !cond.op) return "";
  const def = CONDITION_OPS[cond.op];
  const label = def ? def.label : cond.op;
  if (Array.isArray(cond.value)) {
    const shown = cond.value.slice(0, maxItems).join("; ");
    const more = cond.value.length > maxItems ? ` … (всего ${cond.value.length})` : "";
    return `${label}: ${shown}${more}`;
  }
  return `${label} ${cond.value ?? ""}`.trim();
}
