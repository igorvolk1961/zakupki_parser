"""Параллельная проверка DSL-движка (``parser/filtering.py``, Python-regex) и
tsquery-транслятора (``parser/filtering_tsquery.py``) на одних и тех же кейсах.

Требует PostgreSQL (``ZAKUPKI_TEST_DSN``) — сверяет ``keywords_match`` с реальным
``to_tsvector('simple', text) @@ to_tsquery('simple', tsquery)``. Расхождение здесь
означает, что закупка вне проиндексированного диапазона ОКПД2 (live-путь, DSL) и
закупка внутри диапазона (индексный путь, tsquery) дадут РАЗНЫЙ результат матчинга
по одинаковым ключевым словам — риск, явно отмеченный в плане индексации.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from zakupki_parser.parser.filtering import keywords_match
from zakupki_parser.parser.filtering_tsquery import TS_CONFIG, compile_keyword_to_tsquery

TEST_DSN = os.environ.get("ZAKUPKI_TEST_DSN", "")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="ZAKUPKI_TEST_DSN не задан")


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(TEST_DSN)
    yield eng
    await eng.dispose()


async def _tsquery_matches(engine: AsyncEngine, subject: str, tsquery_text: str) -> bool:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(f"SELECT to_tsvector('{TS_CONFIG}', :subject) @@ to_tsquery('{TS_CONFIG}', :q)"),
            {"subject": subject, "q": tsquery_text},
        )
        return bool(result.scalar())


PARITY_CASES: list[tuple[str, str, bool]] = [
    # (ключевое слово DSL, текст, ожидаемое совпадение — эталон Python-движка)
    ("ИИ", "Разработка ИИ-ассистента", True),
    ("ИИ", "Ремонт помещения", False),
    ("разработ*", "Разработка программного обеспечения", True),
    ("разработ*", "в разработке систем", True),
    ("разработ*", "Поставка готового ПО", False),
    ("внедрен* информацион* систем*", "Внедрение информационных систем на предприятии", True),
    ("внедрен* информацион* систем*", "Внедрение CRM", False),
    ("(автоматизир* систем* учет*)~2", "Автоматизированная система бухгалтерского учета", True),
    (
        "(автоматизир* систем* учет*)~2",
        "Учет закупок в старой автоматизированной системе",
        False,
    ),
    ("(систем* учет*)~0", "система учета", True),
    ("(систем* учет*)~0", "система коммерческого учета", False),
    ("(систем* учет*)~1", "система учета", True),
    ("(систем* учет*)~1", "система коммерческого учета", True),
    ("(систем* учет*)~1", "система автоматизированного коммерческого учета", False),
    ("(систем* учет*)~2", "система автоматизированного коммерческого учета", True),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("keyword", "subject", "expected"), PARITY_CASES)
async def test_dsl_and_tsquery_agree(
    engine: AsyncEngine, keyword: str, subject: str, expected: bool
) -> None:
    python_result = keywords_match({"subject": subject}, [keyword])
    assert python_result is expected, "эталон теста разошёлся с Python-движком"

    tsquery_text = compile_keyword_to_tsquery(keyword)
    assert tsquery_text is not None
    pg_result = await _tsquery_matches(engine, subject, tsquery_text)
    assert pg_result is expected, (
        f"расхождение DSL vs tsquery: keyword={keyword!r} subject={subject!r} "
        f"tsquery={tsquery_text!r} python={python_result} postgres={pg_result}"
    )
