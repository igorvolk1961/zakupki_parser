"""Unit-тесты вердикта приемлемости закупки (scoring_common.verdict)."""

from __future__ import annotations

from typing import Any

import pytest

from scoring_common.verdict import (
    compute_requirements_verdict,
    compute_verdict,
    experience_confirmation,
    normalize_requirement_severity,
)


def test_empty_requirements_gives_empty_verdict() -> None:
    assert compute_requirements_verdict({}, {"licenses": "block"}) == {}


def test_negated_category_never_a_barrier() -> None:
    requirements = {"licenses": [{"text": "Не установлено", "negated": True}]}
    result = compute_requirements_verdict(requirements, {"licenses": "block"})
    assert result["licenses"] == {"severity": None, "negated": True, "count": 1, "detail": None}


@pytest.mark.parametrize(
    ("setting", "expected"), [("block", "block"), ("soft", "soft"), ("off", None)]
)
def test_category_severity_from_profile(setting: str, expected: str | None) -> None:
    requirements = {"licenses": [{"text": "Требуется лицензия МЧС"}]}
    result = compute_requirements_verdict(requirements, {"licenses": setting})
    assert result["licenses"]["severity"] == expected


def test_missing_setting_is_off() -> None:
    requirements = {"minprom": [{"text": "Выписка из реестра Минпромторга"}]}
    assert compute_requirements_verdict(requirements, {})["minprom"]["severity"] is None


# --- BR-03: опыт — по способу подтверждения ----------------------------------------


@pytest.mark.parametrize(
    ("text", "confirmation"),
    [
        ("Опыт подтверждается в соответствии с ПП РФ № 2571", "platform"),
        ("документы, размещённые на электронной площадке", "platform"),
        ("выписка из реестра контрактов, содержащего сведения", "registry"),
        ("копии исполненных договоров и актов приёмки", "documents"),
        ("акты выполненных работ", "documents"),
        ("опыт учитывается в критериях оценки заявок", "evaluation_only"),
        ("наличие опыта", None),
    ],
)
def test_experience_confirmation_by_text(text: str, confirmation: str | None) -> None:
    assert experience_confirmation({"text": text}) == confirmation


def test_experience_confirmation_prefers_llm_data() -> None:
    item = {"text": "копии договоров", "data": {"confirmation": "platform"}}
    assert experience_confirmation(item) == "platform"
    assert experience_confirmation({"text": "опыт", "data": {"ref_2571": True}}) == "platform"


def _exp(*texts: str, negated: bool = False) -> dict[str, Any]:
    return {"experience": [{"text": t, "negated": negated} for t in texts]}


@pytest.mark.parametrize(
    ("texts", "codes", "expected"),
    [
        (["опыт подтверждается по ПП 2571"], [], "block"),
        (["опыт подтверждается по ПП 2571"], ["platform"], None),
        (["копии договоров и актов"], ["platform"], "soft"),
        (["выписка из реестра контрактов"], [], "soft"),
        (["опыт — показатель критерия оценки"], [], None),
        (["наличие опыта аналогичных работ"], [], "soft"),
        (["копии договоров", "опыт по ПП 2571"], [], "block"),
    ],
)
def test_br03_experience_severity(texts: list[str], codes: list[str], expected: str | None) -> None:
    result = compute_requirements_verdict(_exp(*texts), {"experience": "br03"}, codes)
    assert result["experience"]["severity"] == expected


def test_br03_off_and_negated() -> None:
    assert (
        compute_requirements_verdict(_exp("ПП 2571"), {"experience": "off"})["experience"][
            "severity"
        ]
        is None
    )
    negated = compute_requirements_verdict(_exp("ПП 2571", negated=True), {"experience": "br03"})
    assert negated["experience"]["severity"] is None


# --- Итог ---------------------------------------------------------------------------


def test_verdict_hard_and_soft_reasons() -> None:
    requirements = {
        "licenses": [{"text": "лицензия МЧС"}],
        "experience": [{"text": "копии договоров и актов"}],
    }
    fields = [
        {"field_id": "a", "field_name": "Коды ФККО", "severity": "block", "match": False},
        {"field_id": "b", "field_name": "Объём", "severity": "soft", "match": False},
        {"field_id": "c", "field_name": "Срок", "severity": "block", "match": None},
        {"field_id": "d", "field_name": "Цена", "severity": None, "match": False},
    ]
    verdict = compute_verdict(requirements, {"licenses": "soft", "experience": "br03"}, fields)[
        "verdict"
    ]
    assert verdict["accepted"] is False
    assert verdict["blocking_reasons"] == [{"source": "field:a", "label": "Коды ФККО"}]
    assert verdict["soft_reasons"] == [
        {"source": "licenses", "label": "Лицензии"},
        {
            "source": "experience",
            "label": "Опыт исполнения: подтверждение копиями договоров/актов",
        },
        {"source": "field:b", "label": "Объём"},
    ]


def test_verdict_accepted_with_only_soft() -> None:
    verdict = compute_verdict({"minprom": [{"text": "реестр"}]}, {"minprom": "soft"})["verdict"]
    assert verdict["accepted"] is True
    assert len(verdict["soft_reasons"]) == 1


def test_normalize_requirement_severity() -> None:
    assert normalize_requirement_severity({"licenses": "block", "experience": "br03"}) == {
        "licenses": "block",
        "experience": "br03",
    }
    with pytest.raises(ValueError, match="experience"):
        normalize_requirement_severity({"experience": "block"})
    with pytest.raises(ValueError, match="Неизвестная категория"):
        normalize_requirement_severity({"other": "block"})
    with pytest.raises(ValueError):
        normalize_requirement_severity({"licenses": True})
