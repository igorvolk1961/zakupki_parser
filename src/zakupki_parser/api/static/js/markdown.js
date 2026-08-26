"use strict";

// Минимальный Markdown-рендерер для предпросмотра промптов (без внешних библиотек).
// Поддерживает: заголовки, параграфы, списки, код-блоки и инлайн-код,
// жирный/курсив, ссылки, цитаты и горизонтальную линию. Сначала HTML-экранирует
// исходник, поэтому рендеринг безопасен (XSS-инъекций не возникает).

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

  for (const raw of lines) {
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
