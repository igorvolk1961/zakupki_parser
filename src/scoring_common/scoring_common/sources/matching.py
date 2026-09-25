"""Поиск значений и уточнений «между значениями» — в тексте ТЗ и в тексте сайта.

Уточнение (вид работ «утилизация» для кода ФККО) ищется в **окне** значения:
от найденного значения до следующего значения той же формы. Форма выводится
из самого значения (``conditions.value_shape``, для границы — и у кода без
разделителей). Конец окна — ближайшее из:

1. начало следующего значения той же формы;
2. граница страницы сайта / документа ТЗ (окно не переходит на следующую:
   иначе последнее значение страницы захватило бы шапку следующей);
3. ``1.5 × медиана`` расстояний между соседними значениями той же страницы
   (на странице меньше 3 значений — медиана по всему тексту);
4. ``max_window`` символов.

Значение без формы (текст) или единственное во всём тексте — окно
±``window`` символов.

Одна и та же процедура работает и в ТЗ (какие виды работ требуются для кода),
и на сайте (есть ли у кода эти виды работ).
"""

from __future__ import annotations

import logging
import statistics
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from scoring_common.conditions import classify_value, find_value, normalize_text, value_shape
from scoring_common.sources.store import get_text, split_pages, text_key

logger = logging.getLogger(__name__)

DEFAULT_WINDOW = 300
DEFAULT_MAX_WINDOW = 2000
_MEDIAN_FACTOR = 1.5


class TextWindows:
    """Текст, разбитый на страницы (сайт) или документы (ТЗ), с поиском окон."""

    def __init__(self, pages: Iterable[str]) -> None:
        self.pages = list(pages)
        self.norm = [normalize_text(p) for p in self.pages]
        # форма (pattern) -> (начала совпадений по страницам, медианы по страницам, общая)
        self._shape_cache: dict[str, tuple[list[list[int]], list[float | None], float | None]] = {}

    @classmethod
    def from_source_text(cls, text: str) -> TextWindows:
        """Полный текст сайта-источника (маркеры страниц ``store.join_pages``)."""
        return cls(page for _, page in split_pages(text))

    def occurrences(self, value: str, mode: str = "auto") -> list[tuple[int, int, int]]:
        """Все вхождения значения: ``[(страница, начало, конец)]``."""
        return [
            (i, s, e) for i, norm in enumerate(self.norm) for s, e in find_value(norm, value, mode)
        ]

    def _shape_index(self, value: str) -> tuple[list[list[int]], list[float | None], float | None]:
        shape = value_shape(value, allow_plain=True)
        assert shape is not None
        cached = self._shape_cache.get(shape.pattern)
        if cached is None:
            starts = [[m.start() for m in shape.finditer(norm)] for norm in self.norm]
            medians = [_median_gap(page_starts) for page_starts in starts]
            gaps = [
                b - a
                for page_starts in starts
                for a, b in zip(page_starts, page_starts[1:], strict=False)
            ]
            overall = float(statistics.median(gaps)) if gaps else None
            cached = (starts, medians, overall)
            self._shape_cache[shape.pattern] = cached
        return cached

    def window(
        self,
        page: int,
        start: int,
        end: int,
        value: str,
        mode: str = "auto",
        *,
        window: int = DEFAULT_WINDOW,
        max_window: int = DEFAULT_MAX_WINDOW,
    ) -> str:
        """Окно значения, найденного на странице ``page`` в ``[start, end)``."""
        text = self.pages[page]
        if classify_value(value, mode) != "code" or value_shape(value, allow_plain=True) is None:
            return text[max(0, start - window) : min(len(text), end + window)]
        starts, medians, overall = self._shape_index(value)
        page_starts = starts[page]
        median = medians[page] if len(page_starts) >= 3 else overall
        if median is None:
            return text[max(0, start - window) : min(len(text), end + window)]
        limit = min(len(text), start + int(_MEDIAN_FACTOR * median), start + max_window)
        following = next((s for s in page_starts if s >= end), None)
        stop = min(limit, following) if following is not None else limit
        return text[end:stop]


