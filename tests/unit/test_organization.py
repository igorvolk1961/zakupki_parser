"""Unit-тесты универсального механизма ИНН заказчика (ADR-4)."""

from __future__ import annotations

from typing import cast

from playwright.async_api import Page

from zakupki_parser.config.models import (
    DomDetailConfig,
    DomListConfig,
    OrganizationConfig,
    PlatformDom,
)
from zakupki_parser.parser.organization import (
    capture_customer_link,
    extract_inn_from_text,
    resolve_inn,
)


def _as_page(obj: object) -> Page:
    return cast(Page, obj)


class _FakeLocator:
    def __init__(self, count: int = 0, href: str | None = None, text: str = "") -> None:
        self._count = count
        self._href = href
        self._text = text

    @property
    def first(self) -> _FakeLocator:
        return self

    async def count(self) -> int:
        return self._count

    async def get_attribute(self, attr: str) -> str | None:
        return self._href

    async def inner_text(self) -> str:
        return self._text


class _FakePage:
    def __init__(self, locator: _FakeLocator, context: _FakeContext | None = None) -> None:
        self._locator = locator
        self.context = context
        self.url = ""
        self.closed = False

    def locator(self, selector: str) -> _FakeLocator:
        return self._locator

    async def inner_text(self, selector: str) -> str:
        return self._locator._text

    async def goto(self, url: str, **kwargs: object) -> None:
        self.url = url

    async def wait_for_timeout(self, ms: int) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, new_page: _FakePage) -> None:
        self._new_page = new_page

    async def new_page(self) -> _FakePage:
        return self._new_page


def _platform(org: OrganizationConfig | None) -> PlatformDom:
    return PlatformDom(
        name="test",
        url="https://platform.example",
        list_path="/list",
        list_config=DomListConfig(container=".c", detail_link="a.d", next_page="a.next"),
        detail=DomDetailConfig(),
        organization=org,
    )


class TestExtractInnFromText:
    def test_inn_with_label(self) -> None:
        assert extract_inn_from_text("ИНН 7701234567") == "7701234567"
        assert extract_inn_from_text("ИНН: 3903007130, КПП 390601001") == "3903007130"

    def test_inn_in_markup(self) -> None:
        assert extract_inn_from_text("ИНН</dt><dd>771234567812</dd>") == "771234567812"

    def test_no_inn(self) -> None:
        assert extract_inn_from_text("Нет данных") is None
        assert extract_inn_from_text(None) is None


class TestCaptureCustomerLink:
    async def test_returns_href(self) -> None:
        org = OrganizationConfig(customer_link_selector="a.customer")
        page = _FakePage(_FakeLocator(count=1, href="/companyProfile/customer/1201682"))
        assert await capture_customer_link(_as_page(page), _platform(org)) == (
            "/companyProfile/customer/1201682"
        )

    async def test_none_when_missing(self) -> None:
        page = _FakePage(_FakeLocator(count=0))
        assert await capture_customer_link(_as_page(page), _platform(None)) is None
        assert (
            await capture_customer_link(
                _as_page(page), _platform(OrganizationConfig(customer_link_selector="a"))
            )
            is None
        )


class TestResolveInn:
    async def test_from_link_regex(self) -> None:
        org = OrganizationConfig(
            customer_link_selector="a",
            inn_from_link_regex=r"inn=(\d{10,12})",
        )
        page = _FakePage(_FakeLocator())
        link = "https://zakupki.gov.ru/epz/organization/view223/info.html?&inn=3903007130"
        assert await resolve_inn(_as_page(page), _platform(org), link) == "3903007130"

    async def test_none_without_regex_or_selector(self) -> None:
        org = OrganizationConfig(customer_link_selector="a")
        page = _FakePage(_FakeLocator())
        assert await resolve_inn(_as_page(page), _platform(org), "/some/link") is None

    async def test_from_org_page(self) -> None:
        org = OrganizationConfig(
            customer_link_selector="a", inn_from_org_page=True, inn_page_selector="span.inn"
        )
        org_page = _FakePage(_FakeLocator(count=1, text="ИНН 3903007130"))
        context = _FakeContext(org_page)
        page = _FakePage(_FakeLocator(), context=context)
        inn = await resolve_inn(_as_page(page), _platform(org), "/companyProfile/customer/1201682")
        assert inn == "3903007130"
        assert org_page.closed is True

    async def test_from_org_page_generic_without_selector(self) -> None:
        org = OrganizationConfig(customer_link_selector="a", inn_from_org_page=True)
        org_page = _FakePage(_FakeLocator(count=1, text="ИНН 3903007130"))
        context = _FakeContext(org_page)
        page = _FakePage(_FakeLocator(), context=context)
        inn = await resolve_inn(_as_page(page), _platform(org), "/companyProfile/customer/1201682")
        assert inn == "3903007130"

    async def test_org_page_no_inn_returns_none(self) -> None:
        org = OrganizationConfig(
            customer_link_selector="a", inn_from_org_page=True, inn_page_selector="span.inn"
        )
        org_page = _FakePage(_FakeLocator(count=0))
        context = _FakeContext(org_page)
        page = _FakePage(_FakeLocator(), context=context)
        assert (
            await resolve_inn(_as_page(page), _platform(org), "/companyProfile/customer/1") is None
        )

    async def test_org_page_disabled_does_not_open(self) -> None:
        org = OrganizationConfig(customer_link_selector="a", inn_page_selector="span.inn")
        page = _FakePage(_FakeLocator())
        assert (
            await resolve_inn(_as_page(page), _platform(org), "/companyProfile/customer/1") is None
        )
