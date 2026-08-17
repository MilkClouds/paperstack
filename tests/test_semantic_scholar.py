from urllib.parse import unquote

import pytest

from paperstack import semantic_scholar as s2


@pytest.mark.parametrize(
    ("raw", "encoded"),
    [
        ("arxiv:1706.03762", "ARXIV:1706.03762"),
        ("doi:10.1000/test", "DOI:10.1000/test"),
        ("corpus:123", "CorpusId:123"),
        ("s2:abc", "abc"),
    ],
)
def test_identifier_normalizes_explicit_prefixes(raw, encoded):
    assert unquote(s2.identifier(raw)) == encoded


def test_search_applies_bounded_filters(monkeypatch):
    requested = {}

    def get(url, params):
        requested.update(params)
        return {"source": "semantic_scholar", "url": url, "status": "ok", "response": {"data": []}}

    monkeypatch.setattr(s2, "_get", get)

    result = s2.search(
        "robot learning",
        limit=20,
        offset=40,
        year="2024-2026",
        fields_of_study=["Computer Science"],
        open_access=True,
    )

    assert result["status"] == "ok"
    assert requested["limit"] == 20
    assert requested["offset"] == 40
    assert requested["year"] == "2024-2026"
    assert requested["fieldsOfStudy"] == "Computer Science"
    assert requested["openAccessPdf"] == ""


def test_graph_normalizes_provider_wrappers(monkeypatch):
    monkeypatch.setattr(
        s2,
        "_get",
        lambda url, params: {
            "source": "semantic_scholar",
            "url": url,
            "status": "ok",
            "response": {
                "next": 10,
                "data": [{"citingPaper": {"paperId": "p1", "title": "Paper"}, "contexts": ["context"]}],
            },
        },
    )

    result = s2.graph("arxiv:2401.00001", "citations", limit=10)

    assert result["response"]["next"] == 10
    assert result["response"]["data"][0]["paper_id"] == "p1"
    assert result["response"]["data"][0]["contexts"] == ["context"]


@pytest.mark.parametrize("limit", [0, 1001])
def test_graph_rejects_unbounded_limits(limit):
    with pytest.raises(ValueError, match="limit"):
        s2.graph("s2:fixture", "references", limit=limit)
