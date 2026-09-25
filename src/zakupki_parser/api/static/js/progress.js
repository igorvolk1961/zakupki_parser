"use strict";

// Ход длительных операций: сбор сайта-источника и пересчёт условий профиля.
// Пользователь всегда видит, что происходит: опрос статуса раз в 2 с, пока
// операция идёт; опрос останавливается сам, когда элемент исчез со страницы
// (форму закрыли) или операция закончилась.
import { escapeHtml } from "./utils.js";
import { api, apiJSON, apiErrorDetail } from "./api.js";

const POLL_MS = 2000;

// Повторяет fetchFn, пока isActive(data) и элемент el на странице.
export function poll(el, fetchFn, render, isActive) {
  let timer = null;
  let stopped = false;
  const tick = async () => {
    if (stopped || !document.body.contains(el)) return;
    let data = null;
    try {
      data = await fetchFn();
    } catch (err) {
      render(null, err);
      return;
    }
    render(data, null);
    if (isActive(data) && !stopped) timer = setTimeout(tick, POLL_MS);
  };
  tick();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}

const MODE_LABELS = {
  rel_next: "ссылка «следующая»",
  label: "кнопка «следующая»",
  number: "номера страниц",
  load_more: "«Показать ещё»",
  url_param: "номер страницы в адресе",
  scroll: "прокрутка",
};

const STOP_LABELS = {
  no_next: "страниц больше нет",
  repeat: "страницы начали повторяться",
  no_change: "содержимое перестало меняться",
  page_limit: "достигнут лимит страниц",
  size_limit: "достигнут лимит объёма",
  time_limit: "вышло время сбора",
  cancelled: "остановлен",
  nav_failed: "не удалось перейти на следующую страницу",
  error: "ошибка",
};

