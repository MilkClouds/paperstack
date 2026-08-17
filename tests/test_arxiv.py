import json
import os
from urllib.parse import unquote_plus

import pytest

from paperstack import arxiv

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <updated>2024-02-02T00:00:00Z</updated>
    <published>2024-01-01T00:00:00Z</published>
    <title>A {Test} Paper</title>
    <summary>Useful abstract.</summary>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
    <arxiv:doi>10.1000/test</arxiv:doi>
    <link title="pdf" href="https://arxiv.org/pdf/2401.00001v2"/>
  </entry>
</feed>"""


def test_parse_feed_returns_bounded_research_metadata():
    record = arxiv.parse_feed(FEED)[0]

    assert record == {
        "id": "2401.00001",
        "versioned_id": "2401.00001v2",
        "title": "A {Test} Paper",
        "authors": ["Alice Smith", "Bob Jones"],
        "abstract": "Useful abstract.",
        "categories": ["cs.LG"],
        "primary_category": "cs.LG",
        "published": "2024-01-01T00:00:00Z",
        "updated": "2024-02-02T00:00:00Z",
        "comment": None,
        "journal_ref": None,
        "doi": "10.1000/test",
        "url": "https://arxiv.org/abs/2401.00001v2",
        "pdf_url": "https://arxiv.org/pdf/2401.00001v2",
    }


def test_search_preserves_raw_query_and_filters(monkeypatch):
    requested = []
    monkeypatch.setattr(arxiv.metadata, "request", lambda url: requested.append(url) or FEED)

    result = arxiv.search(
        'ti:"robot learning" ANDNOT au:"Example"',
        categories=["cs.RO", "cs.LG"],
        date_from="2024-01-01",
        date_to="2024-12-31",
        limit=7,
        sort="date",
    )

    assert result["status"] == "ok"
    url = unquote_plus(requested[0])
    assert 'ti:"robot learning" ANDNOT au:"Example"' in url
    assert "cat:cs.RO OR cat:cs.LG" in url
    assert "submittedDate:[202401010000 TO 202412312359]" in url
    assert "max_results=7" in requested[0]
    assert "sortBy=submittedDate" in requested[0]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"categories": ["../cs.LG"]}, "category"),
        ({"date_from": "bad"}, "invalid date"),
        ({"date_from": "2025-01-01", "date_to": "2024-01-01"}, "date-from"),
    ],
)
def test_search_rejects_unbounded_or_invalid_inputs(kwargs, message):
    with pytest.raises(ValueError, match=message):
        arxiv.search("fixture", **kwargs)


def test_bibtex_escapes_values():
    record = arxiv.parse_feed(FEED)[0]

    rendered = arxiv.bibtex(record)

    assert rendered.startswith("@misc{arxiv_2401_00001,")
    assert "title = {A \\{Test\\} Paper}" in rendered
    assert "author = {Alice Smith and Bob Jones}" in rendered
    assert "primaryClass = {cs.LG}" in rendered
    assert "doi = {10.1000/test}" in rendered


def test_cached_papers_reports_complete_cache_kinds(tmp_path):
    source = tmp_path / "2401.00001" / "src"
    source.mkdir(parents=True)
    (source / "main.tex").write_text("fixture")
    pdf = tmp_path / "2402.00002" / "paper.md"
    pdf.parent.mkdir()
    pdf.write_text("converted")
    (tmp_path / "incomplete").mkdir()

    assert arxiv.cached_papers(tmp_path) == [
        {"id": "2401.00001", "source": True, "pdf": False, "pdf_chars": 0},
        {"id": "2402.00002", "source": False, "pdf": True, "pdf_chars": 9},
    ]


def test_watches_are_atomic_and_preserve_checkpoint(tmp_path):
    path = tmp_path / "config" / "watches.json"
    first = arxiv.add_watch("robot learning", categories=["cs.RO"], limit=3, path=path)
    updated = arxiv.add_watch("robot learning", categories=["cs.LG"], limit=4, path=path)

    assert first["created_at"] == updated["created_at"]
    assert arxiv.load_watches(path)["topics"] == [updated]
    if os.name != "nt":
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert not list(path.parent.glob(".arxiv-watches-*"))


def test_alert_checkpoint_advances_only_after_success(monkeypatch, tmp_path):
    path = tmp_path / "watches.json"
    arxiv.add_watch("robot learning", limit=2, path=path)
    original = json.loads(path.read_text())

    monkeypatch.setattr(arxiv, "search", lambda *args, **kwargs: {"status": "error", "error": "offline"})
    with pytest.raises(RuntimeError, match="offline"):
        arxiv.check_watches(path=path)
    assert json.loads(path.read_text()) == original

    monkeypatch.setattr(
        arxiv,
        "search",
        lambda *args, **kwargs: {"status": "ok", "response": {"matches": [{"id": "2401.00001"}]}},
    )
    result = arxiv.check_watches(path=path)

    assert result["alerts"][0]["new_paper_count"] == 1
    assert arxiv.load_watches(path)["topics"][0]["last_checked"] == result["checked_at"]


def test_alerts_filter_same_minute_results_against_exact_checkpoint(monkeypatch, tmp_path):
    path = tmp_path / "watches.json"
    arxiv.add_watch("robot learning", path=path)
    payload = arxiv.load_watches(path)
    payload["topics"][0]["last_checked"] = "2026-08-17T11:20:30+00:00"
    arxiv.save_watches(payload, path)
    monkeypatch.setattr(
        arxiv,
        "search",
        lambda *args, **kwargs: {
            "status": "ok",
            "response": {
                "matches": [
                    {"id": "old", "published": "2026-08-17T11:20:10+00:00"},
                    {"id": "new", "published": "2026-08-17T11:20:40+00:00"},
                ]
            },
        },
    )

    result = arxiv.check_watches(path=path)

    assert result["alerts"][0]["new_papers"] == [{"id": "new", "published": "2026-08-17T11:20:40+00:00"}]


def test_remove_watch_is_idempotent(tmp_path):
    path = tmp_path / "watches.json"
    arxiv.add_watch("fixture", path=path)

    assert arxiv.remove_watch("fixture", path=path) is True
    assert arxiv.remove_watch("fixture", path=path) is False
