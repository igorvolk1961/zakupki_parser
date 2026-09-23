"""Тексты требований для Excel-экспорта карточки: сводка по лицензиям и статусы.

Лицензии в отчёте больше не выводятся сырым текстом — показывается компактная
сводка «какой вид нужен + есть ли у поставщика» (``requirements_status.licenses``
из analysis_service). Здесь проверяется формирование текста для XLSX.
"""

from __future__ import annotations

from zakupki_parser.api.app.routes.procurements import (
    _license_summary_text,
    _requirement_status_text,
)


def test_license_summary_text_lists_kinds_with_availability() -> None:
    status = {
        "required": True,
        "items": [
            {"label": "Лицензия МЧС (пожарная безопасность)", "available": False},
            {"label": "Лицензия ФСТЭК", "available": True},
            {"label": "Неизвестный допуск", "available": None},
        ],
    }
    text = _license_summary_text(status)
    assert "Лицензия МЧС (пожарная безопасность) — нет у поставщика" in text
    assert "Лицензия ФСТЭК — есть у поставщика" in text
    assert "Неизвестный допуск — требует проверки" in text


def test_license_summary_text_without_items_reports_status() -> None:
    assert (
        _license_summary_text({"required": False, "negated": True, "items": []})
        == "Не требуется (пометка «не установлено»/«не требуется»)"
    )
    assert (
        _license_summary_text({"required": False, "negated": False, "items": []})
        == "Требования не найдены"
    )


def test_requirement_status_text() -> None:
    assert _requirement_status_text({"negated": True}) == (
        "Не требуется (пометка «не установлено»/«не требуется»)"
    )
    assert _requirement_status_text({"negated": False}) == "Требования не найдены"
