"""Оценка покрытия полей закупок конфигурацией площадки (статика) и по БД (динамика).

Статическое покрытие вычисляется из конфига (``static_field_coverage``) и не требует
БД. Динамическое — по сохранённым записям (``field_coverage_runtime``) и требует
подключения к БД (см. ``coverage`` в cli/commands.py).
"""

from __future__ import annotations

from typing import Any

from zakupki_parser.config.models.fields import (
    FieldTier,
    coverage_score,
    static_field_coverage,
)


def _static_lines(platform: Any) -> list[str]:
    """Строки статического покрытия одной площадки (без печати)."""
    cov = static_field_coverage(platform)
    lines = [
        f"Покрытие (MANDATORY+IMPORTANT): {coverage_score(cov):.0%}",
    ]
    missing = [c.label for c in cov if c.tier == FieldTier.MANDATORY and c.status == "missing"]
    if missing:
        lines.append(f"  ! незакрытые MANDATORY: {', '.join(missing)}")
    for c in cov:
        mark = "+" if c.status == "declared" else "-"
        src = f"  [{', '.join(c.sources)}]" if c.sources else ""
        lines.append(f"    {mark} {c.tier.value:<9} {c.label:<22} {c.key}{src}")
    return lines


def print_platform_static(platform: Any, label: str) -> None:
    """Печатает статическое покрытие площадки."""
    print(f"== {label} ==")
    for line in _static_lines(platform):
        print(line)


def render_static_lines(platform: Any) -> list[str]:
    """Строки статического покрытия (для check-config: без печати в таблице)."""
    return _static_lines(platform)


def render_runtime_row(platform_id: str, row: dict[str, Any] | None) -> list[str]:
    """Человекочитаемые строки динамического покрытия (или ``None`` — нет данных)."""
    if row is None:
        return [f"  {platform_id}: нет записей (или БД недоступна)"]
    fields = row["fields"]
    lines = [
        f"  {platform_id}: записей {row['total']}",
    ]
    ordered = sorted(fields.items(), key=lambda kv: (-kv[1]["fraction"], kv[0]))
    for key, st in ordered:
        lines.append(f"    {key:<18} {st['fraction']:>6.0%}  ({st['filled']}/{row['total']})")
    return lines
