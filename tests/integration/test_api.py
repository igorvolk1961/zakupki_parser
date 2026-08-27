"""Интеграционные тесты FastAPI-сервиса (требуют PostgreSQL)."""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from zakupki_parser.api.app import create_app
from zakupki_parser.auth import ROLE_ADMIN, ROLE_USER, create_token
from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database
from zakupki_parser.storage.repository import ProcurementRepository

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")
AUTH_SECRET = "test-secret"

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")


@pytest.fixture(scope="module")
def api_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[TestClient, Path]]:
    async def _setup() -> int:
        engine = create_async_engine(TEST_DSN)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()
        # Сид пользователя и его default-профиля.
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            user = await repo.first_user()
            if user is None:
                user = await repo.create_user("admin", "test-hash", [ROLE_ADMIN, ROLE_USER])
            await repo.upsert_profile(
                {
                    "name": "default",
                    "enabled": True,
                    "is_active": True,
                    "competencies": "Тестовые компетенции",
                    "keywords": [],
                    "exclusion_words": [],
                    "questions": [],
                },
                user.id,
            )
            return user.id
        finally:
            await db.dispose()

    user_id = asyncio.run(_setup())
    docs = tmp_path_factory.mktemp("docs")

    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    # Авторизация всегда включена: задаём секрет и внутренний токен (обязательны).
    os.environ["ZAKUPKI_AUTH_SECRET"] = AUTH_SECRET
    os.environ["ZAKUPKI_INTERNAL_TOKEN"] = "internal-123"
    app = create_app()
    with TestClient(app) as client:
        token = create_token(user_id, [ROLE_ADMIN, ROLE_USER], AUTH_SECRET, 3600)
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, docs
    os.environ.pop("ZAKUPKI_DB_DSN", None)
    os.environ.pop("ZAKUPKI_AUTH_SECRET", None)
    os.environ.pop("ZAKUPKI_INTERNAL_TOKEN", None)


@pytest.fixture(scope="module")
async def inserted_id(api_client: tuple[TestClient, Path]) -> AsyncIterator[int]:
    client, _ = api_client
    db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
    await db.connect()
    repo = ProcurementRepository(db)
    await repo.upsert(
        {
            "number": "API-1",
            "platform_id": "zakupki_mos",
            "subject": "Тест API",
            "customer": "Заказчик ООО",
            "okpd2_codes": "62.01",
        }
    )
    rows, _ = await repo.list_procurements(number="API-1")
    await db.dispose()
    yield rows[0].id


def test_health(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] is True


