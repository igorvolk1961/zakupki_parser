"""Интеграционные тесты лицензий и подтверждённого опыта профиля (BR-03).

Требуют PostgreSQL (ZAKUPKI_TEST_DSN). Проверяют: сид справочников (типы лицензий,
типы подтверждения BR-03), CRUD лицензий и опыта через вложенные эндпоинты профиля,
tenant-изоляцию (BR-07) и каскадное удаление при удалении профиля.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from zakupki_parser.api.app import create_app
from zakupki_parser.auth import ROLE_ADMIN, ROLE_USER, create_token
from zakupki_parser.config.models import DbConfig
from zakupki_parser.storage.db import Base, Database, ProfileExperience, ProfileLicense
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

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")


@pytest.fixture(scope="module")
def ple_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
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
            user = await repo.first_user()
            if user is None:
                user = await repo.create_user("admin", "test-hash", [ROLE_ADMIN, ROLE_USER])
            await repo.upsert_profile(
                {
                    "name": "default",
                    "enabled": True,
                    "is_active": True,
                    "competencies": COMP_JSON,
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


def _create_profile(client: TestClient, name: str) -> int:
    r = client.post("/api/clients", json={"name": name, "competencies": COMP_JSON, "keywords": []})
    assert r.status_code == 200
    return int(r.json()["id"])


def test_reference_data_seeded(ple_client: TestClient) -> None:
    client = ple_client
    types = client.get("/api/license-types")
    assert types.status_code == 200
    codes = {t["code"] for t in types.json()}
    assert {
        "fstek",
        "fsb",
        "mincifry",
        "roscomnadzor",
        "minpromtorg",
        "mchs",
        "rosgvardia",
        "education",
        "other",
    } <= codes

    conf = client.get("/api/confirmation-types")
    assert conf.status_code == 200
    assert {t["code"] for t in conf.json()} == {"platform", "documents", "registry"}


def test_licenses_crud(ple_client: TestClient) -> None:
    client = ple_client
    pid = _create_profile(client, "lic-profile")
    fstek = next(t for t in client.get("/api/license-types").json() if t["code"] == "fstek")

    created = client.post(
        f"/api/clients/{pid}/licenses",
        json={
            "license_type_id": fstek["id"],
            "number": "1234/Ф",
            "authority": "ФСТЭК России",
            "issue_date": "2023-01-15",
            "expiry_date": "2028-01-15",
            "notes": "рабочая",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["number"] == "1234/Ф"
    assert body["license_type"]["code"] == "fstek"
    lid = body["id"]

    lst = client.get(f"/api/clients/{pid}/licenses")
    assert lst.status_code == 200
    assert lst.json()["total"] == 1

    # PUT — полная замена: не переданные nullable-поля очищаются (не сохраняют
    # прежние значения), переданное значение обновляется.
    updated = client.put(
        f"/api/clients/{pid}/licenses/{lid}",
        json={"license_type_id": fstek["id"], "number": "5678/Ф"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["number"] == "5678/Ф"
    assert body["expiry_date"] is None
    assert body["authority"] is None
    assert body["notes"] is None

    assert client.delete(f"/api/clients/{pid}/licenses/{lid}").status_code == 204
    assert client.get(f"/api/clients/{pid}/licenses").json()["total"] == 0


def test_license_validation(ple_client: TestClient) -> None:
    client = ple_client
    pid = _create_profile(client, "lic-valid-profile")
    r = client.post(f"/api/clients/{pid}/licenses", json={"license_type_id": 999999})
    assert r.status_code == 422
    # Несуществующая запись — 404, даже если ссылка на справочник невалидна.
    r = client.put(
        f"/api/clients/{pid}/licenses/999999",
        json={"license_type_id": 999999},
    )
    assert r.status_code == 404
    assert client.delete(f"/api/clients/{pid}").status_code == 204


def test_experience_crud(ple_client: TestClient) -> None:
    client = ple_client
    pid = _create_profile(client, "exp-profile")
    platform = next(
        t for t in client.get("/api/confirmation-types").json() if t["code"] == "platform"
    )

    created = client.post(
        f"/api/clients/{pid}/experience",
        json={
            "title": "Разработка ИИ-сервиса",
            "customer_name": "ООО Заказчик",
            "contract_number": "К-1",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "amount": 1500000.0,
            "confirmation_type_id": platform["id"],
            "import_independent": True,
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["title"] == "Разработка ИИ-сервиса"
    assert body["confirmation_type"]["code"] == "platform"
    assert body["import_independent"] is True
    eid = body["id"]

    lst = client.get(f"/api/clients/{pid}/experience")
    assert lst.status_code == 200
    assert lst.json()["total"] == 1
    assert lst.json()["items"][0]["confirmation_type"]["name"] == platform["name"]

    # PUT — полная замена: не переданные nullable-поля (start_date/end_date/notes)
    # очищаются; import_independent обновляется.
    updated = client.put(
        f"/api/clients/{pid}/experience/{eid}",
        json={
            "title": "Разработка ИИ-сервиса v2",
            "customer_name": "ООО Заказчик",
            "contract_number": "К-1",
            "amount": 2000000.0,
            "confirmation_type_id": platform["id"],
            "import_independent": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["amount"] == 2000000.0
    assert updated.json()["import_independent"] is False
    assert updated.json()["start_date"] is None
    assert updated.json()["end_date"] is None
    assert updated.json()["notes"] is None

    assert client.delete(f"/api/clients/{pid}/experience/{eid}").status_code == 204
    assert client.get(f"/api/clients/{pid}/experience").json()["total"] == 0


def test_experience_validation(ple_client: TestClient) -> None:
    client = ple_client
    pid = _create_profile(client, "exp-valid-profile")
    r = client.post(
        f"/api/clients/{pid}/experience",
        json={"title": "X", "confirmation_type_id": 999999},
    )
    assert r.status_code == 422
    platform = next(
        t for t in client.get("/api/confirmation-types").json() if t["code"] == "platform"
    )
    r = client.post(
        f"/api/clients/{pid}/experience",
        json={"title": "", "confirmation_type_id": platform["id"]},
    )
    assert r.status_code == 422
    # Несуществующая запись — 404, даже если ссылка на справочник невалидна.
    r = client.put(
        f"/api/clients/{pid}/experience/999999",
        json={"title": "X", "confirmation_type_id": 999999},
    )
    assert r.status_code == 404
    assert client.delete(f"/api/clients/{pid}").status_code == 204


def test_profile_save_with_entries(ple_client: TestClient) -> None:
    """Лицензии/опыт сохраняются полной заменой вместе с профилем (BR-03)."""
    client = ple_client
    fstek = next(t for t in client.get("/api/license-types").json() if t["code"] == "fstek")
    platform = next(
        t for t in client.get("/api/confirmation-types").json() if t["code"] == "platform"
    )

    # Создание профиля сразу со списками лицензий и опыта.
    created = client.post(
        "/api/clients",
        json={
            "name": "entries-profile",
            "competencies": COMP_JSON,
            "keywords": [],
            "licenses": [{"license_type_id": fstek["id"], "number": "Л-1"}],
            "experience": [
                {
                    "title": "Опыт-1",
                    "confirmation_type_id": platform["id"],
                    "import_independent": True,
                }
            ],
        },
    )
    assert created.status_code == 200
    pid = created.json()["id"]

    lic = client.get(f"/api/clients/{pid}/licenses").json()
    assert lic["total"] == 1 and lic["items"][0]["number"] == "Л-1"
    exp = client.get(f"/api/clients/{pid}/experience").json()
    assert exp["total"] == 1 and exp["items"][0]["title"] == "Опыт-1"

    # PUT — полная замена: пустые списки очищают, новые списки заменяют.
    updated = client.put(
        f"/api/clients/{pid}",
        json={
            "name": "entries-profile",
            "competencies": COMP_JSON,
            "licenses": [],
            "experience": [{"title": "Опыт-2", "confirmation_type_id": platform["id"]}],
        },
    )
    assert updated.status_code == 200
    assert client.get(f"/api/clients/{pid}/licenses").json()["total"] == 0
    exp = client.get(f"/api/clients/{pid}/experience").json()
    assert exp["total"] == 1 and exp["items"][0]["title"] == "Опыт-2"

    # Неизвестный тип лицензии в списке — 422.
    r = client.put(
        f"/api/clients/{pid}",
        json={"name": "entries-profile", "licenses": [{"license_type_id": 999999}]},
    )
    assert r.status_code == 422

    assert client.delete(f"/api/clients/{pid}").status_code == 204


def test_tenant_isolation_and_cascade() -> None:
    """Изоляция BR-07 и каскадное удаление при удалении профиля."""

    async def _run() -> None:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            user_a = await repo.create_user("ple-a", "hash", [ROLE_ADMIN])
            user_b = await repo.create_user("ple-b", "hash", [ROLE_ADMIN])
            profile_a = await repo.upsert_profile(
                {"name": "A1", "competencies": COMP_JSON}, user_a.id
            )
            await repo.upsert_profile({"name": "A2", "competencies": COMP_JSON}, user_a.id)
            profile_b = await repo.upsert_profile(
                {"name": "B", "competencies": COMP_JSON}, user_b.id
            )
            assert profile_a.id is not None and profile_b.id is not None

            await repo.ensure_reference_data()
            license_types = await repo.list_license_types()
            conf_types = await repo.list_confirmation_types()
            assert license_types and conf_types

            await repo.create_license(
                profile_a.id,
                {"license_type_id": license_types[0].id, "number": "Л-1"},
            )
            await repo.create_experience(
                profile_a.id,
                {"title": "Опыт-1", "confirmation_type_id": conf_types[0].id},
            )
            # Кросс-тенант: записи профиля A не видны из профиля B.
            assert await repo.list_licenses(profile_b.id) == []
            assert await repo.list_experience(profile_b.id) == []
            # Удаление профиля каскадно удаляет лицензии и опыт (FK ON DELETE CASCADE).
            await repo.delete_profile(user_a.id, profile_a.id)
            async with db.session() as session:
                licenses = (
                    (
                        await session.execute(
                            select(ProfileLicense).where(ProfileLicense.profile_id == profile_a.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                experiences = (
                    (
                        await session.execute(
                            select(ProfileExperience).where(
                                ProfileExperience.profile_id == profile_a.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert licenses == []
                assert experiences == []
        finally:
            await db.dispose()

    asyncio.run(_run())
