import sys

from paperstack.content import arxiv_pdf


def test_missing_converter_names_the_installable_extra(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setitem(sys.modules, "pymupdf4llm", None)

    assert arxiv_pdf.convert("2601.00001") is False
    error = capsys.readouterr().err
    assert "paperstack-cli[pdf]" in error
    assert "uv tool install --force" in error
