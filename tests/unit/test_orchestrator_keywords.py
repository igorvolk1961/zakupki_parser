"""Unit-тесты серверного обхода по ОКПД2 и клиентской фильтрации словами (R9).

По требованию заказчика ключевые слова НЕ передаются на площадку: серверная
фильтрация — только по кодам ОКПД2 (+ обход «без кода»); позитивные/негативные
слова применяются клиентски до записи в БД (см. test_filtering.py). Обход
«без кода» выключен по умолчанию и включается флагом config_service.yaml
``search_criteria.no_code_search``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from playwright.async_api import Page

from zakupki_parser.config.models import AppConfig, RetryConfig, SearchCriteria
from zakupki_parser.parser.orchestrator import Orchestrator
from zakupki_parser.parser.orchestrator.context import ProfileRunContext
from zakupki_parser.storage.db import Profile


class _OkCircuit:
    def allow_request(self) -> bool:
        return True

    def record_success(self) -> None:
        pass


class _Recorder(Orchestrator):
    """Перехватывает _crawl и записывает критерии каждого поискового обхода."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.crawled: list[SearchCriteria] = []
        # Профили, участвовавшие в каждом обходе (мультипрофильная ветка).
        self.crawled_profiles: list[list[Any]] = []

    async def _crawl(
        self,
        page: Page,
        cutoff: datetime | None,
        criteria: SearchCriteria,
        by_relevance: bool,
        retry_cfg: RetryConfig,
    ) -> None:
        self.crawled.append(criteria)
        self.crawled_profiles.append(list(self._profile_ctxs))


def _make_recorder(app_config: AppConfig) -> _Recorder:
    cfg = app_config.model_copy(deep=True)
    return _Recorder(
        cfg=cfg,
        platform_id="zakupki_mos",
        platform=cfg.dom.platforms["zakupki_mos"],
        delayer=object(),
        repository=None,
        notifier=None,
        site_cb=_OkCircuit(),
        db_cb=_OkCircuit(),
        now=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
    )


class _FakeUser:
    id = 1


class _FakeProfile:
    """Активный профиль: только okpd_codes влияет на выбор обходов."""

    id = 1
    target_etp: list[str] = []

    def __init__(self, okpd_codes: list[str]) -> None:
        self.okpd_codes = okpd_codes
        self.nmck_min = None
        self.nmck_max = None
        self.target_etp = []


class _ProfileRepo:
    """Репозиторий с активным профилем и словами (для обхода без БД)."""

    def __init__(self, profile: _FakeProfile, keywords: list[str]) -> None:
        self._profile = profile
        self._keywords = keywords

    async def first_user(self) -> _FakeUser:
        return _FakeUser()

    async def get_active_profile(self, user_id: int) -> _FakeProfile:
        return self._profile

    async def get_profile_keywords(self, profile_id: int) -> dict[str, list[str]]:
        return {"keywords": self._keywords, "exclusion_words": []}

    async def known_numbers(self, platform_id: str) -> set[str]:
        return set()

    async def last_processed_date(self, *args: Any, **kwargs: Any) -> datetime:
        return datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_server_crawl_has_no_keywords(app_config: AppConfig) -> None:
    """R9: в серверных запросах нет ключевых слов — только коды ОКПД2 из профиля.

    Без активного профиля (repository=None) критерии из глобального конфига
    НЕ используются: обход по кодам не выполняется.
    """
    recorder = _make_recorder(app_config)
    await recorder.run(page=object())  # type: ignore[arg-type]

    assert recorder.crawled == []


@pytest.mark.asyncio
async def test_no_code_crawl_skipped_without_profile(app_config: AppConfig) -> None:
    """Без активного профиля (repository=None) обход «без кода» не выполняется.

    Обход «без кода» нужен только при наличии позитивных ключевых слов в профиле —
    иначе он бессмыслен (отбирать не по чему): пропускается с записью в лог.
    """
    recorder = _make_recorder(app_config)
    await recorder.run(page=object())  # type: ignore[arg-type]

    # Без профиля (repository=None) ни обход по кодам, ни «без кода» не выполняются.
    assert recorder.crawled == []


