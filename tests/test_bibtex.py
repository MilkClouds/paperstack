from pathlib import Path

import pytest

from paperstack import bibtex


def write(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def test_lint_applies_style_rules(tmp_path):
    bibliography = write(
        tmp_path / "references.bib",
        """% source: https://example.test/paper
@inproceedings{Good2025,
  title = {Good},
  author = {A. Author and B. Author and others},
  booktitle = {CVPR},
  year = {2025}
}

@article{bad key,
  title = {Bad},
  author = {A and B and C},
  journal = {Unknown Venue}
}
""",
    )
    style = write(
        tmp_path / "bibstyle.toml",
        """[authors]
max = 2

[citation_keys]
pattern = "^[A-Za-z0-9]+$"

[fields.required]
all = ["title", "author", "year"]

[venues]
allowed = ["CVPR"]

[provenance]
required = true
""",
    )

    result = bibtex.lint(bibliography, style)

    assert result["status"] == "invalid"
    assert result["entries"] == 2
    assert {issue["code"] for issue in result["issues"]} == {
        "authors.max",
        "citation_key.pattern",
        "field.required",
        "provenance.required",
        "venue.allowed",
    }


def test_lint_detects_duplicate_and_unterminated_entries(tmp_path):
    bibliography = write(
        tmp_path / "references.bib",
        """@article{same, title={One}}
@article{same, title={Two}}
@article{broken, title={Three}
""",
    )

    result = bibtex.lint(bibliography)

    assert {issue["code"] for issue in result["issues"]} == {"citation_key.duplicate", "syntax"}


def test_lint_rejects_invalid_style_shape(tmp_path):
    bibliography = write(tmp_path / "references.bib", "@article{key, title={One}}")
    style = write(tmp_path / "bibstyle.toml", "[authors]\nmax = 0\n")

    with pytest.raises(ValueError, match="authors.max"):
        bibtex.lint(bibliography, style)


def test_parser_handles_inline_fields_comments_and_parenthesized_entries():
    raw = """% @article{ignored, title={Ignored}}
@article{inline, title={Inline}, author={A and B}}
@article(parenthesized, title={A result ) with caveat}, author={C})
"""

    entries, issues = bibtex.parse(raw)

    assert issues == []
    assert [entry.key for entry in entries] == ["inline", "parenthesized"]
    assert bibtex.field(entries[0].raw, "author") == "A and B"
    assert bibtex.field(entries[1].raw, "title") == "A result ) with caveat"


def test_parser_rejects_missing_field_comma():
    entries, issues = bibtex.parse("@article{key,\n title={Fixture}\n author={A. Author}\n}")

    assert len(entries) == 1
    assert issues[0]["message"] == "malformed BibTeX field list"
