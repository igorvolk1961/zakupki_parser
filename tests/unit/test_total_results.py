"""Unit-тесты извлечения общего числа результатов поиска (ранний пропуск прохода)."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page
from tests.conftest import set_html

from zakupki_parser.config.models import AppConfig
from zakupki_parser.parser.lister import extract_total_results


def _platform(app_config: AppConfig, **list_config_overrides: Any) -> Any:
    plat = app_config.dom.platforms["zakupki_mos"].model_copy(deep=True)
    lc = plat.list_config.model_dump()
    lc.update(list_config_overrides)
    plat.list_config = type(plat.list_config)(**lc)
    return plat


async def test_no_selector_returns_none(page: Page, app_config: AppConfig) -> None:
    await set_html(page, "<div>Найдено: 42</div>")
    plat = _platform(app_config, total_results_selector=None)
    assert await extract_total_results(page, plat) is None


async def test_plain_digits(page: Page, app_config: AppConfig) -> None:
    await set_html(page, '<div class="cnt">Найдено: 42 закупки</div>')
    plat = _platform(app_config, total_results_selector=".cnt")
    assert await extract_total_results(page, plat) == 42


async def test_regex_group(page: Page, app_config: AppConfig) -> None:
    await set_html(page, '<div class="cnt">1–10 из 350 закупок</div>')
    plat = _platform(
        app_config,
        total_results_selector=".cnt",
        total_results_regex=r"из\s+(\d+)",
    )
    assert await extract_total_results(page, plat) == 350


async def test_regex_no_group_uses_whole_match(page: Page, app_config: AppConfig) -> None:
    await set_html(page, '<div class="cnt">Всего: 7</div>')
    plat = _platform(
        app_config,
        total_results_selector=".cnt",
        total_results_regex=r"\d+",
    )
    assert await extract_total_results(page, plat) == 7


async def test_selector_missing_returns_none(page: Page, app_config: AppConfig) -> None:
    await set_html(page, "<div>нет нужного селектора</div>")
    plat = _platform(app_config, total_results_selector=".missing")
    assert await extract_total_results(page, plat) is None


async def test_no_digits_returns_none(page: Page, app_config: AppConfig) -> None:
    await set_html(page, '<div class="cnt">закупок не найдено</div>')
    plat = _platform(app_config, total_results_selector=".cnt")
    assert await extract_total_results(page, plat) is None