@pytest.mark.asyncio
async def test_no_code_crawl_disabled_by_default(app_config: AppConfig) -> None:
    """Пустые коды ОКПД2 + слова: обход «без кода» ВЫКЛЮЧЕН по умолчанию.

    Флаг config_service.yaml search_criteria.no_code_search=false (по умолчанию) —
    обход «без кода» пропускается, даже если в профиле есть позитивные слова.
    """
    recorder = _make_recorder(app_config)
    recorder._repository = _ProfileRepo(_FakeProfile([]), ["ИИ"])  # type: ignore[assignment]
    await recorder.run(page=object())  # type: ignore[arg-type]

    assert recorder.crawled == []


@pytest.mark.asyncio
async def test_no_code_crawl_runs_with_flag(app_config: AppConfig) -> None:
    """Пустые коды ОКПД2 + слова + флаг no_code_search=true: обход «без кода» идёт.

    Пустой список кодов площадка воспринимает как «любой код» (фильтр okpdPaths не
    ставится) — обход «без кода» качает весь реестр и фильтрует его клиентски.
    """
    recorder = _make_recorder(app_config)
    recorder._cfg.service.search_criteria.no_code_search = True
    await recorder.run(page=object(), profiles=[_ctx([], ["ИИ"])])  # type: ignore[arg-type]

    assert len(recorder.crawled) == 1
    assert recorder.crawled[0].okpd_codes == []


@pytest.mark.asyncio
async def test_codes_and_no_code_crawls_when_codes_present(app_config: AppConfig) -> None:
    """Коды заданы + слова + флаг: два прохода — по кодам ОКПД2 и отдельно «без кода».

    Обход «без кода» дополняет кодовый, чтобы не терять закупки, подходящие по
    словам, но вне заданных кодов (например, на mos при okpd_codes=['62.02']).
    """
    recorder = _make_recorder(app_config)
    recorder._cfg.service.search_criteria.no_code_search = True
    await recorder.run(page=object(), profiles=[_ctx(["62.02"], ["ИИ"])])  # type: ignore[arg-type]

    assert [c.okpd_codes for c in recorder.crawled] == [["62.02"], []]


def _ctx(okpd: list[str], keywords: list[str], profile_id: int = 1) -> ProfileRunContext:
    return ProfileRunContext(
        profile=cast(Profile, _FakeProfile(okpd)), keywords=keywords, exclusion_words=[]
    )


@pytest.mark.asyncio
async def test_multiple_profiles_same_criteria_single_crawl(app_config: AppConfig) -> None:
    """Два профиля с одинаковыми кодами ОКПД2 -> ОДИН обход (дедупликация).

    Идентичные запросы к площадке разных профилей объединяются: запрос
    выполняется один раз, результаты раздаются каждому профилю веером.
    """
    recorder = _make_recorder(app_config)
    await recorder.run(
        page=object(),  # type: ignore[arg-type]
        profiles=[_ctx(["62.02"], ["ИИ"]), _ctx(["62.02"], ["разработка"])],
    )

    assert len(recorder.crawled) == 1
    assert recorder.crawled[0].okpd_codes == ["62.02"]
    assert len(recorder.crawled_profiles[0]) == 2


@pytest.mark.asyncio
async def test_multiple_profiles_different_criteria_separate_crawls(
    app_config: AppConfig,
) -> None:
    """Разные коды ОКПД2 -> разные обходы (по одному на набор кодов)."""
    recorder = _make_recorder(app_config)
    await recorder.run(
        page=object(),  # type: ignore[arg-type]
        profiles=[_ctx(["62.02"], ["ИИ"]), _ctx(["63.11"], ["разработка"])],
    )

    assert len(recorder.crawled) == 2
    assert {tuple(c.okpd_codes) for c in recorder.crawled} == {("62.02",), ("63.11",)}


@pytest.mark.asyncio
async def test_deduplicate_disabled_splits_crawl(app_config: AppConfig) -> None:
    """deduplicate_requests=false: одинаковые критерии разных профилей — раздельно."""
    recorder = _make_recorder(app_config)
    recorder._cfg.service.deduplicate_requests = False
    await recorder.run(
        page=object(),  # type: ignore[arg-type]
        profiles=[_ctx(["62.02"], ["ИИ"]), _ctx(["62.02"], ["разработка"])],
    )

    assert len(recorder.crawled) == 2
    assert all(c.okpd_codes == ["62.02"] for c in recorder.crawled)
