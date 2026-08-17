import urllib.error

import pytest

from paperstack import semantic_scholar as s2


def test_headers_use_optional_api_key(monkeypatch):
    monkeypatch.setattr(s2.credentials, "get", lambda name: None)
    assert s2.headers() == {"Accept": "application/json"}

    monkeypatch.setattr(s2.credentials, "get", lambda name: "fixture")
    assert s2.headers()["x-api-key"] == "fixture"


def test_fields_are_bounded_validated_and_deduplicated():
    assert s2.fields("title,authors.name,title", "paperId") == "title,authors.name"
    with pytest.raises(ValueError, match="1-50"):
        s2.fields("title,$bad", "paperId")
    with pytest.raises(ValueError, match="1-50"):
        s2.fields(",".join(f"field{i}" for i in range(51)), "paperId")


@pytest.mark.parametrize(
    ("raw", "encoded"),
    [
        ("arxiv:1706.03762", "ARXIV%3A1706.03762"),
        ("doi:10.1000/example", "DOI%3A10.1000%2Fexample"),
        ("corpus:123", "CorpusId%3A123"),
        ("s2:abc123", "abc123"),
        ("ARXIV:1706.03762", "ARXIV%3A1706.03762"),
    ],
)
def test_identifier_normalizes_supported_curies(raw, encoded):
    assert s2.identifier(raw) == encoded


@pytest.mark.parametrize("raw", ["", "doi:", "bad\nvalue"])
def test_identifier_rejects_empty_or_unsafe_values(raw):
    with pytest.raises(ValueError):
        s2.identifier(raw)


def test_search_forwards_filters_fields_and_pagination(monkeypatch):
    request = {}

    def get_json(url, params=None, headers=None):
        request.update(url=url, params=params, headers=headers)
        return {"total": 0, "data": []}

    monkeypatch.setattr(s2.metadata, "_get_json", get_json)
    monkeypatch.setattr(s2.credentials, "get", lambda name: "fixture")

    result = s2.search(
        "robot learning",
        selected_fields="paperId,title,openAccessPdf",
        limit=25,
        offset=50,
        year="2020-2026",
        fields_of_study=["Computer Science", "Engineering"],
        open_access=True,
    )

    assert result["status"] == "ok"
    assert request["params"] == {
        "query": "robot learning",
        "fields": "paperId,title,openAccessPdf",
        "limit": 25,
        "offset": 50,
        "year": "2020-2026",
        "fieldsOfStudy": "Computer Science,Engineering",
        "openAccessPdf": "",
    }
    assert request["headers"]["x-api-key"] == "fixture"


def test_rate_limit_error_is_actionable(monkeypatch):
    def get_json(*args, **kwargs):
        raise urllib.error.HTTPError("fixture", 429, "", {}, None)

    monkeypatch.setattr(s2.metadata, "_get_json", get_json)

    result = s2.search("fixture")

    assert result["status"] == "error"
    assert "paperstack config set semantic-scholar.api-key" in result["error"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"offset": -1}, "offset"),
        ({"offset": 10_000}, "offset"),
        ({"year": "2020:2022"}, "year"),
        ({"fields_of_study": [""]}, "field-of-study"),
    ],
)
def test_search_rejects_unbounded_filters(kwargs, message):
    with pytest.raises(ValueError, match=message):
        s2.search("fixture", **kwargs)


def test_paper_and_authors_use_encoded_identifier(monkeypatch):
    requests = []

    def get_json(url, params=None, headers=None):
        requests.append((url, params))
        return {"data": []}

    monkeypatch.setattr(s2.metadata, "_get_json", get_json)

    assert s2.paper("doi:10.1000/example", selected_fields="paperId,title")["status"] == "ok"
    assert s2.authors("arxiv:1706.03762", limit=20, offset=10)["status"] == "ok"
    assert requests[0][0].endswith("/paper/DOI%3A10.1000%2Fexample")
    assert requests[1][0].endswith("/paper/ARXIV%3A1706.03762/authors")
    assert requests[1][1]["limit"] == 20
    assert requests[1][1]["offset"] == 10


def test_citation_selects_requested_style(monkeypatch):
    monkeypatch.setattr(
        s2,
        "paper",
        lambda *args, **kwargs: {
            "source": "semantic_scholar",
            "url": "fixture",
            "status": "ok",
            "response": {"citationStyles": {"bibtex": "@article{fixture}", "apa": "Fixture (2026)."}},
        },
    )

    result = s2.citation("fixture", format="apa")

    assert result["citation"] == "Fixture (2026)."
    assert result["format"] == "apa"


def test_citation_reports_unavailable_style(monkeypatch):
    monkeypatch.setattr(
        s2,
        "paper",
        lambda *args, **kwargs: {
            "source": "semantic_scholar",
            "url": "fixture",
            "status": "ok",
            "response": {"citationStyles": {"bibtex": "fixture"}},
        },
    )

    result = s2.citation("fixture", format="mla")

    assert result["status"] == "error"
    assert "available: bibtex" in result["error"]


@pytest.mark.parametrize(
    ("direction", "nested"),
    [("citations", "citingPaper"), ("references", "citedPaper")],
)
def test_graph_normalizes_both_directions(monkeypatch, direction, nested):
    def get_json(url, params=None, headers=None):
        return {
            "next": 10,
            "data": [
                {
                    nested: {
                        "paperId": "s2-id",
                        "externalIds": {"ArXiv": "2401.00001"},
                        "title": "Fixture",
                        "year": 2024,
                        "authors": [{"authorId": "a", "name": "Author"}],
                        "citationCount": 7,
                        "venue": "Venue",
                    },
                    "contexts": ["context"],
                    "intents": ["Background"],
                    "isInfluential": True,
                }
            ],
        }

    monkeypatch.setattr(s2.metadata, "_get_json", get_json)

    result = s2.graph("arxiv:2401.00001", direction, limit=10, offset=0)

    assert result["response"]["next"] == 10
    assert result["response"]["data"][0] == {
        "paper_id": "s2-id",
        "external_ids": {"ArXiv": "2401.00001"},
        "title": "Fixture",
        "year": 2024,
        "authors": [{"authorId": "a", "name": "Author"}],
        "citation_count": 7,
        "venue": "Venue",
        "contexts": ["context"],
        "intents": ["Background"],
        "is_influential": True,
    }


def test_graph_rejects_direction_and_bounds():
    with pytest.raises(ValueError, match="direction"):
        s2.graph("fixture", "related")
    with pytest.raises(ValueError, match="limit"):
        s2.graph("fixture", "citations", limit=1001)
    with pytest.raises(ValueError, match="offset"):
        s2.graph("fixture", "citations", offset=-1)
