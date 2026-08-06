"""Веб-имитатор: FastAPI-приложение и рендеры страниц."""

from zakupki_mos_simulator.web.app import SimulatorApp, create_app
from zakupki_mos_simulator.web.dom_classes import LABELED_VALUE_CLASS
from zakupki_mos_simulator.web.render_detail import render_detail
from zakupki_mos_simulator.web.render_list import (
    PAGE_SIZE_DEFAULT,
    render_list,
    sorted_procurements,
)
from zakupki_mos_simulator.web.render_org import DEFAULT_INN_SELECTOR, render_org

__all__ = [
    "DEFAULT_INN_SELECTOR",
    "LABELED_VALUE_CLASS",
    "PAGE_SIZE_DEFAULT",
    "SimulatorApp",
    "create_app",
    "render_detail",
    "render_list",
    "render_org",
    "sorted_procurements",
]
