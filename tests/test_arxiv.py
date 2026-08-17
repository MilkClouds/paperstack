from urllib.parse import unquote_plus

import pytest

from paperstack import arxiv

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <updated>2024-02-02T00:00:00Z</updated>
    <published>2024-01-01T00:00:00Z</published>
    <title>A Test Paper</title>
    <summary>Useful abstract.</summary>
    <author><name>Alice Smith</name></author>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
    <link title="pdf" href="https://arxiv.org/pdf/2401.00001v2"/>
  </entry>
</feed>"""


def test_parse_feed_returns_source_metadata():
    record = arxiv.parse_feed(FEED)[0]

    assert record["id"] == "2401.00001"
    assert record["versioned_id"] == "2401.00001v2"
    assert record["title"] == "A Test Paper"
    assert record["authors"] == ["Alice Smith"]
    assert record["primary_category"] == "cs.LG"


def test_search_preserves_query_and_filters(monkeypatch):
    requested = []
    monkeypatch.setattr(arxiv.metadata, "request", lambda url: requested.append(url) or FEED)

    result = arxiv.search(
        'ti:"robot learning"',
        categories=["cs.RO"],
        date_from="2024-01-01",
        date_to="2024-12-31",
        limit=7,
        sort="date",
    )

    assert result["status"] == "ok"
    url = unquote_plus(requested[0])
    assert 'ti:"robot learning"' in url
    assert "cat:cs.RO" in url
    assert "submittedDate:[202401010000 TO 202412312359]" in url
    assert "max_results=7" in requested[0]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 101},
        {"categories": ["../cs.LG"]},
        {"date_from": "bad"},
        {"date_from": "2025-01-01", "date_to": "2024-01-01"},
    ],
)
def test_search_rejects_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        arxiv.search("fixture", **kwargs)


def test_date_normalizes_explicit_timezones():
    assert arxiv._date("2024-01-01T23:00:00-05:00") == "202401020400"
