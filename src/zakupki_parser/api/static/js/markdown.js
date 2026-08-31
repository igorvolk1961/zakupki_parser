"use strict";

// Минимальный Markdown-рендерер для предпросмотра промптов и просмотра ТЗ
// (без внешних библиотек). Поддерживает: заголовки, параграфы, списки,
// код-блоки и инлайн-код, жирный/курсив, ссылки, цитаты, горизонтальную линию
// и GFM-таблицы (``| a | b |`` с разделителем ``| --- |``). Сначала HTML-
// экранирует исходник, поэтому рендеринг безопасен (XSS-инъекций нет).

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(s) {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function isPipeRow(s) {
  return /^\s*\|.*\|\s*$/.test(s);
}

function pipeCells(s) {
  return s.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

function isSeparatorRow(cells) {
  return cells.length > 0 && cells.every((c) => /^:?-{2,}:?$/.test(c));
}

// Собрать из последовательных pipe-строк GFM-таблицу. Возвращает {html, end}
// либо null, если блок не таблица (нет строки-разделителя после заголовка).
function renderTable(lines, start) {
  let i = start;
  const rows = [];
  while (i < lines.length && isPipeRow(lines[i])) {
    rows.push(lines[i]);
    i++;
  }
  const cells = rows.map(pipeCells);
  const sepIdx = cells.findIndex(isSeparatorRow);
  if (sepIdx <= 0) return null; // заголовок + разделитель обязательны
  const header = cells[sepIdx - 1];
  const data = cells.slice(sepIdx + 1);
  const headHtml = header.map((c) => `<th>${inline(c)}</th>`).join("");
  const bodyHtml = data
    .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`)
    .join("");
  return { html: `<table><thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`, end: i };
}

export function renderMarkdown(text) {
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  const html = [];
  let inCode = false;
  let codeBuf = [];
  let list = null;
  let liIndex = -1;

  const liPush = (content) => {
    html.push(`<li>${content}</li>`);
    liIndex = html.length - 1;
  };

  const closeList = () => {
    if (list) {
      html.push(`</${list}>`);
      list = null;
      liIndex = -1;
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    if (raw.trim().startsWith("```")) {
      closeList();
      if (inCode) {
        html.push(`<pre><code>${codeBuf.join("\n")}</code></pre>`);
        codeBuf = [];
        inCode = false;
      } else {
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuf.push(raw);
      continue;
    }
    const t = raw.trim();
    if (!t) {
      closeList();
      continue;
    }
    // GFM-таблица — обрабатываем блок строк сразу (может занимать весь абзац).
    if (isPipeRow(raw.trim())) {
      const table = renderTable(lines, i);
      if (table) {
        closeList();
        html.push(table.html);
        i = table.end - 1;
        continue;
      }
    }
    const heading = t.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
      closeList();
      html.push("<hr>");
      continue;
    }
    if (t.startsWith("> ")) {
      closeList();
      html.push(`<blockquote>${inline(t.slice(2))}</blockquote>`);
      continue;
    }
    const ul = t.match(/^[-*]\s+(.*)$/);
    if (ul) {
      if (list !== "ul") {
        closeList();
        html.push("<ul>");
        list = "ul";
      }
      liPush(inline(ul[1]));
      continue;
    }
    const ol = t.match(/^\d+[.)]\s+(.*)$/);
    if (ol) {
      if (list !== "ol") {
        closeList();
        html.push("<ol>");
        list = "ol";
      }
      liPush(inline(ol[1]));
      continue;
    }
    if (list) {
      // Перенесённая строка текущего пункта списка (без маркера).
      html[liIndex] = html[liIndex].replace(/<\/li>$/, ` ${inline(t)}</li>`);
      continue;
    }
    closeList();
    html.push(`<p>${inline(t)}</p>`);
  }
  if (inCode) html.push(`<pre><code>${codeBuf.join("\n")}</code></pre>`);
  closeList();
  return html.join("\n");
}
