"""Unit-тесты вердикта приемлемости закупки (analysis_service.pipeline.verdict)."""

from __future__ import annotations

from analysis_service.pipeline.verdict import compute_requirements_verdict, compute_verdict


def test_empty_requirements_gives_empty_verdict() -> None:
    result = compute_requirements_verdict({}, {"licenses": True})
    assert result == {}


def test_negated_category_never_blocks_even_if_profile_marks_it() -> None:
    requirements = {"licenses": [{"text": "Не установлено", "negated": True}]}
    result = compute_requirements_verdict(requirements, {"licenses": True})
    assert result["licenses"] == {"blocking": False, "negated": True, "count": 1}


def test_real_requirement_blocks_only_if_profile_says_so() -> None:
    requirements = {"licenses": [{"text": "Требуется лицензия МЧС"}]}
    blocked = compute_requirements_verdict(requirements, {"licenses": True})
    assert blocked["licenses"]["blocking"] is True

    not_blocked = compute_requirements_verdict(requirements, {"licenses": False})
    assert not_blocked["licenses"]["blocking"] is False

    default = compute_requirements_verdict(requirements, {})
    assert default["licenses"]["blocking"] is False


def test_mixed_negated_and_real_items_counts_as_real() -> None:
    # Хотя бы один НЕ-negated пункт — категория не считается полностью безопасной.
    requirements = {
        "experience": [
            {"text": "Опыт не требуется", "negated": True},
            {"text": "Опыт исполнения контрактов от 3 лет"},
        ]
    }
    result = compute_requirements_verdict(requirements, {"experience": True})
    assert result["experience"] == {"blocking": True, "negated": False, "count": 2}


def test_verdict_accepted_when_nothing_blocks() -> None:
    requirements = {"licenses": [{"text": "Не установлено", "negated": True}]}
    out = compute_verdict(requirements, {"licenses": True})
    assert out["verdict"]["accepted"] is True
    assert out["verdict"]["blocking_reasons"] == []


def test_verdict_rejected_with_reason_label() -> None:
    requirements = {
        "subcontractors": [{"text": "Исполнитель не вправе привлекать соисполнителей"}],
    }
    out = compute_verdict(requirements, {"subcontractors": True})
    assert out["verdict"]["accepted"] is False
    assert out["verdict"]["blocking_reasons"] == [
        {"source": "subcontractors", "label": "Допустимость привлечения соисполнителей"}
    ]


def test_verdict_absolute_question_blocks_without_profile_toggle() -> None:
    # verdict=absolute уже означает "запрет" по смыслу самого вердикта — не
    # нужен отдельный тумблер в профиле (в отличие от лицензий/опыта/etc.).
    questions = [
        {"question_id": "q1", "question_text": "Требуется опыт 5 лет?", "verdict": "absolute"},
        {"question_id": "q2", "question_text": "Есть скидка?", "verdict": "soft"},
    ]
    out = compute_verdict({}, {}, questions)
    assert out["verdict"]["accepted"] is False
    assert out["verdict"]["blocking_reasons"] == [
        {"source": "question:q1", "label": "«Требуется опыт 5 лет?»"}
    ]


def test_verdict_no_absolute_questions_does_not_block() -> None:
    questions = [{"question_id": "q1", "question_text": "Есть скидка?", "verdict": "soft"}]
    out = compute_verdict({}, {}, questions)
    assert out["verdict"]["accepted"] is True


def test_verdict_multiple_blocking_reasons() -> None:
    requirements = {
        "licenses": [{"text": "Требуется лицензия"}],
        "minprom": [{"text": "Требуется соответствие реестру Минпромторга"}],
    }
    out = compute_verdict(requirements, {"licenses": True, "minprom": True})
    sources = {r["source"] for r in out["verdict"]["blocking_reasons"]}
    assert sources == {"licenses", "minprom"}
    assert out["verdict"]["accepted"] is False


def test_verdict_blocking_field_mismatch_blocks() -> None:
    fields = [
        {"field_id": "f1", "field_name": "Объём", "blocking": True, "match": False},
        {"field_id": "f2", "field_name": "Цена", "blocking": True, "match": True},
    ]
    out = compute_verdict({}, {}, None, fields)
    assert out["verdict"]["accepted"] is False
    assert out["verdict"]["blocking_reasons"] == [{"source": "field:f1", "label": "Объём"}]


def test_verdict_field_not_blocking_when_toggle_off() -> None:
    fields = [{"field_id": "f1", "field_name": "Объём", "blocking": False, "match": False}]
    out = compute_verdict({}, {}, None, fields)
    assert out["verdict"]["accepted"] is True


def test_verdict_field_not_blocking_when_match_none_or_true() -> None:
    fields = [
        {"field_id": "f1", "field_name": "Объём", "blocking": True, "match": None},
        {"field_id": "f2", "field_name": "Цена", "blocking": True, "match": True},
    ]
    out = compute_verdict({}, {}, None, fields)
    assert out["verdict"]["accepted"] is True


def test_verdict_combines_requirements_questions_and_fields() -> None:
    requirements = {"licenses": [{"text": "Требуется лицензия"}]}
    questions = [{"question_id": "q1", "question_text": "Запрет?", "verdict": "absolute"}]
    fields = [{"field_id": "f1", "field_name": "Объём", "blocking": True, "match": False}]
    out = compute_verdict(requirements, {"licenses": True}, questions, fields)
    sources = {r["source"] for r in out["verdict"]["blocking_reasons"]}
    assert sources == {"licenses", "question:q1", "field:f1"}
    assert out["verdict"]["accepted"] is False
