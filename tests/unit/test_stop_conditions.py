"""Unit-тесты условий прекращения обработки закупки."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from zakupki_parser.config.models import AppConfig
from zakupki_parser.parser.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_deadline_expired_skips(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None, deadline_not_expired=True)
    record = {"number": "1", "deadline": datetime(2026, 8, 1, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is True  # noqa: SLF001


@pytest.mark.asyncio
async def test_deadline_future_not_skipped(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None, deadline_not_expired=True)
    record = {"number": "2", "deadline": datetime(2026, 8, 10, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is False  # noqa: SLF001


def _make_orch(
    app_config: AppConfig,
    now: datetime,
    min_deadline_days: int | None,
    deadline_not_expired: bool = True,
    keyword_context_required: bool = False,
) -> Orchestrator:
    cfg = app_config.model_copy(deep=True)
    cfg.service.stop_conditions.min_deadline_days = min_deadline_days
    cfg.service.stop_conditions.deadline_not_expired = deadline_not_expired
    cfg.service.stop_conditions.keyword_context_required = keyword_context_required
    return Orchestrator(
        cfg=cfg,
        platform_id="zakupki_mos",
        platform=cfg.dom.platforms["zakupki_mos"],
        delayer=object(),  # type: ignore[arg-type]
        repository=None,
        notifier=None,  # type: ignore[arg-type]
        site_cb=None,  # type: ignore[arg-type]
        db_cb=None,  # type: ignore[arg-type]
        now=now,
    )


@pytest.mark.asyncio
async def test_min_deadline_days_too_close_skips(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=5)
    # до дедлайна 2 дня < 5 -> пропустить
    record = {"number": "3", "deadline": datetime(2026, 8, 5, 12, 0, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is True  # noqa: SLF001


@pytest.mark.asyncio
async def test_min_deadline_days_enough_kept(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=5)
    # до дедлайна 10 дней >= 5 -> обрабатывать
    record = {"number": "4", "deadline": datetime(2026, 8, 13, 12, 0, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is False  # noqa: SLF001


@pytest.mark.asyncio
async def test_min_deadline_days_disabled(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None)
    record = {"number": "5", "deadline": datetime(2026, 8, 4, 12, 0, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is False  # noqa: SLF001


@pytest.mark.asyncio
async def test_min_deadline_days_ignored_when_deadline_check_off(
    app_config: AppConfig,
) -> None:
    """deadline_not_expired=false отключает и min_deadline_days (дедлайн не режется)."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(
        app_config,
        now,
        min_deadline_days=5,
        deadline_not_expired=False,
    )
    record = {"number": "6", "deadline": datetime(2026, 8, 1, 12, 0, tzinfo=UTC)}
    assert orch._check_stop_conditions(record) is False  # noqa: SLF001


def test_is_known_skips_existing(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None)
    # Без загруженного набора (репозиторий None) — ничего не пропускаем.
    assert orch._is_known("1") is False  # noqa: SLF001
    orch._known_numbers = {"1", "2"}
    assert orch._is_known("1") is True  # noqa: SLF001
    assert orch._is_known("3") is False  # noqa: SLF001
    assert orch._is_known(None) is False  # noqa: SLF001


def test_is_active_matches_active_status(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None)
    assert orch._is_active({"status": "Прием предложений"}) is True  # noqa: SLF001


