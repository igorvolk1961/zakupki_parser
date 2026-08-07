"""FastAPI-приложение имитатора zakupki.mos.ru.

Обрабатывает страницы, которые посещает «Парсер закупок» (Playwright), и отдаёт
HTML в DOM-структуре, заданной в demo-конфиге.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from zakupki_mos_simulator.config import DomSelectors, load_dom_selectors
from zakupki_mos_simulator.data.dataset import load_dataset
from zakupki_mos_simulator.data.models import Dataset
from zakupki_mos_simulator.web.render_detail import render_detail
from zakupki_mos_simulator.web.render_list import render_list
from zakupki_mos_simulator.web.render_org import render_org

logger = logging.getLogger(__name__)


class SimulatorApp:
    """Контейнер приложения имитатора (для инъекции датасета в тестах)."""

    def __init__(
        self,
        dataset: Dataset | None = None,
        selectors: DomSelectors | None = None,
    ) -> None:
        self.dataset = dataset if dataset is not None else load_dataset()
        self.selectors = selectors or load_dom_selectors()
        self._by_id = {p.id: p for p in self.dataset.procurements}
        self._customers = {c.customer_id: c for c in self.dataset.customers}
        self._inn_selector = self.selectors.organization.get("inn_page_selector")
        self.app = FastAPI(title="zakupki.mos.ru imitator", version="0.1.0")
        self._register()

    def _register(self) -> None:
        app = self.app

        @app.get("/purchase/list", response_class=HTMLResponse)
        async def list_page(
            page: int = Query(default=1, ge=1),
            perPage: int | None = Query(default=None, ge=1),
            filter: str | None = None,  # noqa: A002 - параметр площадки
            state: str | None = None,
        ) -> str:
            page_size = perPage or 10
            return render_list(
                self.dataset.procurements,
                self.selectors,
                page=page,
                page_size=page_size,
            )

        @app.get("/need/{procurement_id}", response_class=HTMLResponse)
        async def detail_page(procurement_id: int) -> Response:
            p = self._by_id.get(procurement_id)
            if p is None:
                return PlainTextResponse("Закупка не найдена", status_code=404)
            return HTMLResponse(render_detail(p))

        @app.get("/companyProfile/customer/{customer_id}", response_class=HTMLResponse)
        async def org_page(customer_id: int) -> Response:
            customer = self._customers.get(customer_id)
            inn_selector = self._inn_selector
            return HTMLResponse(render_org(customer, customer_id, inn_selector))

        @app.get("/api/FileStorage/Download")
        async def file_download(id: int) -> Response:  # noqa: A002
            # Стаб: отдаём бинарные данные для демонстрации режима download_files.
            content = f"Демо-файл закупки #{id} (имитатор)".encode()
            return Response(
                content=content,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="file_{id}.bin"'},
            )

        @app.get("/health", response_class=HTMLResponse)
        async def health() -> str:
            return "<html><body>ok</body></html>"


def create_app(
    dataset: Dataset | None = None,
    selectors: DomSelectors | None = None,
) -> FastAPI:
    """Создаёт FastAPI-приложение имитатора."""
    return SimulatorApp(dataset=dataset, selectors=selectors).app
