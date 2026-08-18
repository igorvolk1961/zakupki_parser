"""Условия прекращения обработки закупки (stop-условия).

Миксин, используемый классом ``Orchestrator``. Набор флагов задаётся в
``config_service.yaml -> stop_conditions``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from zakupki_parser.config.models import AppConfig

logger = logging.getLogger(__name__)

# Контекст, в котором ключевое слово должно встречаться в описании закупки, чтобы
# считаться осмысленным совпадением (отдельное слово или перед дефисом):
# - (?<![\w]) — слева не буква/цифра/подчёркивание (для str-паттернов \w покрывает
#   кириллицу, поэтому 'ии' в 'инвестиции' не совпадёт);
# - {kw} — регэксп-эскейп ключевого слова (подставляется в рантайме);
# - (?=[...]|$) — справа пробел, знак препинания, закрывающая скобка/кавычка,
#   дефис или конец строки (примеры: " ИИ ", " ИИ. ", " ИИ) ", " (ИИ ", " ИИ-", " (ИИ- ").
_KEYWORD_WORD_CONTEXT = r"(?<![\w]){kw}(?=[\s.,:;!?»)'\"\]\-}]|$)"


class StopMixin:
    """Проверка условий прекращения обработки закупки."""

    # Задаётся в ``Orchestrator.__init__``.
    _now: datetime
    _cfg: AppConfig

    def _check_stop_conditions(
        self, record: dict[str, Any], *, keywords: list[str] | None = None
    ) -> bool:
        """Проверяет набор флагов прекращения обработки закупки.

        Возвращает True, если закупку следует ПРОПУСТИТЬ (обработка прекращается).
        ``keywords`` — ключевые слова текущего поискового обхода (пусто при обходе
        по ОКПД2 без слов); используется флагом keyword_context_required.
        """
        sc = self._cfg.service.stop_conditions
        if (
            sc.keyword_context_required
            and keywords
            and self._keyword_missing_from_description(record, keywords)
        ):
            return True
        if sc.deadline_not_expired:
            deadline = record.get("deadline")
            if not isinstance(deadline, datetime):
                return False
            if deadline < self._now:
                logger.info(
                    "Закупка %s пропущена: срок приёма истёк (%s)",
                    record.get("number"),
                    deadline,
                )
                return True
            if sc.min_deadline_days is not None:
                days_left = (deadline - self._now).total_seconds() / 86400
                if days_left < sc.min_deadline_days:
                    logger.info(
                        "Закупка %s пропущена: до срока подачи %.1f дн. < %d",
                        record.get("number"),
                        days_left,
                        sc.min_deadline_days,
                    )
                    return True
        return False

    @staticmethod
    def _keyword_in_context(subject: str, keyword: str, regex: str | None = None) -> bool:
        """True, если ключевое слово встречается в описании закупки.

        ``regex`` — явный паттерн из ``stop_conditions.keyword_context_regexes``
        (например 'автоматизаци\\w*' для словоформ 'автоматизации'); применяется
        как есть. Без него — отдельное слово или перед дефисом.
        """
        if regex:
            return re.search(regex, subject, re.IGNORECASE) is not None
        pattern = _KEYWORD_WORD_CONTEXT.replace("{kw}", re.escape(keyword))
        return re.search(pattern, subject, re.IGNORECASE) is not None

    def _keyword_missing_from_description(
        self, record: dict[str, Any], keywords: list[str]
    ) -> bool:
        """True, если в описании закупки нет ни одного ключевого слова в контексте.

        Пустой subject при включённом флаге — критический дефект записи: закупка
        не передаётся дальше (в скоринг) и пропускается.
        """
        subject = record.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            logger.critical(
                "Закупка %s пропущена: пустой subject при keyword_context_required=true",
                record.get("number"),
            )
            return True
        regexes = self._cfg.service.stop_conditions.keyword_context_regexes or {}
        for keyword in keywords:
            if self._keyword_in_context(subject, keyword, regex=regexes.get(keyword)):
                return False
        logger.info(
            "Закупка %s пропущена: ключевые слова %s не встречаются в описании "
            "как отдельное слово или перед дефисом: %.200r",
            record.get("number"),
            keywords,
            subject,
        )
        return True
