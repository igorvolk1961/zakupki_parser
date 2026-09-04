"""Интеграционные тесты мультитенантного скоринга (требуют PostgreSQL).

Проверяют: CRUD профилей в tenant-скоупе, per-user скоринг через POST /score,
rag_report, изоляцию данных между пользователями (BR-07), on-demand analyze/pwin-margin.
Ручные оценки manual/reject — вне MVP (этап 6, пост-MVP).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from zakupki_parser.api.app import create_app
from zakupki_parser.auth import ROLE_ADMIN, ROLE_USER, create_token
from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database
from zakupki_parser.storage.repository import ProcurementRepository

COMP_JSON = json.dumps(
    {
        "positioning": "Тестовые компетенции",
        "breadth": "broad",
        "competencies": [{"area": "Аудит", "description": "обследование"}],
        "exclusions": [],
    },
    ensure_ascii=False,
    separators=(",", ":"),
)

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")
AUTH_SECRET = "test-secret"
# Служебные эндпоинты конвейера (POST /score, /customers/{id}/rating) закрыты
# внутренним токеном (X-Internal-Token) и не принимают пользовательский bearer.
INTERNAL_HEADERS = {"X-Internal-Token": "internal-123"}

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")


async def _seed_default_profile(repo: ProcurementRepository) -> int:
    """Создаёт пользователя (админ + user) и его активный профиль default; возвращает user_id."""
    user = await repo.first_user()
    if user is None:
        user = await repo.create_user("admin", "test-hash", [ROLE_ADMIN, ROLE_USER])
    profile = await repo.upsert_profile(
        {
            "name": "default",
            "enabled": True,
            "is_active": True,
            "competencies": COMP_JSON,
            "keywords": [],
            "exclusion_words": ["медицинский"],
            "questions": [{"id": "q1", "text": "Требуется ли лицензия?"}],
        },
        user.id,
    )
    assert profile.id is not None
    return user.id


@pytest.fixture(scope="module")
def mc_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    async def _setup() -> int:
        engine = create_async_engine(TEST_DSN)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            return await _seed_default_profile(repo)
        finally:
            await db.dispose()

    user_id = asyncio.run(_setup())
    os.environ["ZAKUPKI_DB_DSN"] = TEST_DSN
    # Авторизация всегда включена: задаём секрет и внутренний токен (обязательны).
    os.environ["ZAKUPKI_AUTH_SECRET"] = AUTH_SECRET
    os.environ["ZAKUPKI_INTERNAL_TOKEN"] = "internal-123"
    app = create_app()
    with TestClient(app) as client:
        token = create_token(user_id, [ROLE_ADMIN, ROLE_USER], AUTH_SECRET, 3600)
        client.headers["Authorization"] = f"Bearer {token}"
        yield client
    os.environ.pop("ZAKUPKI_DB_DSN", None)
    os.environ.pop("ZAKUPKI_AUTH_SECRET", None)
    os.environ.pop("ZAKUPKI_INTERNAL_TOKEN", None)


def _seed_procurement() -> int:
    async def _seed() -> int:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            await repo.upsert(
                {"number": "MC-1", "platform_id": "zakupki_mos", "subject": "Разработка ИИ"}
            )
            rows, _ = await repo.list_procurements(number="MC-1")
            return rows[0].id
        finally:
            await db.dispose()

    return asyncio.run(_seed())


def test_clients_crud(mc_client: TestClient) -> None:
    client = mc_client
    active = client.get("/api/clients/active")
    assert active.status_code == 200
    assert active.json()["name"] == "default"
    assert active.json()["exclusion_words"] == ["медицинский"]
    assert active.json()["questions"] == [{"id": "q1", "text": "Требуется ли лицензия?"}]

    # Создание профиля (POST /api/clients — upsert по user_id + name).
    created = client.post(
        "/api/clients",
        json={"name": "client-b", "competencies": COMP_JSON, "keywords": ["ИИ"]},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["name"] == "client-b"
    assert body["keywords"] == ["ИИ"]

    listed = client.get("/api/clients")
    assert listed.status_code == 200
    assert listed.json()["total"] >= 2


def test_profile_target_regions_roundtrip(mc_client: TestClient) -> None:
    """Целевые регионы профиля + макс. расстояние: CRUD + JSON-экспорт/импорт без потерь."""
    client = mc_client
    created = client.post(
        "/api/clients",
        json={
            "name": "region-client",
            "competencies": COMP_JSON,
            "target_regions": ["Московск* обл*", "Санкт-Петербург"],
            "max_region_distance_km": 100.0,
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["target_regions"] == ["Московск* обл*", "Санкт-Петербург"]
    assert body["max_region_distance_km"] == 100.0
    profile_id = body["id"]

    got = client.get(f"/api/clients/{profile_id}")
    assert got.status_code == 200
    assert got.json()["target_regions"] == ["Московск* обл*", "Санкт-Петербург"]
    assert got.json()["max_region_distance_km"] == 100.0

    exported = client.get(f"/api/clients/{profile_id}/export")
    assert exported.status_code == 200
    content = json.loads(exported.json()["profile_content"])
    assert content["profile"]["target_regions"] == ["Московск* обл*", "Санкт-Петербург"]
    assert content["profile"]["max_region_distance_km"] == 100.0

    imported = client.post(
        "/api/clients/import", json={"content": exported.json()["profile_content"]}
    )
    assert imported.status_code == 200
    assert imported.json()["target_regions"] == ["Московск* обл*", "Санкт-Петербург"]
    assert imported.json()["max_region_distance_km"] == 100.0

    # Профиль без target_regions (PUT без поля) сохраняет регионы непустыми.
    updated = client.put(
        f"/api/clients/{profile_id}",
        json={
            "name": "region-client",
            "competencies": COMP_JSON,
            "target_regions": [],
            "max_region_distance_km": None,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["target_regions"] == []
    assert updated.json()["max_region_distance_km"] is None


def test_profile_export_import_roundtrip(mc_client: TestClient) -> None:
    """Экспорт профиля единым JSON-файлом и повторная загрузка (round-trip).

    ``profile_content`` — полный JSON с обёрткой ``profile`` и подобъектом
    ``competencies``; файл самодостаточен и импортируется обратно без потерь.
    """
    client = mc_client
    created = client.post(
        "/api/clients",
        json={
            "name": "export-me",
            "competencies": COMP_JSON,
            "keywords": ["ИИ", "автоматизация"],
            "exclusion_words": ["ремонт"],
            "okpd_codes": ["62.02"],
            "nmck_min": 100000,
            "nmck_max": 5000000,
        },
    )
    assert created.status_code == 200
    profile_id = created.json()["id"]
    name = created.json()["name"]

    exported = client.get(f"/api/clients/{profile_id}/export")
    assert exported.status_code == 200
    body = exported.json()
    assert body["profile_filename"].endswith(".json")
    assert name in body["profile_filename"]

    content = json.loads(body["profile_content"])
    assert content["schema"] == "zakupki-profile"
    assert content["version"] == 1
    assert content["profile"]["name"] == name
    assert content["profile"]["okpd_codes"] == ["62.02"]
    assert content["profile"]["keywords"] == ["ИИ", "автоматизация"]
    assert content["profile"]["exclusion_words"] == ["ремонт"]
    # Компетенции — канонический JSON схемы Profile (BR-07), без legacy-режимов.
    assert content["competencies"]["positioning"] == "Тестовые компетенции"
    assert content["competencies"]["competencies"][0]["area"] == "Аудит"

    # Повторная загрузка того же файла не теряет компетенции.
    imported = client.post("/api/clients/import", json={"content": body["profile_content"]})
    assert imported.status_code == 200
    imported_body = imported.json()
    assert imported_body["name"] == name
    assert "Тестовые компетенции" in imported_body["competencies"]
    assert imported_body["keywords"] == ["ИИ", "автоматизация"]


def test_profile_export_structured_competencies(mc_client: TestClient) -> None:
    """JSON-экспорт структурированных компетенций — подобъект как модель scoring Profile."""
    client = mc_client
    structured = {
        "positioning": "Внедряем ИИ и автоматизируем процессы",
        "breadth": "broad",
        "competencies": [
            {"area": "Аудит", "description": "обследование процессов", "examples": ["кейс1"]}
        ],
        "exclusions": ["поставка оборудования"],
        "scoring_policy": {"uncovered_penalty": 3.0, "ambiguous_range": [5.0, 7.0]},
    }
    created = client.post(
        "/api/clients",
        json={"name": "struct-export", "competencies": json.dumps(structured)},
    )
    assert created.status_code == 200
    profile_id = created.json()["id"]

    exported = client.get(f"/api/clients/{profile_id}/export")
    assert exported.status_code == 200
    body = exported.json()
    content = json.loads(body["profile_content"])
    assert content["competencies"]["positioning"] == "Внедряем ИИ и автоматизируем процессы"
    assert content["competencies"]["competencies"][0]["area"] == "Аудит"

    imported = client.post("/api/clients/import", json={"content": body["profile_content"]})
    assert imported.status_code == 200
    imported_body = imported.json()
    from zakupki_parser.storage.competencies import normalize_competencies

    assert json.loads(imported_body["competencies"]) == json.loads(
        normalize_competencies(json.dumps(structured))
    )


def test_rag_report_via_score_endpoint(mc_client: TestClient) -> None:
    client = mc_client
    procurement_id = _seed_procurement()
    report = {
        "tz_found": True,
        "tz_file": "ТЗ.docx",
        "status": "ok",
        "questions": [
            {
                "question_id": "q1",
                "question_text": "Требуется ли лицензия?",
                "verdict": "absolute",
                "severity": 2,
                "excerpt": "Лицензия обязательна",
                "reasoning": "жёсткое требование",
            }
        ],
        "generated_at": "2026-08-19T00:00:00+00:00",
    }
    r = client.post(
        f"/api/procurements/{procurement_id}/score",
        json={
            "profile_id": 1,
            "score": 10.0,
            "fit_score": 0.7,
            "score_method": "fit",
            "rag_report": report,
        },
        headers=INTERNAL_HEADERS,
    )
    assert r.status_code == 200
    card = r.json()
    assert card["rag_report"]["tz_found"] is True
    assert card["rag_report"]["status"] == "ok"
    assert card["rag_report"]["questions"][0]["verdict"] == "absolute"
    # rag_report не меняет score_method.
    assert card["score_method"] == "fit"


def test_analyze_and_pwin_margin_queue(mc_client: TestClient) -> None:
    """On-demand эндпоинты: транспорт задан в конфиге — постановка best-effort (200 queued)."""
    client = mc_client
    procurement_id = _seed_procurement()
    r = client.post("/api/procurements/analyze", json={"procurement_ids": [procurement_id]})
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
    r = client.post("/api/procurements/pwin-margin", json={"procurement_ids": [procurement_id]})
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_list_uses_active_user_scores(mc_client: TestClient) -> None:
    client = mc_client
    procurement_id = _seed_procurement()
    client.post(
        f"/api/procurements/{procurement_id}/score",
        json={"profile_id": 1, "score": 10.0, "fit_score": 0.9, "score_method": "fit"},
        headers=INTERNAL_HEADERS,
    )
    data = client.get("/api/procurements").json()
    item = next((i for i in data["items"] if i["id"] == procurement_id), None)
    assert item is not None
    assert item["fit_score"] == 0.9
    assert item["score_method"] == "fit"


def test_repository_isolation_br07() -> None:
    """Изоляция BR-07: профили и оценки одного пользователя не видны другому."""

    async def _run() -> None:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            user_a = await repo.create_user("user-a", "hash-a", [ROLE_ADMIN])
            user_b = await repo.create_user("user-b", "hash-b", [ROLE_ADMIN])
            profile_a = await repo.upsert_profile(
                {"name": "A1", "competencies": COMP_JSON}, user_a.id
            )
            profile_b = await repo.upsert_profile(
                {"name": "B1", "competencies": COMP_JSON}, user_b.id
            )
            assert profile_a.id is not None and profile_b.id is not None

            # Профили изолированы.
            assert await repo.get_profile(user_a.id, profile_b.id) is None
            assert await repo.get_profile(user_b.id, profile_a.id) is None
            assert await repo.get_profile_by_name(user_b.id, "A1") is None
            _, total_a = await repo.list_profiles(user_a.id)
            _, total_b = await repo.list_profiles(user_b.id)
            assert total_a >= 1 and total_b >= 1

            # Оценки изолированы (уникальный ключ (procurement_id, profile_id),
            # профиль принадлежит пользователю).
            await repo.upsert(
                {"number": "ISO-1", "platform_id": "zakupki_mos", "subject": "Изоляция"}
            )
            rows, _ = await repo.list_procurements(number="ISO-1")
            procurement_id = rows[0].id
            await repo.upsert_score(procurement_id, profile_a.id, fit_score=0.7, score_method="fit")
            assert await repo.get_score(procurement_id, profile_b.id) is None
            score_a = await repo.get_score(procurement_id, profile_a.id)
            assert score_a is not None and score_a.fit_score == 0.7
        finally:
            await db.dispose()

    asyncio.run(_run())


def test_keywords_sync_and_single_active_profile() -> None:
    """Синхронизация таблицы keywords и единственный активный профиль."""

    async def _run() -> None:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            user = await repo.create_user("kw-user", "hash", [ROLE_ADMIN])
            p1 = await repo.seed_default_profile(
                user.id,
                {
                    "name": "default",
                    "competencies": COMP_JSON,
                    "keywords": ["ИИ", "автоматизация"],
                    "exclusion_words": ["ремонт"],
                },
            )
            assert p1.id is not None
            p2 = await repo.upsert_profile(
                {"name": "other", "competencies": COMP_JSON, "is_active": True}, user.id
            )
            assert p2.id is not None

            # Таблица keywords: keyword + exclusion, перезапись.
            async with db.session() as session:
                from sqlalchemy import select

                from zakupki_parser.storage.db import Keyword

                rows = (
                    (
                        await session.execute(
                            select(Keyword)
                            .where(Keyword.profile_id == p1.id)
                            .order_by(Keyword.type)
                        )
                    )
                    .scalars()
                    .all()
                )
                kinds = {(r.type, r.word) for r in rows}
                assert ("keyword", "ИИ") in kinds
                assert ("keyword", "автоматизация") in kinds
                assert ("exclusion", "ремонт") in kinds

            # Единственный активный профиль: default деактивирован.
            active = await repo.get_active_profile(user.id)
            assert active is not None and active.id == p2.id
            assert active.is_active is True
        finally:
            await db.dispose()

    asyncio.run(_run())
