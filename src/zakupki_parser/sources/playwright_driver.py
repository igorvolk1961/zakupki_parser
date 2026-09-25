"""``PageDriver`` на Playwright: открытый сайт в отдельном браузере.

Браузер свой (не сессия парсера площадок): куки/хранилище площадок сюда не
попадают, и наоборот. Каждый запрос страницы проходит SSRF-проверку
(``net_safety``): не http/https или внутренний адрес — запрос отменяется.
Картинки/шрифты/видео не загружаются — для текста они не нужны.

Поиск кнопки следующей страницы — один JS-вызов (``_FIND_NEXT_JS``): найденный
элемент помечается атрибутом ``data-zp-next``, по нему же потом нажимается.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Playwright, Route, async_playwright

from zakupki_parser.browser.manager import DEFAULT_UA
from zakupki_parser.browser.stealth import apply_init_scripts
from zakupki_parser.config.models import BrowserConfig
from zakupki_parser.net_safety import UnsafeUrlError, ensure_public_host
from zakupki_parser.sources.crawler import NextStep

_SKIP_RESOURCES = {"image", "media", "font"}
_POLL_S = 0.3

# Основное содержимое страницы (без шапки/подвала/меню) — для отпечатка.
_MAIN_TEXT_JS = """
() => {
  const clone = document.body ? document.body.cloneNode(true) : null;
  if (!clone) return "";
  clone.querySelectorAll('header, footer, nav, [role="navigation"], script, style, noscript')
    .forEach((el) => el.remove());
  return (clone.textContent || "").replace(/\\s+/g, " ").trim();
}
"""

_FIND_NEXT_JS = r"""
(pageNo) => {
  document.querySelectorAll('[data-zp-next]').forEach((e) => e.removeAttribute('data-zp-next'));
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  };
  const disabledCls = /(^|[\s_-])(disabled|inactive)([\s_-]|$)/i;
  const cls = (el) => (el && typeof el.className === 'string' ? el.className : '');
  const isDisabled = (el) =>
    el.disabled === true ||
    el.getAttribute('aria-disabled') === 'true' ||
    disabledCls.test(cls(el)) ||
    disabledCls.test(cls(el.parentElement));
  const hrefOf = (el) => {
    const a = el.closest('a[href]');
    if (!a) return null;
    const raw = a.getAttribute('href') || '';
    if (!raw || raw.startsWith('#') || raw.toLowerCase().startsWith('javascript:')) return null;
    return a.href;
  };
  const found = (el, mode) => {
    el.setAttribute('data-zp-next', '1');
    return { mode, href: hrefOf(el), disabled: isDisabled(el) };
  };

  const rel = document.querySelector('a[rel~="next"][href], link[rel~="next"][href]');
  if (rel && rel.href) return { mode: 'rel_next', href: rel.href, disabled: false };

  const priority = (el) => (el.matches('a, button, [role="button"], [role="link"]') ? 0 : 1);
  const clickable = Array.from(
    document.querySelectorAll('a, button, [role="button"], [role="link"], li, span')
  ).filter(visible).sort((a, b) => priority(a) - priority(b));
  const text = (el) => (el.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const attrs = (el) =>
    ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || ''))
      .replace(/\s+/g, ' ').trim().toLowerCase();
  const next = String(pageNo + 1);

  const reNext = /^(след(ующая|\.)?( страница)?|далее|вперед|вперёд|next( page)?|›|»|→|>|>>)$/i;
  const rePage = new RegExp('(^|\\s)(страница|стр\\.?|page)\\s*' + next + '(\\s|$)', 'i');
  for (const el of clickable) {
    const t = text(el);
    const a = attrs(el);
    if (reNext.test(t) || reNext.test(a) || rePage.test(t) || rePage.test(a)) {
      return found(el, 'label');
    }
  }

  const isNumber = (el) => /^\d+$/.test(text(el));
  for (const el of clickable) {
    if (text(el) !== next) continue;
    let box = el.parentElement;
    for (let depth = 0; box && depth < 3; depth += 1, box = box.parentElement) {
      const numbers = Array.from(box.querySelectorAll('a, button, li, span')).filter(
        (x) => x !== el && visible(x) && isNumber(x)
      );
      if (numbers.length >= 2) return found(el, 'number');
    }
  }

  const reMore = /^(показать|загрузить) (ещё|еще|больше)|^(load|show) more/i;
  for (const el of clickable) {
    if (reMore.test(text(el)) || reMore.test(attrs(el))) return found(el, 'load_more');
  }
  return null;
}
"""


class PlaywrightDriver:
    """Браузерный ``PageDriver``; использовать как ``async with``."""

    def __init__(
        self,
        cfg: BrowserConfig,
        *,
        page_timeout_s: float = 20.0,
        check_host: Callable[[str], Awaitable[None]] = ensure_public_host,
    ) -> None:
        self._cfg = cfg
        # SSRF-проверка хоста каждого запроса; подменяется только в тестах
        # (локальный сервер фикстур).
        self._check_host = check_host
        self._timeout_ms = page_timeout_s * 1000
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._host_ok: dict[str, bool] = {}

    async def __aenter__(self) -> PlaywrightDriver:
        self._pw = await async_playwright().start()
        launch: dict[str, Any] = {"headless": self._cfg.headless}
        if self._cfg.chromium_executable_path:
            launch["executable_path"] = self._cfg.chromium_executable_path
        self._browser = await self._pw.chromium.launch(**launch)
        self._context = await self._browser.new_context(
            locale=self._cfg.locale,
            user_agent=self._cfg.user_agent or DEFAULT_UA,
            viewport={"width": self._cfg.viewport_width, "height": self._cfg.viewport_height},
            ignore_https_errors=self._cfg.ignore_https_errors,
        )
        await apply_init_scripts(self._context, self._cfg.disable_webdriver_flag)
        await self._context.route("**/*", self._guard)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self._timeout_ms)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()

    @property
    def page(self) -> Page:
        assert self._page is not None, "PlaywrightDriver не открыт (async with)"
        return self._page

    async def _guard(self, route: Route) -> None:
        request = route.request
        if request.resource_type in _SKIP_RESOURCES:
            await route.abort()
            return
        parts = urlsplit(request.url)
        if parts.scheme in ("data", "blob"):
            await route.continue_()
            return
        host = parts.hostname or ""
        if parts.scheme not in ("http", "https") or not await self._host_allowed(host):
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _host_allowed(self, host: str) -> bool:
        if host not in self._host_ok:
            try:
                await self._check_host(host)
                self._host_ok[host] = True
            except UnsafeUrlError:
                self._host_ok[host] = False
        return self._host_ok[host]

    async def open(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded")
        await self._settle()

    async def _settle(self) -> None:
        """Ждёт, пока основное содержимое перестанет меняться (SPA дорисовывает)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_ms / 1000
        previous = await self.fingerprint()
        stable = 0
        while loop.time() < deadline and stable < 2:
            await asyncio.sleep(_POLL_S)
            current = await self.fingerprint()
            stable = stable + 1 if current == previous and current else 0
            previous = current

    async def text(self) -> str:
        value = await self.page.evaluate("() => document.body ? document.body.innerText : ''")
        return str(value or "")

    async def fingerprint(self) -> str:
        main = await self.page.evaluate(_MAIN_TEXT_JS)
        return hashlib.sha1(str(main or "").encode("utf-8")).hexdigest()

    async def current_url(self) -> str:
        return self.page.url

    async def find_next(self, page_no: int) -> NextStep | None:
        data = await self.page.evaluate(_FIND_NEXT_JS, page_no)
        if not data:
            return None
        return NextStep(
            mode=data["mode"], href=data.get("href"), disabled=bool(data.get("disabled"))
        )

    async def click_next(self) -> None:
        await self.page.locator("[data-zp-next]").first.click()

    async def scroll_to_bottom(self) -> None:
        await self.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")

    async def wait_change(self, old_fingerprint: str, timeout_s: float) -> str | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while loop.time() < deadline:
            await asyncio.sleep(_POLL_S)
            if await self.fingerprint() != old_fingerprint:
                await self._settle()
                return await self.fingerprint()
        return None
