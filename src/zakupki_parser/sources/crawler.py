"""Обход сайта по всем страницам пагинации — без адаптеров под конкретный сайт.

Цикл обхода работает через ``PageDriver`` (браузер — ``playwright_driver``,
в тестах — подделка), поэтому логика «куда идти дальше и когда остановиться»
проверяется без браузера.

**Следующая страница** ищется драйвером в разметке (по порядку): ссылка
``rel=next``; кнопка с подписью «след./далее/next/›/»/Страница N+1»; число
N+1 в ряду номеров страниц; «показать ещё». Не нашлось — номер страницы в
параметре URL (``page=``, ``p=``, ``PAGEN_1=``…) на единицу больше; на первой
странице — бесконечная прокрутка.

**Конец пагинации** определяется без счётчика результатов, по отпечатку
основного содержимого страницы (без шапки/подвала/меню):

- ``no_next`` — нет кнопки «следующая» или она неактивна;
- ``repeat`` — отпечаток уже встречался (сайт показал последнюю страницу
  ещё раз или вернулся к первой);
- ``no_change`` — после перехода содержимое не изменилось.

Это ``complete`` — пагинация закончилась сама. Остальные причины остановки
(лимиты, отмена, сбой перехода) — ``incomplete``: собранное сохраняется, но
полнота не доказана.

В режимах «показать ещё» и прокрутки страница растёт — сохраняется только
прирост текста.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

NextMode = Literal["rel_next", "label", "number", "load_more", "url_param", "scroll"]
StopReason = Literal[
    "no_next",
    "repeat",
    "no_change",
    "page_limit",
    "size_limit",
    "time_limit",
    "cancelled",
    "nav_failed",
]
COMPLETE_REASONS: frozenset[str] = frozenset({"no_next", "repeat", "no_change"})
# Режимы, в которых новая порция дописывается к той же странице.
_ACCUMULATING: frozenset[str] = frozenset({"load_more", "scroll"})

_URL_PAGE_PARAM = re.compile(
    r"([?&](?:page|p|pg|pagen_\d+|pageindex|pagenum)=)(\d+)", re.IGNORECASE
)


@dataclass
class NextStep:
    """Как перейти к следующей странице."""

    mode: NextMode
    href: str | None = None
    disabled: bool = False


class PageDriver(Protocol):
    """Действия со страницей сайта, нужные обходу."""

    async def open(self, url: str) -> None:
        """Открыть URL и дождаться, пока содержимое перестанет меняться."""

    async def text(self) -> str:
        """Видимый текст страницы."""

    async def fingerprint(self) -> str:
        """Отпечаток основного содержимого (без шапки/подвала/меню)."""

    async def current_url(self) -> str: ...

    async def find_next(self, page_no: int) -> NextStep | None:
        """Кнопка/ссылка на страницу ``page_no + 1`` в разметке (режимы 1–4)."""

    async def click_next(self) -> None:
        """Нажать элемент, найденный последним ``find_next``."""

    async def scroll_to_bottom(self) -> None: ...

    async def wait_change(self, old_fingerprint: str, timeout_s: float) -> str | None:
        """Дождаться изменения содержимого; ``None`` — не изменилось за время."""


@dataclass
class CrawlLimits:
    max_pages: int = 500
    max_chars: int = 30_000_000
    page_timeout_s: float = 20.0
    total_timeout_s: float = 1200.0
    delay_ms: tuple[int, int] = (300, 800)


@dataclass
class CrawlProgress:
    """Ход обхода — после каждой страницы."""

    pages: int
    chars: int
    current_url: str
    mode: str | None
    elapsed_s: float


@dataclass
class CrawlOutcome:
    pages: list[tuple[str, str]] = field(default_factory=list)
    stop_reason: StopReason = "no_next"
    error: str | None = None
    mode: str | None = None

    @property
    def complete(self) -> bool:
        return self.stop_reason in COMPLETE_REASONS

    @property
    def chars(self) -> int:
        return sum(len(text) for _, text in self.pages)


def next_url_by_param(url: str) -> str | None:
    """URL следующей страницы по номеру в параметре (``page=3`` -> ``page=4``)."""
    match = _URL_PAGE_PARAM.search(url)
    if match is None:
        return None
    number = int(match.group(2)) + 1
    return url[: match.start(2)] + str(number) + url[match.end(2) :]


def increment(previous: str, current: str) -> str:
    """Что добавилось на странице: строки ``current`` без общих с ``previous``
    строк в начале и в конце. Новые записи вставляются перед кнопкой
    «Показать ещё»/подвалом, поэтому новый текст не продолжает старый."""
    old, new = previous.split("\n"), current.split("\n")
    head = 0
    while head < min(len(old), len(new)) and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < min(len(old), len(new)) - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    return "\n".join(new[head : len(new) - tail]).strip("\n")


async def crawl(
    driver: PageDriver,
    url: str,
    limits: CrawlLimits,
    *,
    on_page: Callable[[CrawlProgress], Awaitable[None]] | None = None,
    cancelled: Callable[[], Awaitable[bool]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> CrawlOutcome:
    """Собирает текст сайта со всех страниц пагинации, начиная с ``url``.

    Ошибка открытия ПЕРВОЙ страницы пробрасывается (собирать нечего); сбой
    перехода на следующую — остановка ``nav_failed`` с тем, что уже собрано.
    """
    started = clock()
    outcome = CrawlOutcome()
    await driver.open(url)
    full_text = await driver.text()
    outcome.pages.append((await driver.current_url(), full_text))
    seen = {await driver.fingerprint()}
    page_no = 1

    async def report() -> None:
        if on_page is not None:
            await on_page(
                CrawlProgress(
                    pages=len(outcome.pages),
                    chars=outcome.chars,
                    current_url=outcome.pages[-1][0],
                    mode=outcome.mode,
                    elapsed_s=clock() - started,
                )
            )

    await report()
    while True:
        if cancelled is not None and await cancelled():
            outcome.stop_reason = "cancelled"
            return outcome

        step = await driver.find_next(page_no)
        if step is None and (href := next_url_by_param(await driver.current_url())):
            step = NextStep(mode="url_param", href=href)
        if step is None and (page_no == 1 or outcome.mode == "scroll"):
            # Бесконечная прокрутка: пробуем на первой странице, дальше — пока растёт.
            step = NextStep(mode="scroll")
        if step is None or step.disabled:
            outcome.stop_reason = "no_next"
            return outcome
        # Лимиты — только когда следующая страница есть: сайт ровно в max_pages
        # страниц собран полностью.
        if len(outcome.pages) >= limits.max_pages:
            outcome.stop_reason = "page_limit"
            return outcome
        if outcome.chars >= limits.max_chars:
            outcome.stop_reason = "size_limit"
            return outcome
        if clock() - started >= limits.total_timeout_s:
            outcome.stop_reason = "time_limit"
            return outcome

        old_fp = await driver.fingerprint()
        try:
            if step.href is not None:
                await driver.open(step.href)
                new_fp: str | None = await driver.fingerprint()
                if new_fp == old_fp:
                    new_fp = None
            else:
                if step.mode == "scroll":
                    await driver.scroll_to_bottom()
                else:
                    await driver.click_next()
                new_fp = await driver.wait_change(old_fp, limits.page_timeout_s)
        except Exception as exc:  # noqa: BLE001 — сбой перехода не теряет собранное
            outcome.stop_reason = "nav_failed"
            outcome.error = str(exc)[:500]
            return outcome

        if new_fp is None:
            # Прокрутка ничего не догрузила — страниц больше нет.
            outcome.stop_reason = "no_next" if step.mode == "scroll" else "no_change"
            return outcome
        if new_fp in seen:
            outcome.stop_reason = "repeat"
            return outcome
        seen.add(new_fp)
        outcome.mode = step.mode

        text = await driver.text()
        if step.mode in _ACCUMULATING:
            text, full_text = increment(full_text, text), text
        else:
            full_text = text
        outcome.pages.append((await driver.current_url(), text))
        page_no += 1
        await report()
        low, high = limits.delay_ms
        await sleep(random.uniform(low, high) / 1000.0)
