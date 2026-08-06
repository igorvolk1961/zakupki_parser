"""Извлечение ИНН заказчика — универсальный механизм (ADR-4).

Для каждой площадки способ получения ИНН задаётся в ``config_dom.yaml ->
platforms.<id>.organization``:
  - ``customer_link_selector`` — селектор ссылки на организацию (имя заказчика);
    href — URL страницы организации;
  - ``inn_from_link_regex`` — ИНН извлекается прямо из href (ЕИС 223-ФЗ);
  - ``inn_from_org_page`` — открывать страницу организации и извлекать ИНН
    (по селектору ``inn_page_selector`` или обобщённо — метка «ИНН» + цифры);

Сбой получения (сеть/антиблок/изменилась страница) не прерывает обработку закупки:
возвращается ``None`` (ИНН остаётся nullable, дозаполняется позже).
"""

from __future__ import annotations

import contextlib
import logging
import re

from playwright.async_api import Page

from zakupki_parser.config.models import PlatformDom

logger = logging.getLogger(__name__)

# ИНН: 10 или 12 цифр рядом с меткой «ИНН» (устойчиво к разметке страниц).
_INN_BY_LABEL_RE = re.compile(r"ИНН[^0-9]{0,20}(\d{10,12})", re.IGNORECASE)


def extract_inn_from_text(text: str | None) -> str | None:
    """ИНН из текста страницы организации (обобщённо, по метке «ИНН»)."""
    if not text:
        return None
    m = _INN_BY_LABEL_RE.search(text)
    return m.group(1) if m else None


def _absolute(base_url: str, href: str) -> str:
    if href.startswith("http"):
        return href
    return base_url.rstrip("/") + href


async def capture_customer_link(page: Page, platform: PlatformDom) -> str | None:
    """Возвращает href ссылки на организацию (или None)."""
    org = platform.organization
    if org is None or not org.customer_link_selector:
        return None
    locator = page.locator(org.customer_link_selector).first
    if await locator.count() == 0:
        return None
    return await locator.get_attribute("href")


async def resolve_inn(page: Page, platform: PlatformDom, customer_link: str | None) -> str | None:
    """ИНН заказчика: из org-ссылки или со страницы организации."""
    org = platform.organization
    if org is None or not customer_link:
        return None

    if org.inn_from_link_regex:
        m = re.search(org.inn_from_link_regex, customer_link)
        if m:
            return m.group(1)

    if org.inn_from_org_page:
        return await _inn_from_org_page(page, platform, customer_link, org.inn_page_selector)

    return None


async def _inn_from_org_page(
    page: Page, platform: PlatformDom, customer_link: str, selector: str | None
) -> str | None:
    """Открывает страницу организации в отдельной вкладке и извлекает ИНН."""
    new_page: Page | None = None
    try:
        new_page = await page.context.new_page()
        await new_page.goto(
            _absolute(platform.url, customer_link),
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await new_page.wait_for_timeout(2000)
        if selector:
            locator = new_page.locator(selector).first
            if await locator.count() == 0:
                logger.debug("ИНН не найден по селектору %s", customer_link)
                return None
            text = await locator.inner_text()
        else:
            text = await new_page.inner_text("body")
        inn = extract_inn_from_text(text)
        if inn is None:
            logger.debug("ИНН не найден на странице организации %s", customer_link)
        return inn
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось получить ИНН с org-страницы %s: %s", customer_link, exc)
        return None
    finally:
        if new_page is not None:
            with contextlib.suppress(Exception):
                await new_page.close()
