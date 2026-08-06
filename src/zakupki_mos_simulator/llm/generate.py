"""Пайплайн генерации тестовой выборки закупок.

Основной путь — LLM (OpenAI-совместимый). Для автономной работы (тесты, демо без
доступа к LLM) доступен детерминированный генератор ``build_demo_dataset``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from zakupki_mos_simulator.data.format import format_dates
from zakupki_mos_simulator.data.models import (
    CATEGORIES,
    Customer,
    Dataset,
    FileMeta,
    Procurement,
)
from zakupki_mos_simulator.llm.client import LLMClient
from zakupki_mos_simulator.llm.prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

# Часовой пояс площадки — МСК (UTC+3).
MSK = timedelta(hours=3)


def read_competencies(path: str | Path) -> str:
    """Читает текст описания компетенций поставщика."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Файл компетенций не найден: {p}")
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Файл компетенций пуст: {p}")
    return text


def assign_metadata(
    rows: list[Procurement],
    *,
    start_id: int = 1,
    now: datetime | None = None,
) -> list[Procurement]:
    """Достраивает id, даты, заказчиков и файлы для сгенерированных LLM записей."""
    now = now or datetime.now().astimezone()
    customers: dict[str, int] = {}
    out: list[Procurement] = []
    for i, row in enumerate(rows):
        pid = start_id + i
        # Дата публикации в пределах «недели» (default_cutoff_days), дедлайн в будущем;
        # часть записей — с истёкшим сроком (для stop-условий демо).
        deadline: datetime = (
            now - timedelta(days=1) if i % 5 == 0 else now + timedelta(days=3 + (i % 9))
        )
        pub = now - timedelta(days=i % 6)
        date_str = format_dates(pub, deadline)

        cid = customers.get(row.customer)
        if cid is None:
            cid = start_id + 900_000 + len(customers)
            customers[row.customer] = cid

        files = [
            FileMeta(
                name=f"Техническое задание {pid}.docx",
                url=f"/api/FileStorage/Download?id={pid}",
            ),
            FileMeta(
                name=f"Проект договора {pid}.docx",
                url=f"/api/FileStorage/Download?id={pid + 100_000}",
            ),
        ]
        out.append(
            row.model_copy(
                update={
                    "id": pid,
                    "number": str(pid),
                    "customer_id": cid,
                    "publication_date": date_str,
                    "files": files,
                }
            )
        )
    return out


def build_customers(procurements: list[Procurement]) -> list[Customer]:
    """Сводит заказчиков из закупок в справочник (по наименованию)."""
    seen: dict[str, Customer] = {}
    for p in procurements:
        if p.customer not in seen:
            seen[p.customer] = Customer(
                customer_id=p.customer_id,
                name=p.customer,
                inn=None,
            )
    return list(seen.values())


def generate_with_llm(
    client: LLMClient,
    *,
    competencies: str,
    okpd2_sections: list[str],
    per_category: int,
) -> Dataset:
    """Генерирует сбалансированную выборку через LLM по категориям."""
    start_id = 1
    rows: list[Procurement] = []
    for category in CATEGORIES:
        prompt = build_user_prompt(competencies, okpd2_sections, category, per_category)
        logger.info("Генерация категории '%s' (%d шт)...", category, per_category)
        payload = asyncio.run(client.chat_json(SYSTEM_PROMPT, prompt))
        raw = payload.get("procurements", [])
        for item in raw:
            item["category"] = category
        rows.extend(Procurement.model_validate(item) for item in raw)
        logger.info("Категория '%s': получено %d закупок", category, len(raw))

    rows = assign_metadata(rows, start_id=start_id)
    return Dataset(
        competencies=competencies,
        okpd2_sections=okpd2_sections,
        procurements=rows,
        customers=build_customers(rows),
    )


def build_demo_dataset(
    *,
    competencies: str,
    okpd2_sections: list[str],
    per_category: int = 4,
) -> Dataset:
    """Детерминированная выборка-заглушка (без LLM) для тестов и офлайн-демо."""
    templates: dict[str, list[tuple[str, str]]] = {
        "perfect": [
            ("Разработка и внедрение программного обеспечения для автоматизации", "62.01"),
            ("Создание и сопровождение ИТ-инфраструктуры", "62.02"),
        ],
        "synonym": [
            ("Проектирование и настройка цифровых систем управления", "62.09"),
            ("Внедрение и поддержка программно-аппаратных комплексов", "62.02"),
        ],
        "close": [
            ("Поставка серверного оборудования и комплектующих", "26.20"),
            ("Оказание услуг по обучению пользователей работе с офисным ПО", "63.11"),
        ],
        "far": [
            ("Поставка канцелярских товаров и расходных материалов", "30.99"),
            ("Услуги по уборке помещений", "81.21"),
        ],
        "false_friend": [
            ("Консультации по выбору аппаратуры звукозаписи для студии", "27.43"),
            ("Поставка систем радиосвязи и сопутствующие консультации", "26.30"),
        ],
    }
    rows: list[Procurement] = []
    for category in CATEGORIES:
        pool = templates[category]
        for idx in range(per_category):
            subject, code = pool[idx % len(pool)]
            rows.append(
                Procurement(
                    id=0,
                    number="0",
                    customer_id=0,
                    publication_date="с 01.01.2000 до 10.01.2000 12:00 (МСК)",
                    subject=subject,
                    customer=f"Заказчик {category} {idx + 1}",
                    nmck=round(1_000_000.0 + idx * 500_000.0, 2),
                    law="44-ФЗ",
                    okpd2_name="ИТ-услуги",
                    okpd2_code=code,
                    category=category,
                    category_reason="детерминированный генератор (демо)",
                )
            )
    rows = assign_metadata(rows)
    return Dataset(
        competencies=competencies,
        okpd2_sections=okpd2_sections,
        procurements=rows,
        customers=build_customers(rows),
    )


def generate_dataset(
    *,
    competencies_path: str | Path,
    okpd2_sections: list[str],
    per_category: int,
    use_llm: bool = True,
    client: LLMClient | None = None,
) -> Dataset:
    """Точка входа генерации: LLM или детерминированный генератор."""
    competencies = read_competencies(competencies_path)
    if use_llm and client is not None:
        return generate_with_llm(
            client,
            competencies=competencies,
            okpd2_sections=okpd2_sections,
            per_category=per_category,
        )
    return build_demo_dataset(
        competencies=competencies,
        okpd2_sections=okpd2_sections,
        per_category=per_category,
    )
