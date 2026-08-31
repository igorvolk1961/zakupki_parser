"""Unit-тесты очистки текста ТЗ и нормализации GFM-таблиц (scoring_common.tz.text)."""

from __future__ import annotations

from scoring_common.tz.text import clean_text


def test_clean_text_normalizes_newlines_and_whitespace() -> None:
    """CR/CRLF выравниваются, лишние пробелы и пустые строки схлопываются."""
    assert clean_text("a\r\nb\r\nc\n\n\n\nd") == "a\nb\nc\n\nd"


def test_clean_text_strips_control_chars() -> None:
    """Управляющие символы (кроме \n, \t) удаляются; таб схлопывается в пробел."""
    assert clean_text("a\x00b\x08c\td") == "abc d"


def test_clean_text_docx_table_becomes_readable_rows() -> None:
    """Таблица DOCX (GFM, с пустой строкой-заглушкой) → читаемые строки данных.

    MarkItDown отдаёт таблицу с пустой строкой-заглушкой ``|  |  |  |`` вместо
    заголовка; настоящие заголовки — первая непустая строка.
    """
    md = (
        "# Раздел 1. Требования\n\n"
        "|  |  |  |\n"
        "| --- | --- | --- |\n"
        "| Показатель | Значение | Комментарий |\n"
        "| Опыт | 3 года | Подтвердить договорами |\n"
        "| Лицензия | ФСБ | Гостайна |\n\n"
        "Обычный текст после таблицы."
    )
    text = clean_text(md)
    # Заголовок и текст сохраняются.
    assert "# Раздел 1. Требования" in text
    assert "Обычный текст после таблицы." in text
    # Разделитель и пустая строка-заглушка убраны.
    assert "| --- |" not in text
    assert "|  |  |" not in text
    # Строки данных стали читаемыми «заголовок: значение».
    assert "Показатель: Опыт | Значение: 3 года | Комментарий: Подтвердить договорами" in text
    assert "Показатель: Лицензия | Значение: ФСБ | Комментарий: Гостайна" in text


def test_clean_text_table_with_proper_header() -> None:
    """Таблица с корректным заголовком (первая строка) и без разделителя."""
    md = "| Имя | Возраст |\n| Иван | 30 |\n| Пётр | 40 |"
    text = clean_text(md)
    assert "Имя: Иван | Возраст: 30" in text
    assert "Имя: Пётр | Возраст: 40" in text


def test_clean_text_header_only_table() -> None:
    """Таблица только с заголовком («| --- |» без данных) не ломает текст."""
    md = "| A | B |\n| --- | --- |"
    assert clean_text(md) == "A | B"


def test_clean_text_single_pipe_line_not_treated_as_table() -> None:
    """Одиночная строка с ``|`` (не таблица) остаётся как есть."""
    md = "Просто текст с | символом в середине."
    assert clean_text(md) == "Просто текст с | символом в середине."


def test_clean_text_drops_long_void_lines() -> None:
    """Длинные строки без пробелов (base64/подписи) отбрасываются."""
    text = clean_text("короткий текст\n" + "A" * 400)
    assert "короткий текст" in text
    assert "A" * 400 not in text