function fmtChars(n) {
  if (!n) return "0 симв.";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(".", ",")} млн симв.`;
  if (n >= 1e3) return `${Math.round(n / 1e3)} тыс. симв.`;
  return `${n} симв.`;
}

function fmtElapsed(s) {
  const sec = Math.round(Number(s) || 0);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function fmtDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("ru-RU");
  } catch (err) {
    return iso;
  }
}

// Текст статуса сайта-источника (без кнопок).
export function sourceStatusText(src) {
  if (!src) return "Сайт поставщика ещё не собирался.";
  const p = src.progress || {};
  if (src.active || src.status === "running" || src.status === "pending") {
    if (!p.pages) return "⏳ Сбор сайта поставщика в очереди…";
    const mode = MODE_LABELS[p.mode] ? ` · переход: ${MODE_LABELS[p.mode]}` : "";
    return `⏳ Собрано ${p.pages} стр. · ${fmtChars(p.chars)} · ${fmtElapsed(p.elapsed_s)}${mode}`;
  }
  // Статус описывает ТЕКСТ, по которому проверяются условия (полный текст не
  // заменяется неполным пересбором), и отдельно — итог последнего пересбора.
  const reason = STOP_LABELS[src.stop_reason] || src.stop_reason || "";
  const when = src.fetched_at ? `, ${fmtDate(src.fetched_at)}` : "";
  const lastRun =
    src.status === "complete"
      ? ""
      : src.status === "failed"
        ? ` Последний пересбор не удался: ${escapeHtml(src.error || reason)}.`
        : ` Последний пересбор: ${reason}.`;
  if (src.fetched_at && src.text_complete)
    return `✓ Текст сайта поставщика полный: ${src.pages} стр., ${fmtChars(src.text_chars)}${when}.${lastRun}`;
  if (src.fetched_at)
    return `◐ Текст сайта поставщика неполный: ${src.pages} стр.${when} — ${reason}. Отсутствие значения на сайте поставщика не доказано.`;
  return `✗ Сайт поставщика не собран: ${escapeHtml(src.error || reason)}.`;
}

// Виджет статуса сайта: текст, текущий адрес и кнопки «Собрать/Обновить»,
// «Остановить». Возвращает функцию остановки опроса.
export function mountSourceStatus(el, url) {
  let stop = () => {};
  let source = null;
  // Постоянная разметка: при опросе меняются только текст и подпись кнопки —
  // кнопку не пересоздаём (иначе нажатие может попасть в исчезающий элемент).
  el.innerHTML = `<span data-part="text">Проверяю сайт поставщика…</span> <button type="button" class="ghost btn-mini" data-part="btn" style="display:none;"></button><span data-part="url" class="muted" style="display:block;font-size:12px;word-break:break-all;"></span>`;
  const textEl = el.querySelector('[data-part="text"]');
  const btn = el.querySelector('[data-part="btn"]');
  const urlEl = el.querySelector('[data-part="url"]');
  const isActive = (src) =>
    !!src && (src.active || src.status === "pending" || src.status === "running");
  const render = (src, err) => {
    if (err) {
      textEl.innerHTML = `<span class="error">${escapeHtml(err.message || String(err))}</span>`;
      return;
    }
    source = src;
    const active = isActive(src);
    textEl.innerHTML = sourceStatusText(src);
    btn.dataset.act = active ? "cancel" : "collect";
    btn.textContent = active ? "Остановить" : src ? "Обновить" : "Собрать";
    btn.disabled = false;
    btn.style.display = "";
    urlEl.textContent = active && src.progress ? src.progress.current_url || "" : "";
  };
  const lookup = async () => {
    const r = await apiJSON(`/api/sources/lookup?url=${encodeURIComponent(url)}`);
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(await apiErrorDetail(r));
    return r.json();
  };
  const follow = () => {
    stop();
    stop = poll(el, () => (source ? api(`/api/sources/${source.id}`) : lookup()), render, isActive);
  };
  btn.addEventListener("click", async () => {
    const act = btn.dataset.act;
    btn.disabled = true;
    try {
      let r;
      if (act === "cancel") r = await apiJSON(`/api/sources/${source.id}/cancel`, { method: "POST" });
      else if (source) r = await apiJSON(`/api/sources/${source.id}/refresh`, { method: "POST" });
      else
        r = await apiJSON("/api/sources", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        });
      if (!r.ok) throw new Error(await apiErrorDetail(r));
      render(await r.json(), null);
      follow();
    } catch (err) {
      render(source, err);
    }
  });
  stop = poll(el, lookup, render, isActive);
  return () => stop();
}

// Ход пересчёта условий профиля (без LLM) — после сохранения профиля и
// окончания сбора сайта. onDone — когда закончился (обновить список закупок).
// Какие завершения пересчёта уже показаны (profileId -> finished_at): итог
// сообщается один раз, иначе обновление списка по onDone снова видело бы
// «только что закончился».
const reportedRecheck = {};

export function watchRecheck(profileId, el, onDone) {
  let wasRunning = false;
  return poll(
    el,
    () => api(`/api/clients/${profileId}/recheck`),
    (st) => {
      if (!st) return;
      if (st.running) {
        wasRunning = true;
        el.style.display = "";
        el.textContent = `Пересчёт условий отчётных полей: ${st.done} / ${st.total} закупок…`;
        return;
      }
      // Небольшой профиль пересчитывается быстрее первого опроса — итог всё
      // равно показываем, если пересчёт закончился только что.
      const justFinished =
        st.finished_at && Date.now() - new Date(st.finished_at).getTime() < 15000;
      const fresh = reportedRecheck[profileId] !== st.finished_at;
      if (fresh && (wasRunning || (justFinished && st.total))) {
        reportedRecheck[profileId] = st.finished_at;
        el.textContent = st.stale
          ? `Условия пересчитаны: ${st.total} закупок; ${st.stale} требуют повторного анализа (изменилось само поле).`
          : `Условия пересчитаны: ${st.total} закупок.`;
        if (onDone) onDone(st);
        setTimeout(() => {
          if (el.textContent.startsWith("Условия пересчитаны")) el.style.display = "none";
        }, 8000);
      } else {
        el.style.display = "none";
      }
    },
    (st) => !!st && st.running
  );
}
