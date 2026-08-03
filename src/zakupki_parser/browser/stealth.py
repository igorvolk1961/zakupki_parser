"""Минимальный набор stealth-утилит для снижения риска детекции бота.

Всё реализовано без внешних зависимостей, настройки приходят из конфига.
"""

from __future__ import annotations

from playwright.async_api import BrowserContext, Page


async def apply_init_scripts(ctx: BrowserContext, disable_webdriver: bool) -> None:
    """Добавляет init-скрипты, маскирующие признаки автоматизации."""
    if disable_webdriver:
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
    # Фиксируем стабильный набор плагинов и языков
    await ctx.add_init_script(
        """
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {
          get: () => [1, 2, 3, 4, 5]
        });
        """
    )


async def humanize(page: Page, scroll: bool, mouse: bool) -> None:
    """Выполняет «человеческие» движения (скролл и мышь)."""
    if scroll:
        await page.mouse.move(320, 240)
        await page.evaluate("window.scrollBy(0, 200)")
    if mouse:
        await page.mouse.move(520, 340)
