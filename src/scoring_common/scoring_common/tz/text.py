"""Очистка извлечённого текста ТЗ от мусора."""

from __future__ import annotations

import re


def clean_text(text: str) -> str:
    """Очистить извлечённый текст от мусора."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Управляющие символы (кроме переноса строки и табуляции).
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Схлопывание пробелов/табов и пустых строк.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Отбрасывание "мусорных" длинных строк без пробелов (base64 и т.п.).
    text = "\n".join(
        line for line in text.splitlines() if not (len(line) > 300 and " " not in line)
    )
    return text.strip()
