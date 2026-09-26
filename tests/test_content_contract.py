"""Source identity, document coverage and partial extraction are observable CLI contracts."""

import hashlib
import json
import sys

import pytest

from paperstack import cli, metadata
from paperstack.content import arxiv_pdf


def tex(cache, ident, name, body):
    path = cache / ident / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r"\documentclass{article}\begin{document}\section{Results}" + body + r"\end{document}")


def run(monkeypatch, cache, *args):
    monkeypatch.setenv("PAPERSTACK_PAPERS_DIR", str(cache))
    monkeypatch.setattr(sys, "argv", ["paperstack", "paper", *args])
    return cli.main()


def test_versioned_read_never_uses_unversioned_or_other_version_cache(tmp_path, monkeypatch, capsys):
    tex(tmp_path, "2406.09246", "main.tex", "UNPINNED")
    tex(tmp_path, "2406.09246v1", "main.tex", "FIRST_VERSION")
    tex(tmp_path, "2406.09246v2", "main.tex", "SECOND_VERSION")
    assert run(monkeypatch, tmp_path, "read", "arxiv:2406.09246v1", "--offline") == 0
    output = capsys.readouterr().out
    assert "FIRST_VERSION" in output and "UNPINNED" not in output and "SECOND_VERSION" not in output
    with pytest.raises(SystemExit):
        run(monkeypatch, tmp_path, "read", "arxiv:2406.09246v3", "--offline")
    assert metadata.PaperRef.parse("arxiv:2406.09246v1").value == "2406.09246"


def test_multiple_roots_require_selection_and_supplement_can_be_read(tmp_path, monkeypatch, capsys):
    tex(tmp_path, "2406.09246v1", "main.tex", "MAIN")
    tex(tmp_path, "2406.09246v1", "supplement.tex", "SUPPLEMENT")
    with pytest.raises(SystemExit, match="multiple source documents"):
        run(monkeypatch, tmp_path, "read", "arxiv:2406.09246v1", "--offline")
    assert run(monkeypatch, tmp_path, "read", "arxiv:2406.09246v1", "--offline", "--documents") == 0
    assert "supplement.tex" in capsys.readouterr().out
    assert run(monkeypatch, tmp_path, "read", "arxiv:2406.09246v1", "--offline", "--document", "supplement.tex") == 0
    assert "SUPPLEMENT" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="unknown source document"):
        run(monkeypatch, tmp_path, "read", "arxiv:2406.09246v1", "--offline", "--document", "../escape.tex")


def cache_pdf(directory, quality="complete"):
    directory.mkdir(parents=True)
    raw, text = b"%PDF-test", b"actual source text " * 20
    (directory / "paper.pdf").write_bytes(raw)
    (directory / "paper.md").write_bytes(text)
    (directory / "meta.json").write_text(
        json.dumps(
            {
                "converter": "pdf-inspector",
                "quality": quality,
                "bytes": len(text),
                "sha256": hashlib.sha256(text).hexdigest(),
                "pdf_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    )


def test_pdf_quality_and_original_bytes_are_checked_offline(tmp_path, monkeypatch):
    directory = tmp_path / "2406.09246v1"
    cache_pdf(directory, "partial")
    with pytest.raises(SystemExit):
        run(monkeypatch, tmp_path, "pdf", "arxiv:2406.09246v1", "--offline")
    assert run(monkeypatch, tmp_path, "pdf", "arxiv:2406.09246v1", "--offline", "--allow-partial") == 0
    (directory / "paper.pdf").write_bytes(b"%PDF-different")
    assert arxiv_pdf._cached_conversion(directory, allow_partial=True) is None


@pytest.mark.parametrize("reference", ["doi:10.1234/example", "openreview:abc123"])
def test_explicit_non_arxiv_pdf_is_read_without_network(tmp_path, monkeypatch, capsys, reference):
    url = "https://publisher.example/paper.pdf"
    key = "external-" + hashlib.sha256(f"{reference}\n{url}".encode()).hexdigest()
    cache_pdf(tmp_path / key)
    assert run(monkeypatch, tmp_path, "read", reference, "--pdf-url", url, "--offline", "--max-chars", "18") == 0
    assert capsys.readouterr().out.strip() == "actual source text"
    with pytest.raises(SystemExit):
        run(monkeypatch, tmp_path, "read", reference, "--offline")


def test_online_pdf_download_keeps_requested_version(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(arxiv_pdf, "convert", lambda ident, **kwargs: seen.append((ident, kwargs)) or True)
    assert run(monkeypatch, tmp_path, "pdf", "arxiv:2406.09246v2") == 0
    assert seen[0][0] == "2406.09246v2"
    assert not seen[0][1]["allow_partial"]


def test_source_without_sections_is_readable(tmp_path, monkeypatch, capsys):
    src = tmp_path / "2406.09246v1" / "src"
    src.mkdir(parents=True)
    (src / "main.tex").write_text(r"\documentclass{article}\begin{document}Unsectioned result\end{document}")
    assert run(monkeypatch, tmp_path, "read", "arxiv:2406.09246v1", "--offline") == 0
    assert "Unsectioned result" in capsys.readouterr().out


def test_non_arxiv_online_read_stdout_contains_only_text(tmp_path, monkeypatch, capsys):
    def convert(key, **kwargs):
        assert kwargs["url"] == "https://publisher.example/paper.pdf"
        assert kwargs["paper_ref"] == "doi:10.1234/example"
        cache_pdf(tmp_path / key)
        print("Conversion progress")
        return True

    monkeypatch.setattr(arxiv_pdf, "convert", convert)
    assert (
        run(monkeypatch, tmp_path, "read", "doi:10.1234/example", "--pdf-url", "https://publisher.example/paper.pdf")
        == 0
    )
    captured = capsys.readouterr()
    assert "Conversion progress" not in captured.out
    assert "Conversion progress" in captured.err
    assert captured.out.startswith("actual source text")


@pytest.mark.parametrize("option", ["--refresh", "--outline", "--document"])
def test_pdf_offline_conflicting_modes_fail(tmp_path, monkeypatch, option):
    arguments = [
        "read",
        "doi:10.1234/example",
        "--pdf-url",
        "https://publisher.example/paper.pdf",
        "--offline",
        option,
    ]
    if option == "--document":
        arguments.append("main.tex")
    with pytest.raises(SystemExit):
        run(monkeypatch, tmp_path, *arguments)
