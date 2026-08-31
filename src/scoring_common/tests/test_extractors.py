"""Unit-тесты извлечения текста из легаси .doc (scoring_common.tz.extractors)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scoring_common.tz.extractors import _decode


def _fake_run_libreoffice(tmpdir_holder: dict):
    """Фейк subprocess.run для LibreOffice: пишет document.txt в outdir."""

    def run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        outdir_ndx = argv.index("--outdir")
        outdir = argv[outdir_ndx + 1]
        (Path(outdir) / "document.txt").write_text("текст легаси doc", encoding="utf-8")
        return SimpleNamespace(stdout=b"")

    return run


def test_decode_doc_uses_libreoffice(monkeypatch) -> None:
    """Легаси .doc конвертируется LibreOffice_headless (проверка связки)."""
    from scoring_common.tz import extractors

    def fake_which(name: str) -> str | None:
        return "/usr/bin/soffice" if name in ("soffice", "libreoffice") else None

    monkeypatch.setattr(extractors.shutil, "which", fake_which)
    monkeypatch.setattr(extractors.subprocess, "run", _fake_run_libreoffice({}))
    assert _decode(b"D0\xcf\x11\xe0", "описание объекта закупки.doc") == "текст легаси doc"


def test_decode_doc_no_converter_returns_none(monkeypatch) -> None:
    """Без внешнего конвертера .doc не извлекается (best-effort, None)."""
    from scoring_common.tz import extractors

    monkeypatch.setattr(extractors.shutil, "which", lambda name: None)
    assert _decode(b"some-doc-bytes", "описание объекта закупки.doc") is None


def test_decode_doc_falls_back_to_catdoc(monkeypatch) -> None:
    """Если LibreOffice нет, текст берём из catdoc (stdout, UTF-8)."""
    from scoring_common.tz import extractors

    def fake_which(name: str) -> str | None:
        if name in ("soffice", "libreoffice"):
            return None
        if name == "catdoc":
            return "/usr/bin/catdoc"
        return None

    monkeypatch.setattr(extractors.shutil, "which", fake_which)
    monkeypatch.setattr(
        extractors.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(stdout="текст из catdoc".encode()),
    )
    assert _decode(b"some-doc-bytes", "описание.doc") == "текст из catdoc"


def test_decode_detects_docx_by_signature(monkeypatch) -> None:
    """Имя без расширения (Росэлторг «Техническое задание») → docx по байтам PK.

    ZIP/OOXML-байты читаются как .docx, даже если расширения в имени нет.
    """
    from scoring_common.tz import extractors

    monkeypatch.setattr(extractors, "_extract_docx", lambda raw: f"docx:{raw!r}")
    assert _decode(b"PK\x03\x04...", "Техническое задание") == "docx:b'PK\\x03\\x04...'"


def test_decode_detects_pdf_by_signature(monkeypatch) -> None:
    """Имя без расширения → PDF по сигнатуре %PDF."""
    from scoring_common.tz import extractors

    monkeypatch.setattr(extractors, "_extract_pdf", lambda raw: f"pdf:{raw!r}")
    assert _decode(b"%PDF-1.7", "Техническое задание") == "pdf:b'%PDF-1.7'"


def test_decode_detects_doc_by_signature(monkeypatch) -> None:
    """Имя без расширения → легаси .doc по OLE2-сигнатуре D0CF11E0."""
    from scoring_common.tz import extractors

    monkeypatch.setattr(extractors, "_extract_doc", lambda raw: f"doc:{raw!r}")
    assert _decode(b"\xd0\xcf\x11\xe0", "Техническое задание") == "doc:b'\\xd0\\xcf\\x11\\xe0'"


def test_decode_extension_takes_precedence_over_signature(monkeypatch) -> None:
    """Явное расширение используется до эвристики по содержимому."""
    from scoring_common.tz import extractors

    # Тот же документ, но с расширением .pdf — должен уйти в pdf, а не в docx.
    monkeypatch.setattr(extractors, "_extract_pdf", lambda raw: "pdf-branch")
    monkeypatch.setattr(extractors, "_extract_docx", lambda raw: "docx-branch")
    assert _decode(b"PK\x03\x04", "Техническое задание.pdf") == "pdf-branch"


def test_decode_unknown_signature_falls_back_to_plain() -> None:
    """Произвольные байты без расширения читаются как plain-text (cp1251)."""
    assert _decode("текст закупки".encode("cp1251"), "Техническое задание") == "текст закупки"
