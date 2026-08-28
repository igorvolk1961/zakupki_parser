#!/usr/bin/env python3
"""Проверка трассируемости «требование ↔ код ↔ тест» (requirements-registry.yaml).

Читает единый источник правды `docs/system_analysis/traceability/requirements-registry.yaml`
и сверяет его с фактическими модулями `src/**` и тестами `tests/**` и `src/*/tests/**`.

Ошибки (фатальны при `--check` и `--strict`):
  * неуникальный id требования;
  * недопустимый type/status;
  * путь в code/tests не существует, либо «path#symbol» не найден в AST;
  * требование `implemented`/`partial` без кода; FR/US без теста (если нет `covered_by`).

Предупреждения (фатальны только при `--strict`):
  * «сирота» — тест-файл, не упомянутый ни в одном требовании и не в exclusions.test_files.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "docs" / "system_analysis" / "traceability" / "requirements-registry.yaml"

VALID_TYPES = {"US", "FR", "BR", "NFR"}
VALID_STATUS = {"implemented", "partial", "planned", "out-of-scope"}

TEST_ROOTS = [
    REPO / "tests",
    *sorted((p / "tests") for p in (REPO / "src").iterdir() if (p / "tests").is_dir()),
]


def load_registry() -> dict[str, Any]:
    if not REGISTRY.exists():
        sys.exit(f"registry not found: {REGISTRY}")
    with REGISTRY.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("requirements"), list):
        sys.exit("registry: не найден блок requirements (list)")
    return data


def iter_test_files() -> list[str]:
    """Все тест-файлы (`tests/**` и `src/*/tests/**`) относительно REPO."""
    files: list[str] = []
    for root in TEST_ROOTS:
        for path in root.rglob("*.py"):
            if path.name in {"conftest.py", "__init__.py"}:
                continue
            if ".venv" in path.parts or ".mypy_cache" in path.parts:
                continue
            files.append(path.relative_to(REPO).as_posix())
    return sorted(files)


def iter_source_paths(req_code: list[str]) -> list[tuple[Path, str | None]]:
    """(путь, symbol) для записей code, распарсенных как «path» или «path#symbol»."""
    out: list[tuple[Path, str | None]] = []
    for entry in req_code:
        if "#" in entry:
            path, symbol = entry.rsplit("#", 1)
            out.append((REPO / path, symbol))
        else:
            out.append((REPO / entry, None))
    return out


def symbol_exists(path: Path, symbol: str) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return symbol in names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--check", action="store_true", help="фаталить ошибки валидации (по умолчанию вкл)"
    )
    ap.add_argument("--strict", action="store_true", help="фаталить также сироты")
    args = ap.parse_args()

    data = load_registry()
    reqs = data["requirements"]
    exclusions = data.get("exclusions", {}) or {}
    excl_tests = set(exclusions.get("test_files") or [])

    errors: list[str] = []
    warnings: list[str] = []

    seen_ids: set[str] = set()
    referenced_tests: set[str] = set()

    for req in reqs:
        rid = req.get("id")
        if rid in seen_ids:
            errors.append(f"{rid}: неуникальный id")
        seen_ids.add(rid)
        if req.get("type") not in VALID_TYPES:
            errors.append(f"{rid}: недопустимый type={req.get('type')!r}")
        if req.get("status") not in VALID_STATUS:
            errors.append(f"{rid}: недопустимый status={req.get('status')!r}")

        # code: существование и AST-symbol
        code_entries = req.get("code") or []
        for path, symbol in iter_source_paths(code_entries):
            if not path.exists():
                errors.append(f"{rid}: code-путь не существует: {path.relative_to(REPO)}")
            elif symbol is not None and not symbol_exists(path, symbol):
                errors.append(f"{rid}: символ не найден в {path.relative_to(REPO)}: {symbol}")

        # tests: существование + регистрация
        for t in req.get("tests") or []:
            referenced_tests.add(t)
            tpath = REPO / t
            if not tpath.exists():
                errors.append(f"{rid}: тест-файл не существует: {t}")

        # полнота
        if req.get("status") in {"implemented", "partial"} and not code_entries:
            errors.append(f"{rid}: status={req['status']} без code")
        if (
            req.get("status") in {"implemented", "partial"}
            and req.get("type") in {"US", "FR"}
            and not req.get("tests")
            and not req.get("covered_by")
        ):
            errors.append(f"{rid}: {req['type']} без тестов")

    # сироты: тест-файлы, не упомянутые нигде
    all_tests = set(iter_test_files())
    for t in sorted(all_tests - referenced_tests):
        if t in excl_tests:
            continue
        warnings.append(f"сирота: тест-файл не связан ни с одним требованием: {t}")

    for t in sorted(excl_tests - all_tests):
        warnings.append(f"exclusions.test_files ссылается на несуществующий файл: {t}")

    print(
        f"Требований: {len(reqs)}; тест-файлов: {len(all_tests)}; "
        f"ошибок: {len(errors)}; сирот: {len(warnings)}"
    )

    for e in sorted(set(errors)):
        print(f"  [ERROR] {e}")
    if warnings:
        for w in sorted(set(warnings)):
            print(f"  [WARN ] {w}")

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
