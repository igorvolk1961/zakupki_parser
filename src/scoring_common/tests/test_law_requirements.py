"""Unit-тесты сопоставления требований закупки с универсальными требованиями 44-ФЗ."""

from __future__ import annotations

from scoring_common.law_requirements import (
    annotate_requirements,
    is_negated,
    is_universal,
    load_law_requirements,
    universal_items,
)

# Минимальный реестр (эталон ст. 31 ч.1 — универсальные требования).
_LAW = {
    "parts": [
        {
            "number": "1",
            "text": "Заказчик устанавливает единые требования.",
            "items": [
                {
                    "number": "3)",
                    "text": (
                        "непроведение ликвидации участника закупки - юридического лица и "
                        "отсутствие решения арбитражного суда о признании участника закупки "
                        "несостоятельным (банкротом) и об открытии конкурсного производства;"
                    ),
                },
            ],
        },
        {
            "number": "2",
            "text": "Дополнительные требования.",
            "items": [
                {"number": "1)", "text": "наличия финансовых ресурсов для исполнения контракта;"}
            ],
        },
    ],
}


def test_load_law_requirements_missing_file() -> None:
    assert load_law_requirements("/no/such/path.json") == {}


def test_load_law_requirements_bundled() -> None:
    """Реестр по умолчанию загружается из ресурса пакета (data/)."""
    doc = load_law_requirements()
    assert universal_items(doc), "реестр универсальных требований закона должен быть непустым"


def test_universal_items_only_part_one() -> None:
    items = universal_items(_LAW)
    assert len(items) == 1
    assert "ликвидации" in items[0]


def test_is_universal_detects_verbatim_repeat() -> None:
    text = (
        "11.2 Непроведение ликвидации участника закупки - юридического лица и отсутствие "
        "решения арбитражного суда о признании участника закупки несостоятельным (банкротом) "
        "и об открытии конкурсного производства."
    )
    assert is_universal(text, _LAW)


def test_is_universal_false_for_specific() -> None:
    assert not is_universal("13 Дополнительные требования к аудиторским услугам.", _LAW)


def test_is_negated_uses_net_not_phrase() -> None:
    # маркер-слово без конвертации в «НЕТ» отрицанием не считается.
    assert not is_negated({"text": "…Не установлено", "additional": ""})
    assert is_negated({"additional": "НЕТ"})
    assert is_negated({"text": "Требование … НЕТ"})
    # «не установлено иное» — оборот, а не отрицание нормы.
    assert not is_negated({"text": "если Правительством не установлено иное.", "additional": ""})


def test_annotate_adds_show_hide_flags() -> None:
    structure = {
        "other": [
            {
                "text": (
                    "11.2 Непроведение ликвидации участника закупки - юридического лица и "
                    "отсутствие решения арбитражного суда о признании участника закупки "
                    "несостоятельным (банкротом) и об открытии конкурсного производства."
                ),
                "data": None,
                "file_name": "x.pdf",
            },
            {
                "text": "13 Дополнительные требования к аудиторским услугам.",
                "data": None,
                "file_name": "x.pdf",
                "additional": "НЕТ",
            },
        ]
    }
    annotate_requirements(structure, _LAW)
    uni, specific = structure["other"]
    assert uni["universal"] is True
    assert uni["negated"] is False
    assert uni["show"] is False  # универсальное без отрицания — скрыто
    assert specific["universal"] is False
    assert specific["negated"] is True
    assert specific["show"] is True  # отрицание нормы/специфика — показано
