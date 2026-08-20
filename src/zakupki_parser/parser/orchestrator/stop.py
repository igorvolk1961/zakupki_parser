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
from zakupki_parser.storage.db import ClientProfile

logger = logging.getLogger(__name__)


class StopMixin:
    """Проверка условий прекращения обработки закупки."""

    # Задаётся в ``Orchestrator.__init__``.
    _now: datetime
    _cfg: AppConfig
    # Активный клиентский профиль (многоклиентный скоринг); задаётся оркестратором.
    _client_profile: ClientProfile | None = None

    def _check_stop_conditions(
        self,
        record: dict[str, Any],
        *,
        keywords: list[str] | None = None,
        exclusion_words: list[str] | None = None,
    ) -> bool:
        """Проверяет набор флагов прекращения обработки закупки.

        Возвращает True, если закупку следует ПРОПУСТИТЬ (обработка прекращается).
        ``keywords`` — ключевые слова текущего поискового обхода (пусто при обходе
        по ОКПД2 без слов); используется флагом keyword_context_required.
        ``exclusion_words`` — слова-исключения активного клиентского профиля;
        используется флагом exclusion_words_present.
        """
        sc = self._cfg.service.stop_conditions
        if (
            sc.keyword_context_required
            and keywords
            and self._keyword_missing_from_description(record, keywords)
        ):
            return True
        if (
            sc.exclusion_words_present
            and exclusion_words
            and self._exclusion_word_present(record, exclusion_words)
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
        """True, если ``regex`` (из keyword_context_regexes) совпал с описанием.

        Паттерн применяется как есть (регистронезависимо). Без паттерна — False:
        слово без паттерна в обходе не проверяется вовсе (см. также
        ``_keyword_missing_from_description``).
        """
        if not regex:
            return False
        return re.search(regex, subject, re.IGNORECASE) is not None

    @staticmethod
    def _exclusion_word_present(record: dict[str, Any], exclusion_words: list[str]) -> bool:
        """True, если в описании встречается любое слово-исключение.

        Стем-матчинг с учётом русской морфологии: ``(?<!\\w)<основа>[а-яё]*``.
        Основа строится усечением окончания: слова длиннее 5 — без 2 последних
        букв («медицинский» → «медицинск» ловит «медицинский/ой/ая/ых»), слова
        длиной 3–5 — без одной буквы («ель» → «ел», «алмаз» → «алма»), короткие —
        как есть. Слово на границе (не внутри другого слова): «карамель» не
        сработает на «ель», «следов» — на «лед», «немедицинский» — на
        «медицинский». Регистронезависимо. Пустой subject не отсекается.
        """
        subject = record.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            return False
        found: list[str] = []
        for word in exclusion_words:
            if not word or not word.strip():
                continue
            if len(word) >= 6:
                stem = word[:-2]
            elif len(word) >= 3:
                stem = word[:-1]
            else:
                stem = word
            pattern = r"(?<!\w)" + re.escape(stem) + r"[а-яё]*"
            if re.search(pattern, subject, re.IGNORECASE) is not None:
                found.append(word)
        if found:
            logger.info(
                "Закупка %s пропущена: слова-исключения %s в описании: %.200r",
                record.get("number"),
                found,
                subject,
            )
            return True
        return False

    def _keyword_missing_from_description(
        self, record: dict[str, Any], keywords: list[str]
    ) -> bool:
        """True, если в описании нет ни одного проверяемого ключевого слова.

        Стоп-условие применяется ТОЛЬКО к словам с явным паттерном в
        ``keyword_context_regexes`` (приоритет — у активного клиентского профиля,
        fallback — глобальные stop_conditions): слово без паттерна не проверяется
        вовсе (закупка через него не отсекается). Если в обходе нет ни одного
        слова с паттерном — условие не применяется (False).

        Пустой subject при наличии проверяемых слов — критический дефект
        записи: закупка не передаётся дальше (в скоринг) и пропускается.
        """
        profile = self._client_profile
        regexes = (
            (profile.keyword_context_regexes if profile else None)
            or self._cfg.service.stop_conditions.keyword_context_regexes
            or {}
        )
        checked = [(kw, regexes[kw]) for kw in keywords if kw in regexes]
        if not checked:
            return False
        subject = record.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            logger.critical(
                "Закупка %s пропущена: пустой subject при keyword_context_required=true",
                record.get("number"),
            )
            return True
        for keyword, regex in checked:
            if self._keyword_in_context(subject, keyword, regex=regex):
                return False
        logger.info(
            "Закупка %s пропущена: ключевые слова %s не встречаются в описании "
            "как отдельное слово или перед дефисом: %.200r",
            record.get("number"),
            [kw for kw, _ in checked],
            subject,
        )
        return True
