"""Сбор сайта в настоящем браузере (``PlaywrightDriver``) на локальных страницах.

Каждая фикстура — свой вид пагинации: ссылки ``rel=next``; кнопки-номера,
которые перерисовывает JavaScript (как onlineecology.com — подписи
«Страница N», без ссылок в HTML); «›» с неактивной кнопкой на последней
странице; «Показать ещё»; бесконечная прокрутка. Проверяется, что обход
находит пагинацию и сам определяет её конец.

Медленные (Chromium): запускать явно, ``-m slow``.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from zakupki_parser.config.models import BrowserConfig
from zakupki_parser.sources.crawler import CrawlLimits, CrawlOutcome, crawl
from zakupki_parser.sources.playwright_driver import PlaywrightDriver

pytestmark = pytest.mark.slow

_ROWS = 7  # всего строк данных; по 3 на страницу -> 3 страницы

_SPA_NUMBERED = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<header>Онлайн Экология — фильтр: Сбор Транспортирование Утилизация</header>
<main><table id="rows"></table><div id="paging"></div></main>
<footer>© 2026</footer>
<script>
const ROWS = Array.from({length: %(rows)d},
  (_, i) => `1 11 0${10 + i} 21 49 2 | отход ${i} | Сбор`);
const PER = 3, PAGES = Math.ceil(ROWS.length / PER);
function render(p) {
  setTimeout(() => {
    document.getElementById('rows').innerHTML =
      ROWS.slice((p - 1) * PER, p * PER).map((r) => `<tr><td>${r}</td></tr>`).join('');
    const paging = document.getElementById('paging');
    paging.innerHTML = '';
    for (let n = 1; n <= PAGES; n++) {
      const b = document.createElement('span');
      b.textContent = String(n);
      b.setAttribute('title', 'Страница ' + n);
      b.style.padding = '4px';
      if (n === p) b.className = 'active'; else b.onclick = () => render(n);
      paging.appendChild(b);
    }
  }, 150);
}
render(1);
</script></body></html>"""

_NEXT_ARROW = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<nav>Меню</nav><ul id="rows"></ul>
<div class="pager"><a href="#" id="prev">‹</a><a href="#" id="next">›</a></div>
<script>
const ROWS = Array.from({length: %(rows)d}, (_, i) => 'строка ' + i);
let p = 1; const PER = 3, PAGES = Math.ceil(ROWS.length / PER);
function render() {
  document.getElementById('rows').innerHTML =
    ROWS.slice((p - 1) * PER, p * PER).map((r) => '<li>' + r + '</li>').join('');
  document.getElementById('next').className = p >= PAGES ? 'disabled' : '';
}
document.getElementById('next').onclick = (e) => {
  e.preventDefault();
  if (p < PAGES) { p++; render(); }
};
render();
</script></body></html>"""

_LOAD_MORE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<ul id="rows"></ul><button id="more">Показать ещё</button>
<script>
const ROWS = Array.from({length: %(rows)d}, (_, i) => 'позиция ' + i);
let shown = 0;
function more() {
  const next = ROWS.slice(shown, shown + 3);
  shown += next.length;
  document.getElementById('rows').insertAdjacentHTML(
    'beforeend', next.map((r) => '<li>' + r + '</li>').join(''));
  if (shown >= ROWS.length) document.getElementById('more').remove();
}
document.getElementById('more').onclick = more;
more();
</script></body></html>"""

_INFINITE = """<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">
<div id="rows"></div>
<script>
const ROWS = Array.from({length: %(rows)d}, (_, i) => 'элемент ' + i);
let shown = 0;
function more() {
  const next = ROWS.slice(shown, shown + 3);
  shown += next.length;
  document.getElementById('rows').insertAdjacentHTML('beforeend',
    next.map((r) => '<div style="height:900px">' + r + '</div>').join(''));
}
window.addEventListener('scroll', () => {
  const bottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 5;
  if (bottom && shown < ROWS.length) {
    setTimeout(more, 100);
  }
});
more();
</script></body></html>"""


def _static_pages(root: Path) -> None:
    for n in (1, 2, 3):
        rel = f'<link rel="next" href="/static{n + 1}.html">' if n < 3 else ""
        rows = "".join(f"<li>запись {n}-{i}</li>" for i in range(3))
        (root / f"static{n}.html").write_text(
            f'<!doctype html><html><head><meta charset="utf-8">{rel}</head>'
            f"<body><ul>{rows}</ul></body></html>",
            encoding="utf-8",
        )


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = tmp_path_factory.mktemp("site")
    for name, template in (
        ("spa.html", _SPA_NUMBERED),
        ("arrow.html", _NEXT_ARROW),
        ("more.html", _LOAD_MORE),
        ("infinite.html", _INFINITE),
    ):
        (root / name).write_text(template % {"rows": _ROWS}, encoding="utf-8")
    _static_pages(root)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def _local_ok(host: str) -> None:
    return None


def _crawl(url: str) -> CrawlOutcome:
    async def run() -> CrawlOutcome:
        driver = PlaywrightDriver(BrowserConfig(), page_timeout_s=5.0, check_host=_local_ok)
        async with driver:
            return await crawl(driver, url, CrawlLimits(page_timeout_s=3.0, delay_ms=(0, 0)))

    return asyncio.run(run())


def _joined(outcome: CrawlOutcome) -> str:
    return "\n".join(text for _, text in outcome.pages)


def test_rel_next_links(site: str) -> None:
    outcome = _crawl(f"{site}/static1.html")
    assert outcome.mode == "rel_next"
    assert len(outcome.pages) == 3
    assert outcome.complete
    assert "запись 3-2" in _joined(outcome)


def test_js_numbered_buttons_with_page_titles(site: str) -> None:
    """Как onlineecology: номера страниц рисует JS, в HTML ссылок нет."""
    outcome = _crawl(f"{site}/spa.html")
    assert outcome.mode in ("label", "number")
    assert len(outcome.pages) == 3
    assert outcome.complete
    text = _joined(outcome)
    assert all(f"отход {i}" in text for i in range(_ROWS))


def test_next_arrow_disabled_on_last_page(site: str) -> None:
    outcome = _crawl(f"{site}/arrow.html")
    assert outcome.mode == "label"
    assert len(outcome.pages) == 3
    assert outcome.stop_reason == "no_next"
    assert all(f"строка {i}" in _joined(outcome) for i in range(_ROWS))


def test_load_more_accumulates(site: str) -> None:
    outcome = _crawl(f"{site}/more.html")
    assert outcome.mode == "load_more"
    assert outcome.complete
    text = _joined(outcome)
    assert all(text.count(f"позиция {i}") == 1 for i in range(_ROWS))


def test_infinite_scroll(site: str) -> None:
    outcome = _crawl(f"{site}/infinite.html")
    assert outcome.mode == "scroll"
    assert outcome.complete
    text = _joined(outcome)
    assert all(text.count(f"элемент {i}") == 1 for i in range(_ROWS))


def test_internal_address_blocked_without_override(site: str) -> None:
    """Без подмены проверки хоста браузер не ходит на локальный адрес."""

    async def run() -> None:
        driver = PlaywrightDriver(BrowserConfig(), page_timeout_s=5.0)
        async with driver:
            await driver.open(f"{site}/static1.html")

    with pytest.raises(Exception, match="ERR_BLOCKED_BY_CLIENT"):
        asyncio.run(run())
