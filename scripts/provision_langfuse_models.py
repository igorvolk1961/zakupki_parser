#!/usr/bin/env python3
"""Провижн тарифов DeepSeek/Giga в Langfuse через Models API (POST /api/public/models).

Нужен для LLM-пути СКОРИНГА (Langfuse LangChain-callback): там стоимость
инферится из определения модели, а мы хотим задать цены из нашего
``scoring_common.costing`` (app-side), а не полагаться на каталог Langfuse.

App-side инъекция ``usage_details``/``cost_details`` уже покрывает анализ-LLM и
эмбеддинги (оба owned-пути); этот скрипт закрывает единственный оставшийся путь —
LLM-генерации LangChain (fit/judge/refine).

Кратко:
  - Берёт ``LANGFUSE_PUBLIC_KEY``/``LANGFUSE_SECRET_KEY``/``LANGFUSE_HOST`` из env.
  - Создаёт flat-модели (unit=TOKENS, цены за токен) для deepseek-v4-flash/pro и
    EmbeddingsGigaR. Цены — из ``scoring_common.costing`` (пиковые за 1M токенов;
    env-переопределения ``COSTING_*`` применяются единообразно).
  - `--offpeak` задаёт СТАТИЧНУЮ непиковую цену (x0.5) для ВСЕХ вызовов LangChain.
    Ограничение: Langfuse-модель не различает время суток — точный пик/непик для
    скоринг-LLM возможен только инъекцией ``cost_details`` app-side. Используйте
    `--offpeak`, только если прогоны преимущественно вне пиковых часов.
  - Идемпотентность: при HTTP 409 модель пропускается; ``--force`` удаляет
    существующую модель по имени и пересоздаёт её заново.

Запуск:
  export LANGFUSE_PUBLIC_KEY=pk-lf-... LANGFUSE_SECRET_KEY=sk-lf-... LANGFUSE_HOST=...
  uv run python scripts/provision_langfuse_models.py                # пиковые цены
  uv run python scripts/provision_langfuse_models.py --offpeak --force
  uv run python scripts/provision_langfuse_models.py --dry-run      # только показать
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

from scoring_common.costing import (
    DEFAULT_RUB_TO_USD,
    GIGA_EMBEDDING_RUB_PER_1M,
    deepseek_peak_rates,
)

OFFPEAK_FACTOR = 0.5
_PER_TOKEN = 1_000_000.0


def _model_payload(
    match_pattern: str,
    model_name: str,
    input_per_token: float,
    output_per_token: float,
    start_date: str,
) -> dict[str, object]:
    return {
        "modelName": model_name,
        "matchPattern": match_pattern,
        "inputPrice": input_per_token,
        "outputPrice": output_per_token,
        "unit": "TOKENS",
        "startDate": start_date,
    }


def _api(
    host: str, creds: str, method: str, path: str, body: bytes | None = None
) -> tuple[int, str]:
    """HTTP-вызов Langfuse public API; возвращает (status, body). Сетевой сбой → (-1, msg)."""
    req = urllib.request.Request(
        f"{host}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")[:400]
    except urllib.error.URLError as exc:
        return -1, str(exc)


def _delete_by_model_name(host: str, creds: str, model_name: str) -> None:
    """Удалить существующие определения модели с данным именем (для --force)."""
    status, body = _api(host, creds, "GET", "/api/public/models")
    if status != 200:
        print(f"  [force] не удалось прочитать модели (HTTP {status}), пропуск замены")
        return
    try:
        items = json.loads(body).get("data") or []
    except json.JSONDecodeError:
        return
    for item in items:
        if item.get("modelName") != model_name or not item.get("id"):
            continue
        d_status, _ = _api(host, creds, "DELETE", f"/api/public/models/{item['id']}")
        print(f"  [force] удалил существующую модель {model_name} (HTTP {d_status})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="только показать payload")
    ap.add_argument("--offpeak", action="store_true", help="непиковые цены (x0.5)")
    ap.add_argument("--force", action="store_true", help="удалять существующую модель при 409")
    args = ap.parse_args()

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
    if not (public_key and secret_key):
        print("Ошибка: задайте LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY", file=sys.stderr)
        return 2

    factor = OFFPEAK_FACTOR if args.offpeak else 1.0
    start_date = "2026-01-01T00:00:00Z"

    models: list[dict[str, object]] = []
    for name in ("deepseek-v4-flash", "deepseek-v4-pro"):
        rates = deepseek_peak_rates(name)
        if rates is None:
            continue
        _cache_hit, cache_miss, output = rates
        models.append(
            _model_payload(
                rf"(?i)^{name}$",
                name,
                cache_miss / _PER_TOKEN * factor,
                output / _PER_TOKEN * factor,
                start_date,
            )
        )
    models.append(
        _model_payload(
            r"(?i)^EmbeddingsGigaR$",
            "EmbeddingsGigaR",
            GIGA_EMBEDDING_RUB_PER_1M / DEFAULT_RUB_TO_USD / _PER_TOKEN,
            0.0,
            start_date,
        )
    )

    creds = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    for model in models:
        if args.dry_run:
            print(f"[dry-run] POST {host}/api/public/models\n  {model}")
            continue
        attempts = 0
        while attempts < 3:
            attempts += 1
            status, resp_body = _api(
                host, creds, "POST", "/api/public/models", json.dumps(model).encode()
            )
            if status in (200, 201):
                print(f"OK {model['modelName']}: HTTP {status}")
                break
            if status == 409 and args.force:
                print(f"{model['modelName']}: HTTP 409 с --force — заменяю модель")
                _delete_by_model_name(host, creds, str(model["modelName"]))
                continue
            if status == 409:
                print(
                    f"{model['modelName']}: HTTP 409 (модель уже существует) "
                    "— используйте --force для замены"
                )
            elif status < 0:
                print(f"{model['modelName']}: сеть: {resp_body}", file=sys.stderr)
                return 1
            else:
                print(f"{model['modelName']}: HTTP {status} {resp_body}")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
