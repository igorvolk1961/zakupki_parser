"""Захват реальных HTML-страниц площадки для тестовых фикстур.

Позволяет сохранить страницу списка и детальную страницу (в урезанном виде)
в ``tests/fixtures``, чтобы интеграционные тесты не зависели от живого сайта.
"""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import Page

from zakupki_parser.browser.manager import BrowserManager
from zakupki_parser.config.loader import load_config

_LIST_MARKER = "PublicListStyles__PublicListContainer"
_DETAIL_MARKER = "О портале"


async def capture_fixtures(cfg_dir: str, platform_id: str, out: str) -> None:
    cfg = load_config(cfg_dir)
    platform = cfg.dom.platforms[platform_id]
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)

    browser = BrowserManager(cfg.parser.browser)
    try:
        await browser.start()
        page = await browser.new_page()
        await page.goto(
            platform.url.rstrip("/") + platform.list_path,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await page.wait_for_timeout(4000)
        await _trim_save(page, out_dir / "list_cardregion.html", _LIST_MARKER)
        print("Сохранён список:", out_dir / "list_cardregion.html")

        # первая ссылка на детали
        link = await page.locator("a[href^='/need/']").first.get_attribute("href")
        if link:
            await page.goto(platform.url.rstrip("/") + link, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            await _trim_save(page, out_dir / "detail_content.html", _DETAIL_MARKER)
            print("Сохранена деталь:", out_dir / "detail_content.html")
        else:
            print("Предупреждение: ссылка на детальную страницу не найдена")
    finally:
        await browser.close()


async def _trim_save(page: Page, path: Path, marker: str) -> None:
    html = await page.content()
    i = html.find(marker)
    end = html.find("auth/realms")
    if end < 0:
        end = len(html)
    region = html[i:end] if i >= 0 else html[:end]
    path.write_text(region, encoding="utf-8")
    print(f"  размер региона: {len(region)} байт")
