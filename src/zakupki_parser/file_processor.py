"""Дополнительная обработка скачанных файлов для извлечения переменных.

Сейчас — заглушка: интерфейс и базовая реализация, возвращающая пустой словарь.
Полноценное извлечение (PDF/DOCX) реализуется позже (см. TODO).
"""

from __future__ import annotations

import logging
from typing import Any

from zakupki_parser.storage.object_store import FileRef

logger = logging.getLogger(__name__)


class FileProcessor:
    """Обрабатывает скачанные файлы и возвращает извлечённые переменные."""

    async def process(self, files: list[FileRef], number: str) -> dict[str, Any]:
        """Заглушка. Возвращает пустой dict.

        Args:
            files: ссылки на скачанные файлы заявки (в хранилище).
            number: номер заявки.

        Returns:
            Словарь извлечённых переменных (сейчас пустой).
        """
        logger.info(
            "file_processor: заглушка, обработано файлов=%d по заявке %s",
            len(files),
            number,
        )
        return {}
