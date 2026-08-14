import sys
from types import SimpleNamespace

from paperstack.content import arxiv_pdf


def test_cached_markdown_does_not_import_converter(tmp_path, monkeypatch):
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    cached = tmp_path / "2601.00001" / "paper.md"
    cached.parent.mkdir(parents=True)
    cached.write_text("x" * 1001)
    monkeypatch.setitem(sys.modules, "pymupdf4llm", None)
    assert arxiv_pdf.convert("2601.00001") is True


def test_missing_converter_names_the_distribution(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setitem(sys.modules, "pymupdf4llm", None)
    assert arxiv_pdf.convert("2601.00001") is False
    assert "paperstack-cli[pdf]" in capsys.readouterr().err


def test_converter_failure_is_reported_without_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(arxiv_pdf, "_fetch", lambda url: b"%PDF" + b"x" * 100)
    monkeypatch.setitem(
        sys.modules,
        "pymupdf4llm",
        SimpleNamespace(to_markdown=lambda path: (_ for _ in ()).throw(RuntimeError("broken"))),
    )
    assert arxiv_pdf.convert("2601.00001") is False
    error = capsys.readouterr().err
    assert "PDF conversion failed (broken)" in error
    assert "Traceback" not in error