def _median_gap(starts: Sequence[int]) -> float | None:
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    return float(statistics.median(gaps)) if gaps else None


def qualifier_forms(window_text: str, qualifier: str, *, exact: bool = False) -> list[str]:
    """Словоформы уточнения в окне: по основе (слово из ТЗ) или, ``exact``, в
    точной форме (задана пользователем; ``*`` — любое продолжение)."""
    norm = normalize_text(window_text)
    return [window_text[s:e] for s, e in find_value(norm, qualifier, "text", exact=exact)]


# ---------------------------------------------------------------------- #
# Сайт-источник для проверки условия
# ---------------------------------------------------------------------- #

SourceState = Literal["ready", "pending", "failed"]


@dataclass
class SourceContext:
    """Текст сайта для проверки условия и насколько ему можно верить."""

    url_norm: str
    state: SourceState
    # Текст получен полным сбором (пагинация закончилась сама): отсутствие
    # значения на сайте доказано. Неполный — доказано только наличие.
    complete: bool = False
    windows: TextWindows | None = None
    # Идёт (пере)сбор — показывается пользователю вместо «не проверено».
    collecting: bool = False
    progress: dict[str, Any] = field(default_factory=dict)


_TEXT_CACHE: OrderedDict[tuple[str, str], TextWindows] = OrderedDict()
_TEXT_CACHE_SIZE = 4


def source_context(meta: Mapping[str, Any]) -> SourceContext:
    """Контекст сайта по сведениям API (``condition["source"]``).

    Текст читается из хранилища и кэшируется в процессе по (URL, время сбора):
    одни и те же сайты проверяются для множества закупок подряд.
    """
    url_norm = str(meta.get("url_norm") or "")
    collecting = bool(meta.get("active")) or meta.get("status") in ("pending", "running")
    fetched_at = meta.get("fetched_at")
    progress = dict(meta.get("progress") or {})
    if not url_norm or not fetched_at:
        state: SourceState = "pending" if collecting else "failed"
        return SourceContext(url_norm, state, collecting=collecting, progress=progress)
    key = (
        url_norm,
        str(fetched_at if not isinstance(fetched_at, datetime) else fetched_at.isoformat()),
    )
    windows = _TEXT_CACHE.get(key)
    if windows is None:
        text = get_text(text_key(url_norm))
        if text is None:
            return SourceContext(url_norm, "failed", collecting=collecting, progress=progress)
        windows = TextWindows.from_source_text(text)
        _TEXT_CACHE[key] = windows
        while len(_TEXT_CACHE) > _TEXT_CACHE_SIZE:
            _TEXT_CACHE.popitem(last=False)
    else:
        _TEXT_CACHE.move_to_end(key)
    return SourceContext(
        url_norm,
        "ready",
        complete=bool(meta.get("text_complete")),
        windows=windows,
        collecting=collecting,
        progress=progress,
    )


def source_contexts(field_defs: Iterable[Mapping[str, Any]]) -> dict[str, SourceContext]:
    """Контексты всех сайтов из условий полей (по ``condition["source"]``)."""
    out: dict[str, SourceContext] = {}
    for field_def in field_defs:
        condition = field_def.get("condition") or {}
        meta = condition.get("source") if condition.get("value_kind") == "url" else None
        if not isinstance(meta, Mapping):
            continue
        url_norm = str(meta.get("url_norm") or "")
        if url_norm and url_norm not in out:
            try:
                out[url_norm] = source_context(meta)
            except Exception:  # noqa: BLE001 — хранилище недоступно: условие «не проверено»
                logger.warning("Текст сайта %s недоступен", url_norm, exc_info=True)
                out[url_norm] = SourceContext(url_norm, "failed")
    return out


def clear_text_cache() -> None:
    _TEXT_CACHE.clear()


__all__ = [
    "DEFAULT_MAX_WINDOW",
    "DEFAULT_WINDOW",
    "SourceContext",
    "TextWindows",
    "clear_text_cache",
    "qualifier_forms",
    "source_context",
    "source_contexts",
]