def test_is_active_normalizes_case_and_ellipsis(app_config: AppConfig) -> None:
    """Верхний регистр и хвостовое CSS-обрезание '...' не ломают сопоставление."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None)
    assert orch._is_active({"status": "ПРИЕМ ПРЕДЛОЖЕНИЙ ..."}) is True  # noqa: SLF001
    assert orch._is_active({"status": "Прием предложений ..."}) is True  # noqa: SLF001


def test_is_active_inactive_status(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None)
    assert orch._is_active({"status": "Прием предложений завершен"}) is False  # noqa: SLF001
    assert orch._is_active({"status": ""}) is False  # noqa: SLF001
    assert orch._is_active({}) is False  # noqa: SLF001


def test_is_active_ignores_deadline_at_write(app_config: AppConfig) -> None:
    """Срок актуальности не влияет на is_active при записи в БД.

    Проверка текущей даты — обязанность клиента (репозиторий/API),
    см. ``effective_is_active``.
    """
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None)
    expired = {"status": "Прием предложений", "deadline": datetime(2026, 8, 1, tzinfo=UTC)}
    assert orch._is_active(expired) is True  # noqa: SLF001


def test_is_active_default_true_when_no_statuses(app_config: AppConfig) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None)
    orch._platform.list_config.active_statuses = None
    assert orch._is_active({"status": "anything"}) is True  # noqa: SLF001
    assert orch._is_active({}) is True  # noqa: SLF001


def test_keyword_context_missing_skips(app_config: AppConfig) -> None:
    """Буквосочетание слова входит в другое слово — закупка не проходит."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None, keyword_context_required=True)
    record = {"number": "10", "subject": "Услуга по оценки инвестиции"}
    assert orch._check_stop_conditions(record, keywords=["ИИ"]) is True  # noqa: SLF001


def test_keyword_context_embedded_word_skips(app_config: AppConfig) -> None:
    """Отсекаются и другие слова-подстроки ('облигации', 'полуавтоматизация')."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None, keyword_context_required=True)
    for subject, keyword in (
        ("облигации и акции", "ИИ"),
        ("полуавтоматизация", "автоматизация"),
        ("фотоискусственный интеллект", "искусственный интеллект"),
    ):
        record = {"number": "11", "subject": subject}
        assert (
            orch._check_stop_conditions(record, keywords=[keyword]) is True  # noqa: SLF001
        )


def test_keyword_context_present_kept(app_config: AppConfig) -> None:
    """Отдельное слово или слово перед дефисом — закупка проходит."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None, keyword_context_required=True)
    for subject in (
        "Разработка ИИ-решений",
        "(ИИ-системы)",
        "система ИИ",
        "ИИ и автоматизация",
        "применение ИИ.",
        "решение (ИИ)",
        "инвестиции ИИ",
    ):
        record = {"number": "12", "subject": subject}
        assert (
            orch._check_stop_conditions(record, keywords=["ИИ"]) is False  # noqa: SLF001
        )


def test_keyword_context_any_word_kept(app_config: AppConfig) -> None:
    """Достаточно одного ключевого слова в контексте (поиск по нескольким словам)."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None, keyword_context_required=True)
    record = {"number": "13", "subject": "Разработка системы учета"}
    assert (
        orch._check_stop_conditions(record, keywords=["ИИ", "системы"]) is False  # noqa: SLF001
    )
    assert (
        orch._check_stop_conditions(record, keywords=["ИИ", "нейросеть"]) is True  # noqa: SLF001
    )


def test_keyword_context_empty_subject_skips(app_config: AppConfig) -> None:
    """Пустой subject при включённом флаге — критический дефект, закупка не идёт дальше."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    orch = _make_orch(app_config, now, min_deadline_days=None, keyword_context_required=True)
    assert (
        orch._check_stop_conditions({"number": "14", "subject": ""}, keywords=["ИИ"])  # noqa: SLF001
        is True
    )
    assert (
        orch._check_stop_conditions({"number": "15"}, keywords=["ИИ"]) is True  # noqa: SLF001
    )


def test_keyword_context_disabled_or_no_keywords(app_config: AppConfig) -> None:
    """Флаг выключен или обход без ключевых слов (ОКПД2) — условие не применяется."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    record = {"number": "16", "subject": "Услуга по оценки инвестиции"}
    orch_disabled = _make_orch(app_config, now, min_deadline_days=None)
    assert (
        orch_disabled._check_stop_conditions(record, keywords=["ИИ"]) is False  # noqa: SLF001
    )
    orch_enabled = _make_orch(
        app_config, now, min_deadline_days=None, keyword_context_required=True
    )
    assert orch_enabled._check_stop_conditions(record, keywords=[]) is False  # noqa: SLF001
    assert orch_enabled._check_stop_conditions(record, keywords=None) is False  # noqa: SLF001
