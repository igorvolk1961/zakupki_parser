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
