"""Промпты для генерации тестовой выборки закупок по 5 категориям."""

from __future__ import annotations

from zakupki_mos_simulator.data.models import CATEGORIES, CATEGORY_LABELS, Category

SYSTEM_PROMPT = (
    "Ты — эксперт по государственным и коммерческим закупкам (44-ФЗ, 223-ФЗ, "
    "закупки по потребностям на Портале поставщиков Москвы). Ты формируешь "
    "качественную тестовую выборку закупок для проверки точности сервиса скоринга "
    "закупок относительно компетенций поставщика.\n"
    "Отвечай ТОЛЬКО валидным JSON без пояснений и markdown-разметки."
)


def okpd2_sections_text(okpd2_sections: list[str]) -> str:
    """Читает названия разделов ОКПД2 из configs/codes/mos_okpd2_tree.json (path_to_name)."""
    import json
    from pathlib import Path

    from zakupki_mos_simulator.settings import Settings

    tree_path = Settings().default_okpd2_tree
    if not Path(tree_path).exists():
        return "\n".join(okpd2_sections)
    with Path(tree_path).open("r", encoding="utf-8") as fh:
        tree = json.load(fh)
    code_to_path = tree.get("code_to_path", {})
    path_to_name = tree.get("path_to_name", {})
    lines: list[str] = []
    for code in okpd2_sections:
        path = code_to_path.get(code)
        name = path_to_name.get(path, "") if path else ""
        lines.append(f"- {code}: {name or code}")
    return "\n".join(lines)


def category_instructions() -> str:
    """Требования по каждой категории с примерами-различиями."""
    blocks = [f"- **{cat}** — {CATEGORY_LABELS[cat]}." for cat in CATEGORIES]
    blocks.append(
        "Пример различия «perfect» и «false_friend»: в компетенции может быть "
        "«консультации по выбору аппаратуры связи», а в закупке — «консультации по "
        "выбору аппаратуры звукозаписи»: большинство слов совпадает, но смысл "
        "абсолютно не совпадает."
    )
    blocks.append(
        "Важно: категории должны быть в выборке примерно равными долями; не дублируй "
        "закупки; формулируй предмет (subject) реалистично и достаточно подробно, "
        "как в реальных закупках."
    )
    return "\n".join(blocks)


def build_user_prompt(
    competencies: str,
    okpd2_sections: list[str],
    category: Category,
    count: int,
) -> str:
    """Формирует user-промпт для генерации ``count`` закупок категории ``category``."""
    okpd2_text = okpd2_sections_text(okpd2_sections)
    return f"""Сгенерируй тестовую выборку закупок для проверки скоринга.

## Компетенции поставщика
{competencies}

## Разделы классификатора ОКПД2 (тематика ИТ-услуг, используй как основу предмета)
{okpd2_text}

## Требуемая категория
Категория: **{category}** — {CATEGORY_LABELS[category]}.

{category_instructions()}

## Требование к JSON
Верни объект вида:
{{
  "procurements": [
    {{
      "subject": "текст предмета закупки (подробно, как в реальной закупке)",
      "purchase_type": "Закупка по потребностям",
      "status": "Прием предложений",
      "customer": "Полное наименование организации-заказчика",
      "nmck": 123456.78,
      "region": "Москва",
      "law": "44-ФЗ" | "223-ФЗ",
      "okpd2_name": "наименование раздела/класса ОКПД2",
      "okpd2_code": "62.02" | "63.11" | ...,
      "category_reason": "краткое обоснование, почему закупка относится к этой категории"
    }}
  ]
}}

Сгенерируй ровно {count} закупок категории «{category}». Поля subject обязательно
детальные: они используются для семантической проверки скоринга. Не повторяй
субъекты, имена заказчиков и стоимости.
"""
