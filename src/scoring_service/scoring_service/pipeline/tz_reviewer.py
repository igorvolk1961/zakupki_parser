"""Оркестрация шага уточнения скора по тексту ТЗ как дочернего span'а LangFuse.

Шаг ``tz_review`` выполняется внутри того же корневого трейса ``scoring_job``
(как дочерний span через ``parent_config``), чтобы поиск/извлечение текста ТЗ
и решение «найден/не найден» были видны в одном трейсе с fit/judge.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig, RunnableLambda
from pydantic import BaseModel, Field

from scoring_service.pipeline.tz_review import (
    clean_text,
    extract_text,
    find_tz_reference,
)
from scoring_service.settings import Settings


class TzReviewOutcome(BaseModel):
    """Результат шага уточнения по ТЗ."""

    found: bool
    file_name: str | None = None
    description: str | None = None  # очищенный текст ТЗ (None = файл не найден/не извлечён)
    reason: str = Field(description="Пояснение исхода (пишется в трейс)")


class TzReviewer:
    """Запускает поиск и извлечение текста ТЗ внутри span'а ``tz_review``."""

    def __init__(self, settings: Settings, callbacks: list[Any] | None = None) -> None:
        self._settings = settings
        self._callbacks = callbacks

    def invoke(
        self,
        record: dict[str, Any],
        parent_config: RunnableConfig,
        trace_meta: dict[str, Any],
        session_id: str | None,
    ) -> TzReviewOutcome:
        runner = RunnableLambda(self._impl, name="tz_review")
        return runner.invoke(
            record,
            config={
                "callbacks": self._callbacks or None,
                "run_name": "tz_review",
                "metadata": {
                    **trace_meta,
                    **({"langfuse_session_id": session_id} if session_id is not None else {}),
                },
            },
        )

    def _impl(self, record: dict[str, Any]) -> TzReviewOutcome:
        timeout = self._settings.tz_download_timeout
        verify_ssl = self._settings.tz_verify_ssl
        ref = find_tz_reference(record, timeout=timeout, verify_ssl=verify_ssl)
        if ref is None:
            return TzReviewOutcome(
                found=False,
                reason="Файл ТЗ не найден ни среди файлов карточки, ни внутри архивов",
            )
        text = extract_text(ref, timeout=timeout, verify_ssl=verify_ssl)
        if text is None:
            return TzReviewOutcome(
                found=False,
                file_name=ref.name,
                reason=f"Не удалось извлечь текст из файла ТЗ: {ref.name}",
            )
        cleaned = clean_text(text)
        if not cleaned:
            return TzReviewOutcome(
                found=False,
                file_name=ref.name,
                reason=f"Пустой текст ТЗ (не удалось извлечь содержимое): {ref.name}",
            )
        return TzReviewOutcome(
            found=True,
            file_name=ref.name,
            description=cleaned,
            reason=f"Текст ТЗ получен и очищен ({len(cleaned)} символов)",
        )
