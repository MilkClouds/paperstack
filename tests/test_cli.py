import sys
from pathlib import Path

import pytest

from paperstack import cli, dblp_build, dblp_index, metadata
from paperstack.content import arxiv_pdf


def _help(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["paperstack", *args, "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    return capsys.readouterr().out


def test_top_level_exposes_only_command_groups(monkeypatch, capsys):
    output = _help(monkeypatch, capsys)

    assert "{corpus,config,viewer,review,paper,index}" in output
    assert "{corpus,config,viewer,review,paper,index,show" not in output
    assert "paperstack corpus add" in output
    assert "Use `corpus` to select authored data" in output


def test_group_help_explains_review_paper_boundary(monkeypatch, capsys):
    review = _help(monkeypatch, capsys, "review")
    paper = _help(monkeypatch, capsys, "paper")

    assert "authored entry corpus" in review
    assert "paperstack paper" in review
    assert "external source records" in paper
    assert "paperstack review" in paper


def test_content_flags_are_scoped(monkeypatch, capsys):
    output = _help(monkeypatch, capsys, "paper", "read")

    assert "--offline" in output
    assert "--outline" in output
    assert "--section" in output
    assert "print one section by outline number" in output
    assert "--json" not in output
    assert "--sync" not in output


def test_paper_interface_is_flat_and_source_oriented(monkeypatch, capsys):
    paper = _help(monkeypatch, capsys, "paper")
    search = _help(monkeypatch, capsys, "paper", "search")

    for command in ("metadata", "search", "authors", "citations", "references", "read", "pdf"):
        assert command in paper
    for removed in ("bibtex", "watch", "cache"):
        assert removed not in paper
    assert "semantic-scholar" in search
    assert "--category" in search
    assert "--year" in search


def test_review_sync_has_no_contradictory_offline_flag(monkeypatch, capsys):
    output = _help(monkeypatch, capsys, "review", "sync")

    assert "--force" in output
    assert "--offline" not in output


def test_corrupt_offline_index_exits_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["paperstack", "paper", "search", "query", "--source", "dblp", "--offline"])
    monkeypatch.setattr(dblp_index, "installed", lambda: True)
    monkeypatch.setattr(metadata, "search", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unreadable")))

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 3
    error = capsys.readouterr().err
    assert "DBLP index lookup failed: unreadable" in error
    assert "Traceback" not in error


def test_dblp_build_is_exposed_through_cli(monkeypatch, capsys, tmp_path):
    output = tmp_path / "dblp.parquet"
    calls = []

    def build(path, **kwargs):
        calls.append((path, kwargs))
        return {"path": str(path), "records": 1}

    monkeypatch.setattr(dblp_build, "build", build)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "paperstack",
            "index",
            "dblp",
            "build",
            str(output),
            "--snapshot",
            "2026.09",
            "--base",
            "previous.parquet",
            "--venue",
            "rss",
            "--year",
            "2026",
            "--minimum-records",
            "1",
        ],
    )

    assert cli.main() == 0
    assert calls[0][0] == output
    assert calls[0][1]["snapshot"] == "2026.09"
    assert calls[0][1]["base"] == Path("previous.parquet")
    assert calls[0][1]["selected_venues"] == ["rss"]
    assert calls[0][1]["selected_years"] == [2026]
    assert "records: 1" in capsys.readouterr().out


def test_offline_pdf_accepts_a_short_current_conversion(monkeypatch, tmp_path):
    paper_dir = tmp_path / "2601.00001"
    paper_dir.mkdir()
    (paper_dir / "paper.md").write_text("short OCR result " * 8)
    (paper_dir / "meta.json").write_text('{"converter": "pdf-inspector"}')
    monkeypatch.setenv("PAPERSTACK_PAPERS_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["paperstack", "paper", "pdf", "arxiv:2601.00001", "--offline"])
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)

    assert cli.main() == 0
