#!/usr/bin/env python3
"""Наполняет БД демо-данными для MVP web-приложения.

Запуск:
    uv run scripts/demo_seed.py [--configs configs]

Вставляет несколько примеров закупок (тематика ИТ-услуг) с заказчиками и ИНН
через обычный репозиторий (upsert), чтобы web-демо не было пустым. Повторный
запуск безопасен — записи дедуплицируются по (number, source_platform).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from zakupki_parser.config.loader import load_config
from zakupki_parser.storage.db import Database
from zakupki_parser.storage.repository import ProcurementRepository

MSK = timezone(timedelta(hours=3))
now = datetime.now(MSK)

# (number, platform, customer, inn, subject, law, nmck, okpd2, days_to_deadline)
DEMO = [
    (
        "2026-IT-AUDIT-001",
        "zakupki_mos",
        "ГБУ «Автомобильные дороги»",
        None,
        "Обследование ИТ-ландшафта и уровня автоматизации",
        "223-ФЗ",
        2_500_000,
        "62.02",
        21,
    ),
    (
        "2026-IT-AI-002",
        "zakupki_mos",
        "ГБУ «Автомобильные дороги»",
        None,
        "Внедрение ИИ-ассистента для обработки обращений",
        "223-ФЗ",
        4_800_000,
        "62.01",
        35,
    ),
    (
        "0138100003126000026",
        "zakupki_gov",
        "ФКУ «Главный центр специальной связи»",
        "7712345678",
        "Услуги в области информационных технологий (разработка ПО)",
        "44-ФЗ",
        12_600_000,
        "62.09",
        14,
    ),
    (
        "0138300006126000098",
        "zakupki_gov",
        "Управление социальной защиты населения",
        "7722001234",
        "Аудит применимости ИИ и дорожная карта автоматизации",
        "44-ФЗ",
        980_000,
        "63.11",
        8,
    ),
    (
        "0338200002226000330",
        "zakupki_gov",
        "ООО «Технопарк цифровых решений»",
        "3903007130",
        "Поддержка и развитие АСУ ТП (223-ФЗ)",
        "223-ФЗ",
        7_250_000,
        "62.01",
        30,
    ),
    (
        "2026-IT-ROAD-006",
        "zakupki_mos",
        "МБУ «Центр информатизации»",
        None,
        "Разработка финмодели и ТЭО автоматизации",
        "44-ФЗ",
        1_450_000,
        "63.11",
        17,
    ),
    (
        "0138100004726000162",
        "zakupki_gov",
        "Департамент городского имущества",
        "7703456789",
        "Услуги по разработке ПО для ИИ-автоматизации",
        "44-ФЗ",
        9_800_000,
        "62.02",
        25,
    ),
]


def _record(d: tuple[Any, ...]) -> dict[str, Any]:
    number, platform, customer, inn, subject, law, nmck, okpd2, days = d
    return {
        "number": number,
        "source_platform": platform,
        "customer": customer,
        "inn": inn,
        "subject": subject,
        "law": law,
        "nmck": nmck,
        "okpd2_codes": okpd2,
        "publication_date": now - timedelta(days=days),
        "update_date": now - timedelta(days=min(days, 1)),
        "deadline": now + timedelta(days=days),
        # Дефолтная эвристика Fit × P(win) × Margin (P(win)=1): иллюстративно.
        "score": round(0.7 * nmck, 2),
        "score_method": "default",
    }


async def main(configs_dir: str) -> int:
    cfg = load_config(configs_dir)
    if not cfg.service.db.enabled:
        print("БД отключена в конфиге — пропускаю.", file=sys.stderr)
        return 1
    db = Database(cfg.service.db)
    await db.connect()
    try:
        repo = ProcurementRepository(db)
        for d in DEMO:
            saved = await repo.upsert(_record(d))
            status = "inserted" if saved else "exists"
            print(f"  [{status}] {d[1]:12} {d[0]:22} {d[3] or '-':12} {d[4][:44]}")
        print(
            f"\nГотово: {len(DEMO)} записей. Запустите 'zakupki-parser serve' и откройте http://localhost:8000/"
        )
    finally:
        await db.dispose()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--configs", default="configs", help="каталог с YAML-конфигами (по умолчанию configs)"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.configs)))