def test_parser_status_initial(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    body = client.get("/api/parser/status").json()
    assert body["running"] is False
    assert body["error"] is None


def test_parser_stop_when_idle(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    resp = client.post("/api/parser/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"


def test_db_clear_when_idle(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    resp = client.post("/api/db/clear")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cleared"
    assert client.get("/api/procurements").json()["total"] == 0


def test_db_clear_inactive(api_client: tuple[TestClient, Path]) -> None:
    """POST /api/db/clear-inactive удаляет только неактивные закупки."""
    client, _ = api_client

    async def _seed() -> tuple[int, int]:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            await repo.upsert(
                {
                    "number": "CIN-1",
                    "platform_id": "zakupki_mos",
                    "subject": "Неактивна",
                    "is_active": False,
                }
            )
            await repo.upsert(
                {
                    "number": "CIN-2",
                    "platform_id": "zakupki_mos",
                    "subject": "Активна",
                }
            )
            rows, _ = await repo.list_procurements(number="CIN-")
            ids = {p.number: p.id for p in rows}
            return ids["CIN-1"], ids["CIN-2"]
        finally:
            await db.dispose()

    inactive_id, active_id = asyncio.run(_seed())

    resp = client.post("/api/db/clear-inactive")
    assert resp.status_code == 200
    assert resp.json()["deleted"] >= 1

    assert client.get(f"/api/procurements/{inactive_id}").status_code == 404
    assert client.get(f"/api/procurements/{active_id}").status_code == 200


def test_db_clear_irrelevant(api_client: tuple[TestClient, Path]) -> None:
    """POST /api/db/clear-irrelevant удаляет закупки с fit_score < порога (по умолчанию 0.4)."""
    client, _ = api_client

    async def _seed() -> tuple[int, int, int]:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            await repo.upsert(
                {
                    "number": "CIR-1",
                    "platform_id": "zakupki_mos",
                    "subject": "Релевантная",
                }
            )
            await repo.upsert(
                {
                    "number": "CIR-2",
                    "platform_id": "zakupki_mos",
                    "subject": "Нерелевантная",
                }
            )
            # Отсечка по векторной близости (ADR-8): fit_score=0 — нерелевантна.
            await repo.upsert(
                {
                    "number": "CIR-3",
                    "platform_id": "zakupki_mos",
                    "subject": "Векторная отсечка",
                }
            )
            rows, _ = await repo.list_procurements(number="CIR-")
            ids = {p.number: p.id for p in rows}
            # Оценки per-profile (BR-07): результат внешнего скоринга приходит
            # через POST /score и пишется в procurement_evaluations активного профиля.
            user = await repo.first_user()
            assert user is not None
            profile = await repo.get_active_profile(user.id)
            assert profile is not None
            await repo.upsert_score(ids["CIR-1"], profile.id, fit_score=0.8, score_method="fit")
            await repo.upsert_score(ids["CIR-2"], profile.id, fit_score=0.2, score_method="fit")
            await repo.upsert_score(ids["CIR-3"], profile.id, fit_score=0.0, score_method="sim")
            return ids["CIR-1"], ids["CIR-2"], ids["CIR-3"]
        finally:
            await db.dispose()

    relevant_id, irrelevant_id, sim_id = asyncio.run(_seed())

    resp = client.post("/api/db/clear-irrelevant", json={"min_fit_score": 0.4})
    assert resp.status_code == 200
    assert resp.json()["deleted"] >= 1

    assert client.get(f"/api/procurements/{relevant_id}").status_code == 200
    assert client.get(f"/api/procurements/{irrelevant_id}").status_code == 404
    assert client.get(f"/api/procurements/{sim_id}").status_code == 404


def test_websocket_receives_broadcast(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    with client.websocket_connect("/ws") as ws:
        # Запрос, меняющий БД, шлёт клиенту "data-changed".
        r = client.post("/api/db/clear")
        assert r.status_code == 200
        assert ws.receive_text() == "data-changed"


def test_index_page_served(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Парсер закупок" in resp.text
    assert 'src="/static/js/main.js"' in resp.text


def test_list_and_get(api_client: tuple[TestClient, Path], inserted_id: int) -> None:
    client, _ = api_client
    resp = client.get("/api/procurements", params={"number": "API-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(item["id"] == inserted_id for item in body["items"])

    detail = client.get(f"/api/procurements/{inserted_id}")
    assert detail.status_code == 200
    assert detail.json()["number"] == "API-1"
    assert detail.json()["is_active"] is True


def test_list_filter_active(api_client: tuple[TestClient, Path], inserted_id: int) -> None:
    client, _ = api_client
    active = client.get("/api/procurements", params={"active": True}).json()
    inactive = client.get("/api/procurements", params={"active": False}).json()
    assert any(item["id"] == inserted_id for item in active["items"])
    assert all(item["is_active"] is True for item in active["items"])
    assert all(item["is_active"] is False for item in inactive["items"])


def test_missing_procurement_404(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    assert client.get("/api/procurements/999999").status_code == 404


def test_procurement_tz_text(
    api_client: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/procurements/{id}/tz возвращает текст ТЗ (в т.ч. из архива).

    Кэш задействован через настоящий extract_text_cached: подменяется только
    извлечение (extract_text) и поиск файла (find_tz_reference), чтобы не ходить
    в сеть. Повторный запрос не переизвлекает текст (счётчик вызовов не растёт).
    """
    import zakupki_parser.api.app.routes.procurements as proc_route
    from scoring_common.tz import clear_tz_text_cache
    from scoring_common.tz.files import FileRef

    client, _ = api_client
    clear_tz_text_cache()
    try:

        async def _seed() -> int:
            db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
            await db.connect()
            try:
                repo = ProcurementRepository(db)
                assert await repo.upsert(
                    {
                        "number": "TZ-1",
                        "platform_id": "zakupki_mos",
                        "subject": "Закупка с ТЗ в архиве",
                        "files_json": [
                            {"name": "приложение.zip", "url": "http://x/a.zip"},
                            {"name": "смета.xlsx", "url": "http://x/smeta.xlsx"},
                        ],
                    }
                )
                rows, _ = await repo.list_procurements(number="TZ-1")
                return rows[0].id
            finally:
                await db.dispose()

        tz_id = asyncio.run(_seed())

        extract_calls: list[tuple[str, str]] = []

        def fake_find(record: dict[str, Any], timeout: float = 30.0) -> FileRef | None:
            files = record.get("files_json") or []
            assert any(f.get("name") == "приложение.zip" for f in files)
            return FileRef("ТЗ.docx", "http://x/a.zip#doc/ТЗ.docx")

        def fake_extract(ref: FileRef, timeout: float = 30.0) -> str | None:
            extract_calls.append((ref.url, ref.name))
            return "# Раздел 1\nТребования к товару."

        monkeypatch.setattr(proc_route, "find_tz_reference_cached", fake_find)
        monkeypatch.setattr("scoring_common.tz.extract_text", fake_extract)

        body = client.get(f"/api/procurements/{tz_id}/tz").json()
        assert body["found"] is True
        assert body["file_name"] == "ТЗ.docx"
        assert body["from_archive"] is True
        assert "Раздел 1" in body["text"]
        assert extract_calls == [("http://x/a.zip#doc/ТЗ.docx", "ТЗ.docx")]

        # Повторный запрос отдаёт тот же результат без повторного извлечения (кэш).
        again = client.get(f"/api/procurements/{tz_id}/tz").json()
        assert again["text"] == body["text"]
        assert extract_calls == [("http://x/a.zip#doc/ТЗ.docx", "ТЗ.docx")]
    finally:
        clear_tz_text_cache()


def test_procurement_tz_not_found(api_client: tuple[TestClient, Path]) -> None:
    """Без файлов ТЗ эндпоинт отдаёт found=False (без обращения к сети)."""
    client, _ = api_client

    async def _seed() -> int:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            assert await repo.upsert(
                {
                    "number": "TZ-NONE",
                    "platform_id": "zakupki_mos",
                    "subject": "Без ТЗ",
                    "files_json": [{"name": "смета.xlsx", "url": "http://x/smeta.xlsx"}],
                }
            )
            rows, _ = await repo.list_procurements(number="TZ-NONE")
            return rows[0].id
        finally:
            await db.dispose()

    tz_id = asyncio.run(_seed())
    body = client.get(f"/api/procurements/{tz_id}/tz").json()
    assert body["found"] is False
    assert body["text"] is None


def test_relevance_threshold_endpoint(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    body = client.get("/api/config/threshold").json()
    assert "notify_min_fit_score" in body
    assert isinstance(body["notify_min_fit_score"], (int, float))


def test_list_filter_min_fit_score(api_client: tuple[TestClient, Path], inserted_id: int) -> None:
    client, _ = api_client
    # Задаём закупке фит-скор (выше порога по умолчанию 0.4).
    resp = client.post(
        f"/api/procurements/{inserted_id}/score",
        json={"score": 123.5, "fit_score": 0.85, "score_method": "fit"},
    )
    assert resp.status_code == 200

    # Порог ниже/равен 0.85 — закупка попадает в выборку.
    below = client.get("/api/procurements", params={"min_fit_score": 0.5}).json()
    assert any(item["id"] == inserted_id for item in below["items"])
    assert all(
        item["fit_score"] is not None and item["fit_score"] >= 0.5 for item in below["items"]
    )

    # Порог выше 0.85 — закупка исключается.
    above = client.get("/api/procurements", params={"min_fit_score": 0.99}).json()
    assert all(item["id"] != inserted_id for item in above["items"])


def test_list_filter_min_fit_score_ignores_default_scored(
    api_client: tuple[TestClient, Path],
) -> None:
    """Дефолтный фит-скор (до обработки скорингом) не считается релевантным."""
    client, _ = api_client

    async def _insert_default() -> int:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            assert await repo.upsert(
                {
                    "number": "API-DEFAULT",
                    "platform_id": "zakupki_mos",
                    "subject": "Дефолтный скор",
                    "customer": "Заказчик ООО",
                    "fit_score": 0.9,
                    "score_method": "default",
                }
            )
            rows, _ = await repo.list_procurements(number="API-DEFAULT")
            return rows[0].id
        finally:
            await db.dispose()

    default_id = asyncio.run(_insert_default())

    # Несмотря на высокий fit_score, дефолтный не попадает в «релевантные».
    relevant = client.get("/api/procurements", params={"min_fit_score": 0.5}).json()
    assert all(item["id"] != default_id for item in relevant["items"])
    # Но присутствует в обычном списке (без фильтра).
    all_procs = client.get("/api/procurements", params={"number": "API-DEFAULT"}).json()
    assert any(item["id"] == default_id for item in all_procs["items"])


def test_sim_filtered_record_visible_with_fit_score(
    api_client: tuple[TestClient, Path],
) -> None:
    """Отсечка по векторной близости (score_method=sim) видна в API: fit_score=0.

    В «Только релевантные» (порог > 0) такая закупка не попадает, но в обычном
    списке возвращается с fit_score=0 и score_method=sim (ADR-8) — то есть
    отсечённая закупка отличима от ещё не обработанной.
    """
    client, _ = api_client

    async def _seed() -> int:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            assert await repo.upsert(
                {
                    "number": "API-VECTOR",
                    "platform_id": "zakupki_mos",
                    "subject": "Векторная отсечка",
                    "customer": "Заказчик ООО",
                }
            )
            rows, _ = await repo.list_procurements(number="API-VECTOR")
            return rows[0].id
        finally:
            await db.dispose()

    sim_id = asyncio.run(_seed())

    # Результат сервиса скоринга приходит через POST /score (ADR-7): sim —
    # предварительная фильтрация по векторной близости, LLM не выполнялся.
    resp = client.post(
        f"/api/procurements/{sim_id}/score",
        json={
            "score": 0.0,
            "fit_score": 0.0,
            "score_method": "sim",
            "embedding_similarity": 0.62,
        },
    )
    assert resp.status_code == 200

    # В обычном списке — с fit_score=0 и score_method=sim.
    all_procs = client.get("/api/procurements", params={"number": "API-VECTOR"}).json()
    item = next(item for item in all_procs["items"] if item["id"] == sim_id)
    assert item["fit_score"] == 0.0
    assert item["score"] == 0.0
    assert item["score_method"] == "sim"
    assert item["embedding_similarity"] == 0.62

    # В «Только релевантные» (порог 0.4) не попадает: fit_score=0 < порога.
    relevant = client.get("/api/procurements", params={"min_fit_score": 0.4}).json()
    assert all(item["id"] != sim_id for item in relevant["items"])


def test_list_sort_fit_score(api_client: tuple[TestClient, Path]) -> None:
    """GET /api/procurements?sort=fit_score сортирует по релевантности (NULL в конце)."""
    client, _ = api_client

    async def _seed() -> None:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            await repo.upsert(
                {
                    "number": "SORT-MID",
                    "platform_id": "zakupki_mos",
                    "subject": "Средний",
                    "fit_score": 0.5,
                    "score_method": "fit",
                }
            )
            await repo.upsert(
                {
                    "number": "SORT-NONE",
                    "platform_id": "zakupki_mos",
                    "subject": "Без скоринга",
                }
            )
            await repo.upsert(
                {
                    "number": "SORT-HIGH",
                    "platform_id": "zakupki_mos",
                    "subject": "Высокий",
                    "fit_score": 0.9,
                    "score_method": "fit",
                }
            )
        finally:
            await db.dispose()

    asyncio.run(_seed())

    body = client.get(
        "/api/procurements",
        params={"number": "SORT", "sort": "fit_score", "limit": 100},
    ).json()
    fits = [item["fit_score"] for item in body["items"]]
    assert fits == sorted(fits, key=lambda v: v if v is not None else -1, reverse=True)
    assert body["items"][-1]["fit_score"] is None


def test_list_sort_publication_date(api_client: tuple[TestClient, Path]) -> None:
    """GET /api/procurements?sort=publication_date сортирует по убыванию даты (NULL в конце)."""
    client, _ = api_client

    async def _seed() -> None:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            await repo.upsert(
                {
                    "number": "SORTDATE-OLD",
                    "platform_id": "zakupki_mos",
                    "subject": "Старая",
                    "publication_date": datetime(2026, 1, 1, tzinfo=UTC),
                }
            )
            await repo.upsert(
                {
                    "number": "SORTDATE-NEW",
                    "platform_id": "zakupki_mos",
                    "subject": "Новая",
                    "publication_date": datetime(2026, 6, 1, tzinfo=UTC),
                }
            )
            await repo.upsert(
                {
                    "number": "SORTDATE-NONE",
                    "platform_id": "zakupki_mos",
                    "subject": "Без даты",
                }
            )
        finally:
            await db.dispose()

    asyncio.run(_seed())

    body = client.get(
        "/api/procurements",
        params={"number": "SORTDATE", "sort": "publication_date", "limit": 100},
    ).json()
    dates = [item["publication_date"] for item in body["items"]]
    assert dates[0] == "2026-06-01T00:00:00Z"
    assert dates[1] == "2026-01-01T00:00:00Z"
    assert dates[-1] is None


def test_set_score_by_external_service(
    api_client: tuple[TestClient, Path], inserted_id: int
) -> None:
    client, _ = api_client
    resp = client.post(
        f"/api/procurements/{inserted_id}/score",
        json={"score": 123.5, "fit_score": 0.85, "score_method": "fit"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 123.5
    assert body["fit_score"] == 0.85
    assert body["score_method"] == "fit"

    detail = client.get(f"/api/procurements/{inserted_id}").json()
    assert detail["score"] == 123.5
    assert detail["fit_score"] == 0.85


def test_set_score_notifies_above_threshold(
    api_client: tuple[TestClient, Path], inserted_id: int
) -> None:
    client, _ = api_client
    calls: list[dict[str, object]] = []

    class _FakeNotifier:
        async def notify(self, record: dict[str, object]) -> None:
            calls.append(record)

    state = cast(Any, client.app).state.parser
    state.notifier = _FakeNotifier()
    state.notify_min_fit_score = 0.5

    # Ниже порога — score обновляется, уведомления нет (sim — терминальная
    # отсечка по векторной близости, ADR-8).
    resp = client.post(
        f"/api/procurements/{inserted_id}/score",
        json={"score": 50.0, "fit_score": 0.3, "score_method": "sim"},
    )
    assert resp.status_code == 200
    assert calls == []

    # Выше порога (по fit_score) — уведомление с обновлённой карточкой.
    resp = client.post(
        f"/api/procurements/{inserted_id}/score",
        json={"score": 150.0, "fit_score": 0.9, "score_method": "fit"},
    )
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["score"] == 150.0
    assert calls[0]["fit_score"] == 0.9


def test_set_score_404(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    assert client.post("/api/procurements/999999/score", json={"score": 1.0}).status_code == 404


def test_set_score_rejects_unknown_method(
    api_client: tuple[TestClient, Path], inserted_id: int
) -> None:
    """POST /score с неизвестным score_method отклоняется (422), а не пишется в БД.

    Приёмный эндпоинт принимает только известные результаты внешнего скоринга
    (fit/pwin/margin/sim, ADR-7/ADR-8): неизвестный метод — признак рассинхрона
    конвейера, его не нужно молча сохранять.
    """
    client, _ = api_client
    resp = client.post(
        f"/api/procurements/{inserted_id}/score",
        json={"score": 50.0, "fit_score": 0.3, "score_method": "unknown-stage"},
    )
    assert resp.status_code == 422

    detail = client.get(f"/api/procurements/{inserted_id}").json()
    assert detail["score_method"] != "unknown-stage"


def test_procurement_has_customer_id_and_name(
    api_client: tuple[TestClient, Path], inserted_id: int
) -> None:
    client, _ = api_client
    body = client.get(f"/api/procurements/{inserted_id}").json()
    assert body["customer_id"] is not None
    assert body["customer"] == "Заказчик ООО"


def test_procurements_filter_by_customer(
    api_client: tuple[TestClient, Path], inserted_id: int
) -> None:
    client, _ = api_client
    resp = client.get("/api/procurements", params={"customer": "заказчик"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(item["id"] == inserted_id for item in body["items"])


def test_customers_list_and_rating(api_client: tuple[TestClient, Path], inserted_id: int) -> None:
    client, _ = api_client
    customer_id = client.get(f"/api/procurements/{inserted_id}").json()["customer_id"]

    listed = client.get("/api/customers").json()
    assert listed["total"] >= 1
    assert any(item["id"] == customer_id for item in listed["items"])

    got = client.get(f"/api/customers/{customer_id}")
    assert got.status_code == 200
    assert got.json()["name"] == "Заказчик ООО"

    rated = client.post(f"/api/customers/{customer_id}/rating", json={"rating": 0.9})
    assert rated.status_code == 200
    assert rated.json()["rating"] == 0.9


def test_customer_rating_404(api_client: tuple[TestClient, Path]) -> None:
    client, _ = api_client
    assert client.post("/api/customers/999999/rating", json={"rating": 1.0}).status_code == 404
    assert client.get("/api/customers/999999").status_code == 404


def test_config_get_redacts_and_put_saves(tmp_path: Path) -> None:
    """Конфиг-сервис: GET отдаёт без секретов, PUT валидирует и пишет в YAML.

    Используем копию configs в tmp_path, чтобы не трогать реальный конфиг.
    """
    from zakupki_parser.api.app import create_app

    cfgdir = tmp_path / "configs"
    # Копируем ТЕСТОВЫЙ набор конфигов (tests/configs), а не рабочие configs/*.
    shutil.copytree(Path(__file__).resolve().parents[2] / "tests" / "configs", cfgdir)
    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    app = create_app(str(cfgdir))
    with TestClient(app) as client:
        cfg = client.get("/api/config").json()
        assert "sites" in cfg
        # Эксплуатационные параметры (таймер, БД, уведомления) не отдаются через API —
        # они живут в config_ops.yaml.
        assert "timeout_seconds" not in cfg
        assert "notifications" not in cfg

        old = cfg["default_cutoff_days"]
        cfg["default_cutoff_days"] = old + 1
        r = client.put("/api/config", json=cfg)
        assert r.status_code == 200
        assert r.json()["default_cutoff_days"] == old + 1

        saved = (cfgdir / "config_service.yaml").read_text(encoding="utf-8")
        assert f"default_cutoff_days: {old + 1}" in saved

        # Некорректные данные — 422, файл не меняется.
        bad = client.put("/api/config", json={"default_cutoff_days": "not-a-number"})
        assert bad.status_code == 422
    os.environ.pop("ZAKUPKI_DB_DSN", None)


def test_export_csv_download(api_client: tuple[TestClient, Path], inserted_id: int) -> None:
    """CSV отдаётся файлом: только активные релевантные закупки (fit_score >= порога)."""
    client, _ = api_client

    async def _seed_relevant() -> None:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            await repo.upsert(
                {
                    "number": "EXPORT-REL",
                    "platform_id": "zakupki_mos",
                    "subject": "Релевантная активная",
                    "customer": "Заказчик ООО",
                }
            )
            await repo.upsert(
                {
                    "number": "EXPORT-IRR",
                    "platform_id": "zakupki_mos",
                    "subject": "Нерелевантная",
                }
            )
            await repo.upsert(
                {
                    "number": "EXPORT-INACTIVE",
                    "platform_id": "zakupki_mos",
                    "subject": "Неактивная",
                    "is_active": False,
                }
            )
            rows, _ = await repo.list_procurements(number="EXPORT-")
            ids = {p.number: p.id for p in rows}
            # Оценки per-profile (BR-07): релевантность фильтра/выгрузки считается
            # по procurement_evaluations активного профиля.
            user = await repo.first_user()
            assert user is not None
            profile = await repo.get_active_profile(user.id)
            assert profile is not None
            await repo.upsert_score(
                ids["EXPORT-REL"], profile.id, fit_score=0.8, score_method="fit"
            )
            await repo.upsert_score(
                ids["EXPORT-IRR"], profile.id, fit_score=0.2, score_method="fit"
            )
        finally:
            await db.dispose()

    asyncio.run(_seed_relevant())

    resp = client.post("/api/procurements/export", json={"min_fit_score": 0.4})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers.get("content-disposition", "")
    content = resp.content.decode("utf-8-sig")
    # Заголовок + активная релевантная запись.
    assert "number,platform_id" in content
    assert "EXPORT-REL" in content
    assert "Заказчик ООО" in content
    # Нерелевантная (fit_score < 0.4) и неактивная закупки в выгрузку не попадают.
    assert "EXPORT-IRR" not in content
    assert "EXPORT-INACTIVE" not in content


def test_prompts_list_get_put_validate(tmp_path: Path) -> None:
    """Промпты: список, чтение, сохранение; JSON валидируется, traversal запрещён.

    Используем копию tests/configs и отдельный каталог промптов в tmp_path,
    чтобы не трогать реальные конфиги и файлы промптов.
    """
    from zakupki_parser.api.app import create_app

    cfgdir = tmp_path / "configs"
    shutil.copytree(Path(__file__).resolve().parents[2] / "tests" / "configs", cfgdir)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "fit_system.md").write_text("СТАРЫЙ ПРОМПТ", encoding="utf-8")
    (prompts_dir / "few_shot.json").write_text('[{"a": 1}]', encoding="utf-8")

    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    os.environ["ZAKUPKI_PROMPTS_DIR"] = str(prompts_dir)
    app = create_app(str(cfgdir))
    with TestClient(app) as client:
        # Список: только md/json внутри prompts_dir.
        files = client.get("/api/prompts").json()["files"]
        names = [f["name"] for f in files]
        assert "fit_system.md" in names
        assert "few_shot.json" in names

        # Чтение содержимого.
        got = client.get("/api/prompts/fit_system.md")
        assert got.status_code == 200
        assert got.json()["content"] == "СТАРЫЙ ПРОМПТ"
        assert got.json()["kind"] == "markdown"

        # Сохранение markdown.
        r = client.put("/api/prompts/fit_system.md", json={"content": "НОВЫЙ ПРОМПТ"})
        assert r.status_code == 200
        assert r.json()["content"] == "НОВЫЙ ПРОМПТ"
        assert (prompts_dir / "fit_system.md").read_text(encoding="utf-8") == "НОВЫЙ ПРОМПТ"

        # Некорректный JSON — 422, файл не меняется.
        bad = client.put("/api/prompts/few_shot.json", json={"content": "{broken"})
        assert bad.status_code == 422
        assert (prompts_dir / "few_shot.json").read_text(encoding="utf-8") == '[{"a": 1}]'

        # Корректный JSON сохраняется.
        ok = client.put("/api/prompts/few_shot.json", json={"content": '[{"b": 2}]'})
        assert ok.status_code == 200
        assert ok.json()["kind"] == "json"

        # Path traversal и несуществующие файлы отклоняются.
        assert client.get("/api/prompts/..%2Fconfig_service.yaml").status_code in (400, 404)
        assert client.put("/api/prompts/nope.md", json={"content": "x"}).status_code == 404
        assert client.get("/api/prompts/secret.txt").status_code in (400, 404)
    os.environ.pop("ZAKUPKI_DB_DSN", None)
    os.environ.pop("ZAKUPKI_PROMPTS_DIR", None)


def test_analysis_prompts_list_get_put(tmp_path: Path) -> None:
    """Промпты analysis_service: список, чтение, сохранение.

    Используем отдельный каталог промптов анализатора в tmp_path (env
    ZAKUPKI_ANALYSIS_PROMPTS_DIR), чтобы не трогать реальные файлы.
    """
    from zakupki_parser.api.app import create_app

    cfgdir = tmp_path / "configs"
    shutil.copytree(Path(__file__).resolve().parents[2] / "tests" / "configs", cfgdir)
    analysis_prompts_dir = tmp_path / "analysis_prompts"
    analysis_prompts_dir.mkdir()
    (analysis_prompts_dir / "verdict_system.md").write_text("СТАРЫЙ ПРОМПТ", encoding="utf-8")

    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    os.environ["ZAKUPKI_ANALYSIS_PROMPTS_DIR"] = str(analysis_prompts_dir)
    app = create_app(str(cfgdir))
    with TestClient(app) as client:
        # Список: только md/json внутри каталога промптов анализатора.
        files = client.get("/api/analysis-prompts").json()["files"]
        names = [f["name"] for f in files]
        assert "verdict_system.md" in names

        # Чтение содержимого.
        got = client.get("/api/analysis-prompts/verdict_system.md")
        assert got.status_code == 200
        assert got.json()["content"] == "СТАРЫЙ ПРОМПТ"
        assert got.json()["kind"] == "markdown"

        # Сохранение.
        r = client.put("/api/analysis-prompts/verdict_system.md", json={"content": "НОВЫЙ ПРОМПТ"})
        assert r.status_code == 200
        assert r.json()["content"] == "НОВЫЙ ПРОМПТ"
        assert (analysis_prompts_dir / "verdict_system.md").read_text(
            encoding="utf-8"
        ) == "НОВЫЙ ПРОМПТ"

        # Каталоги скоринга и анализатора независимы: анализ-промпт не виден
        # в /api/prompts и наоборот.
        assert client.get("/api/analysis-prompts/fit_system.md").status_code == 404

        # Path traversal отклоняется.
        assert client.get("/api/analysis-prompts/..%2Fconfig_service.yaml").status_code in (
            400,
            404,
        )
    os.environ.pop("ZAKUPKI_DB_DSN", None)
    os.environ.pop("ZAKUPKI_ANALYSIS_PROMPTS_DIR", None)
