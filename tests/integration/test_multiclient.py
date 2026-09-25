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
from typing import Any, cast

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
    # Как начальный администратор: активный аккаунт со всеми платными опциями.
    await repo.ensure_default_account(user.id, paid_default=True)
    profile = await repo.upsert_profile(
        {
            "name": "default",
            "enabled": True,
            "is_active": True,
            "competencies": COMP_JSON,
            "keywords": [],
            "exclusion_words": ["медицинский"],
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


async def _seed_profile_with_account(username: str, options: dict[str, bool]) -> int:
    """Отдельный пользователь + профиль + аккаунт с заданными платными опциями.

    Изолирован от общего пользователя ``mc_client`` (который легаси — без
    аккаунтов, поэтому имеет «полный доступ» ко всем платным опциям, см.
    ``effective_options``): здесь опции аккаунта заданы явно, чтобы проверить
    ``scoring_embeddings_enabled`` в /api/clients/active для КОНКРЕТНОГО набора
    переключателей, а не для легаси-дефолта.
    """
    db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
    await db.connect()
    try:
        repo = ProcurementRepository(db)
        user = await repo.create_user(username, "test-hash", [ROLE_USER])
        await repo.create_account(user.id, "default", options=options)
        profile = await repo.upsert_profile(
            {
                "name": "default",
                "enabled": True,
                "is_active": True,
                "competencies": COMP_JSON,
                "keywords": [],
                "exclusion_words": [],
            },
            user.id,
        )
        assert profile.id is not None
        return profile.id
    finally:
        await db.dispose()


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


@pytest.mark.slow  # первый тест модуля — оплачивает setup module-scoped mc_client
def test_clients_crud(mc_client: TestClient) -> None:
    client = mc_client
    active = client.get("/api/clients/active")
    assert active.status_code == 200
    assert active.json()["name"] == "default"
    assert active.json()["exclusion_words"] == ["медицинский"]

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


def test_save_profile_without_competencies_is_allowed(mc_client: TestClient) -> None:
    """US-10.7: запрет сохранять профиль без компетенций снят (даже для
    легаси-пользователя без аккаунта — раньше блокировался в первую очередь,
    см. account_provides_competency_scoring). LLM-скоринг по такому профилю
    просто не выполняется (Scheduler._profile_has_valid_competencies) —
    предупреждение об этом показывается в веб-редакторе, не запретом сохранения."""
    client = mc_client
    created = client.post("/api/clients", json={"name": "no-comp-profile"})
    assert created.status_code == 200, created.text
    body = created.json()
    profile = json.loads(body["competencies"])
    assert profile["competencies"] == []
    assert profile["positioning"] == ""

    updated = client.put(
        f"/api/clients/{body['id']}",
        json={"name": "no-comp-profile", "competencies": ""},
    )
    assert updated.status_code == 200, updated.text


@pytest.mark.slow  # test_clients_crud (обычный «первый») уже slow и деселектится
# под -m "not slow" — из-за этого setup module-scoped mc_client теперь платит
# ЭТОТ тест (артефакт атрибуции module-scoped фикстуры, не своя логика).
def test_active_client_exposes_scoring_embeddings_enabled_true(mc_client: TestClient) -> None:
    """/api/clients/active (X-Profile-ID, конвейер скоринга) отдаёт
    scoring_embeddings_enabled=True, когда опция включена в аккаунте владельца."""
    client = mc_client
    profile_id = asyncio.run(
        _seed_profile_with_account("embtest-on", {"scoring": True, "scoring_embeddings": True})
    )
    resp = client.get(
        "/api/clients/active",
        headers={**INTERNAL_HEADERS, "X-Profile-ID": str(profile_id)},
    )
    assert resp.status_code == 200
    assert resp.json()["scoring_embeddings_enabled"] is True


def test_active_client_exposes_scoring_embeddings_enabled_false_by_default(
    mc_client: TestClient,
) -> None:
    """Опция «эмбеддинги при скоринге» не включена в аккаунте (только «scoring») —
    scoring_embeddings_enabled=False (явное включение, как у остальных платных опций)."""
    client = mc_client
    profile_id = asyncio.run(_seed_profile_with_account("embtest-off", {"scoring": True}))
    resp = client.get(
        "/api/clients/active",
        headers={**INTERNAL_HEADERS, "X-Profile-ID": str(profile_id)},
    )
    assert resp.status_code == 200
    assert resp.json()["scoring_embeddings_enabled"] is False


class _FakeSchedulerForRefresh:
    """Минимальный планировщик для теста ``POST /api/clients/{id}/refresh``:
    только методы, которые реально вызывает ``refresh_client``/``_collection_
    notice`` (не полноценный ``Scheduler`` — не нужны БД/площадки)."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, bool, bool]] = []
        self._pending: set[int] = set()

    def request_profile_refresh(
        self, profile_id: int, *, rebuild: bool = False, rescore: bool = False
    ) -> None:
        self.calls.append((profile_id, rebuild, rescore))
        self._pending.add(profile_id)

    def profile_refresh_status(self, profile_id: int) -> dict[str, Any]:
        pending = profile_id in self._pending
        return {"pending": pending, "remaining_seconds": 0.0 if pending else None}


def test_refresh_client_unknown_profile_404(mc_client: TestClient) -> None:
    client = mc_client
    resp = client.post("/api/clients/999999999/refresh")
    assert resp.status_code == 404


def test_refresh_client_other_users_profile_404(mc_client: TestClient) -> None:
    """Tenant-скоуп (BR-07): принудительное обновление чужого профиля — 404,
    как и у остальных эндпоинтов профиля (get_profile фильтрует по user_id)."""
    client = mc_client
    other_profile_id = asyncio.run(
        _seed_profile_with_account("refreshtest-other", {"scoring": True})
    )
    resp = client.post(f"/api/clients/{other_profile_id}/refresh")
    assert resp.status_code == 404


def test_refresh_client_disabled_profile_no_scheduler_call(mc_client: TestClient) -> None:
    """Отключённый профиль: уведомление говорит «включите и сохраните», и
    planировщик вообще не вызывается (``_request_refresh_for`` гейтит по
    ``profile.enabled``, как и у сохранения)."""
    client = mc_client
    created = client.post(
        "/api/clients",
        json={"name": "refresh-disabled", "competencies": COMP_JSON, "enabled": False},
    )
    assert created.status_code == 200
    profile_id = created.json()["id"]

    fake = _FakeSchedulerForRefresh()
    app_state = cast(Any, client.app).state.parser
    app_state.parser_scheduler = fake
    try:
        resp = client.post(f"/api/clients/{profile_id}/refresh")
        assert resp.status_code == 200
        assert "отключён" in resp.json()["notice"]
        assert fake.calls == []
    finally:
        app_state.parser_scheduler = None


def test_refresh_client_enabled_profile_calls_scheduler_with_rebuild(
    mc_client: TestClient,
) -> None:
    """Включённый профиль: тот же fast-start путь, что и у сохранения
    (``request_profile_refresh(id, rebuild=True, rescore=False)``), уведомление
    начинается с «Обновление запрошено» (не «Профиль сохранён» — профиль не
    менялся)."""
    client = mc_client
    created = client.post(
        "/api/clients",
        json={"name": "refresh-enabled", "competencies": COMP_JSON, "enabled": True},
    )
    assert created.status_code == 200
    profile_id = created.json()["id"]

    fake = _FakeSchedulerForRefresh()
    app_state = cast(Any, client.app).state.parser
    app_state.parser_scheduler = fake
    try:
        resp = client.post(f"/api/clients/{profile_id}/refresh")
        assert resp.status_code == 200
        body = resp.json()
        assert body["notice"].startswith("Обновление запрошено")
        assert fake.calls == [(profile_id, True, False)]

        # Повторное нажатие сразу же — throttle уже "pending", сообщение не
        # содержит "не выполняется" (профиль всё ещё включён и обрабатывается).
        resp2 = client.post(f"/api/clients/{profile_id}/refresh")
        assert resp2.status_code == 200
        assert fake.calls == [(profile_id, True, False), (profile_id, True, False)]
    finally:
        app_state.parser_scheduler = None


def _seed_indexed_procurement(number: str, subject: str, okpd2_codes: str) -> int:
    """Закупка с заданными предметом/ОКПД2 (для проверки синхронного пересбора
    из индекса — subject-матчинг, без отдельного procurement_search_index)."""

    async def _seed() -> int:
        db = Database(DbConfig(dsn=TEST_DSN, enabled=True))
        await db.connect()
        try:
            repo = ProcurementRepository(db)
            await repo.upsert(
                {
                    "number": number,
                    "platform_id": "zakupki_mos",
                    "subject": subject,
                    "okpd2_codes": okpd2_codes,
                }
            )
            rows, _ = await repo.list_procurements(number=number)
            return rows[0].id
        finally:
            await db.dispose()

    return asyncio.run(_seed())


def test_create_client_fully_index_covered_matches_immediately_without_scheduler(
    mc_client: TestClient,
) -> None:
    """Профиль, ЦЕЛИКОМ покрытый диапазоном фоновой индексации: создание сразу
    пересобирает результаты из уже проиндексированных данных — без throttle и
    БЕЗ работающего планировщика (``parser_scheduler`` весь тест остаётся
    ``None`` — сбор данных всё равно уже произошёл, синхронно)."""
    client = mc_client
    app_state = cast(Any, client.app).state.parser
    assert app_state.parser_scheduler is None
    app_state.cfg.service.indexing.enabled = True
    app_state.cfg.service.indexing.okpd2_prefixes = ["62"]
    try:
        procurement_id = _seed_indexed_procurement(
            "IDX-COV-NEW", "Разработка робототехнического комплекса", "62.01.11"
        )
        created = client.post(
            "/api/clients",
            json={
                "name": "idx-covered-new",
                "competencies": COMP_JSON,
                "enabled": True,
                "is_active": True,
                "okpd_codes": ["62.01"],
                "keywords": ["робототехническ*"],
            },
        )
        assert created.status_code == 200, created.text
        assert app_state.parser_scheduler is None  # ни разу не понадобился
        assert "проиндексированный диапазон" in created.json()["notice"]

        listed = client.get("/api/procurements", params={"number": "IDX-COV-NEW"})
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert any(item["id"] == procurement_id for item in items)
    finally:
        app_state.cfg.service.indexing.enabled = False
        app_state.cfg.service.indexing.okpd2_prefixes = []


def test_refresh_client_fully_index_covered_skips_scheduler_no_throttle(
    mc_client: TestClient,
) -> None:
    """«Обновить сейчас» для профиля, ЦЕЛИКОМ покрытого индексацией: планировщик
    не вызывается вовсе (throttle его не касается) — повторное нажатие подряд
    снова синхронно пересобирает результаты, а не «остаётся в очереди»."""
    client = mc_client
    app_state = cast(Any, client.app).state.parser
    app_state.cfg.service.indexing.enabled = True
    app_state.cfg.service.indexing.okpd2_prefixes = ["62"]
    try:
        created = client.post(
            "/api/clients",
            json={
                "name": "idx-covered-refresh",
                "competencies": COMP_JSON,
                "enabled": True,
                "okpd_codes": ["62.01"],
                "keywords": ["робот*"],
            },
        )
        assert created.status_code == 200
        profile_id = created.json()["id"]

        fake = _FakeSchedulerForRefresh()
        app_state.parser_scheduler = fake
        try:
            resp1 = client.post(f"/api/clients/{profile_id}/refresh")
            assert resp1.status_code == 200
            assert "проиндексированный диапазон" in resp1.json()["notice"]
            resp2 = client.post(f"/api/clients/{profile_id}/refresh")
            assert resp2.status_code == 200
            # Планировщик не просился НИ РАЗУ — throttle к нему не применяется.
            assert fake.calls == []
        finally:
            app_state.parser_scheduler = None
    finally:
        app_state.cfg.service.indexing.enabled = False
        app_state.cfg.service.indexing.okpd2_prefixes = []


def test_update_client_partial_index_coverage_still_calls_scheduler(
    mc_client: TestClient,
) -> None:
    """Частичное покрытие (часть кодов вне диапазона индексации): синхронный
    пересбор по покрытой части выполняется, но планировщик ВСЁ РАВНО просится —
    непокрытая часть кодов нуждается в живом обходе (throttle защищает его)."""
    client = mc_client
    app_state = cast(Any, client.app).state.parser
    app_state.cfg.service.indexing.enabled = True
    app_state.cfg.service.indexing.okpd2_prefixes = ["62"]
    try:
        created = client.post(
            "/api/clients",
            json={
                "name": "idx-partial",
                "competencies": COMP_JSON,
                "enabled": True,
                "okpd_codes": ["71"],
            },
        )
        assert created.status_code == 200
        profile_id = created.json()["id"]

        fake = _FakeSchedulerForRefresh()
        app_state.parser_scheduler = fake
        try:
            resp = client.put(
                f"/api/clients/{profile_id}",
                json={
                    "name": "idx-partial",
                    "competencies": COMP_JSON,
                    "enabled": True,
                    "okpd_codes": ["62.01", "71.20"],
                },
            )
            assert resp.status_code == 200, resp.text
            # Частичное покрытие — уведомление НЕ говорит «обход не требуется»,
            # планировщик просился для непокрытого остатка (код 71.20).
            assert "проиндексированный диапазон" not in resp.json()["notice"]
            assert fake.calls == [(profile_id, True, False)]
        finally:
            app_state.parser_scheduler = None
    finally:
        app_state.cfg.service.indexing.enabled = False
        app_state.cfg.service.indexing.okpd2_prefixes = []


def test_add_exclusion_word_retroactively_removes_already_matched_procurement(
    mc_client: TestClient,
) -> None:
    """Баг: добавление слова-исключения через карточку («В исключения») само по
    себе только сохраняло слово в keywords, но НЕ пересматривало уже отобранные
    закупки (procurement_evaluations) — закупка, которую слово должно было
    исключить, оставалась видна до следующего сохранения профиля. Теперь
    add_procurement_exclusion_word сам запускает тот же пересбор, что и
    сохранение профиля (_sync_profile_results) — для полностью покрытого
    индексом профиля пересбор синхронный, эффект виден сразу же, без
    отдельного сохранения/«Обновить сейчас»."""
    client = mc_client
    app_state = cast(Any, client.app).state.parser
    app_state.cfg.service.indexing.enabled = True
    app_state.cfg.service.indexing.okpd2_prefixes = ["62"]
    try:
        procurement_id = _seed_indexed_procurement(
            "IDX-EXCL-1", "Разработка робототехнического комплекса", "62.01.11"
        )
        created = client.post(
            "/api/clients",
            json={
                "name": "idx-excl",
                "competencies": COMP_JSON,
                "enabled": True,
                "is_active": True,
                "okpd_codes": ["62.01"],
                "keywords": ["робототехническ*"],
            },
        )
        assert created.status_code == 200, created.text

        # Закупка отобрана профилем сразу после создания (полностью покрыт индексом).
        listed = client.get("/api/procurements", params={"number": "IDX-EXCL-1"})
        assert any(item["id"] == procurement_id for item in listed.json()["items"])

        # Добавляем слово-исключение из карточки — БЕЗ отдельного сохранения
        # профиля/«Обновить сейчас».
        resp = client.post(
            f"/api/procurements/{procurement_id}/exclusion-word",
            json={"word": "комплекса"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["added"] is True

        # Закупка должна пропасть из выдачи профиля СРАЗУ ЖЕ.
        listed2 = client.get("/api/procurements", params={"number": "IDX-EXCL-1"})
        assert all(item["id"] != procurement_id for item in listed2.json()["items"])
    finally:
        app_state.cfg.service.indexing.enabled = False
        app_state.cfg.service.indexing.okpd2_prefixes = []


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


def test_profile_export_import_restores_report_fields_and_blocking(
    mc_client: TestClient,
) -> None:
    """Профиль переносится полностью: отчётные поля с условиями и уровни
    барьеров категорий требований уходят в файл и восстанавливаются импортом."""
    client = mc_client
    fields = [
        {
            "id": "fkko",
            "name": "коды ФККО",
            "hint": "коды отходов",
            "type": "list",
            "value_mode": "code",
            "extend_list": False,
            "condition": {"op": "all_in", "value": ["1 11 010 21 49 2", "4 71 101 01 52 1"]},
            "severity": "block",
        }
    ]
    severity = {
        "licenses": "block",
        "experience": "br03",
        "minprom": "soft",
        "subcontractors": "off",
    }
    created = client.post(
        "/api/clients",
        json={
            "name": "portable-profile",
            "competencies": COMP_JSON,
            "report_fields": fields,
            "requirement_severity": severity,
        },
    )
    assert created.status_code == 200, created.text
    profile_id = created.json()["id"]
    saved_fields = created.json()["report_fields"]
    assert saved_fields[0]["condition"]["value"] == ["1 11 010 21 49 2", "4 71 101 01 52 1"]

    exported = client.get(f"/api/clients/{profile_id}/export")
    assert exported.status_code == 200
    content = exported.json()["profile_content"]
    profile = json.loads(content)["profile"]
    assert profile["report_fields"] == saved_fields
    assert profile["requirement_severity"] == severity
    assert profile["report_field_mapping"] == {}

    # Портим профиль и восстанавливаем из файла.
    cleared = client.put(
        f"/api/clients/{profile_id}",
        json={
            "name": "portable-profile",
            "competencies": COMP_JSON,
            "report_fields": [],
            "requirement_severity": {},
        },
    )
    assert cleared.status_code == 200
    assert cleared.json()["report_fields"] == []

    imported = client.post("/api/clients/import", json={"content": content})
    assert imported.status_code == 200, imported.text
    assert imported.json()["id"] == profile_id
    assert imported.json()["report_fields"] == saved_fields
    assert imported.json()["requirement_severity"] == severity


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


@pytest.mark.slow
def test_rag_report_via_score_endpoint(mc_client: TestClient) -> None:
    """rag_report — свободная JSONB: score-эндпоинт сохраняет и отдаёт её как
    есть, не привязываясь к конкретным ключам (fields/requirements_verdict/…)."""
    client = mc_client
    procurement_id = _seed_procurement()
    report = {
        "tz_found": True,
        "tz_file": "ТЗ.docx",
        "status": "ok",
        "fields": [
            {
                "field_id": "f1",
                "field_name": "Объём партии",
                "found": True,
                "value": "4000",
                "severity": None,
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
    assert card["rag_report"]["fields"][0]["field_name"] == "Объём партии"
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


@pytest.mark.slow  # state.notify_min_fit_score по умолчанию 0.0 и notifier не
# подменён фейком (в отличие от test_set_score_notifies_above_threshold) —
# POST /score с fit_score=0.9 уходит в реальный Notifier, который ждёт таймаут
# сетевого вызова (~15с). Не связано с логикой самого теста/list_procurements.
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
