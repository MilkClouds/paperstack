import urllib.error

import pytest

from paperstack import dblp_index, metadata
from paperstack.metadata import PaperRef


@pytest.mark.parametrize(
    ("raw", "kind", "value"),
    [
        ("arxiv:2410.24164v2", "arxiv", "2410.24164"),
        ("arxiv:hep-th/9901001", "arxiv", "hep-th/9901001"),
        ("arxiv:math.GT/0309136", "arxiv", "math.GT/0309136"),
        ("arxiv:cs.SE/9901001", "arxiv", "cs.SE/9901001"),
        ("arxiv:nlin.CD/0001001", "arxiv", "nlin.CD/0001001"),
        ("doi:10.1000/example", "doi", "10.1000/example"),
        ("dblp:conf/rss/Example24", "dblp", "conf/rss/Example24"),
        ("openreview:abc123", "openreview", "abc123"),
    ],
)
def test_paper_reference(raw, kind, value):
    assert PaperRef.parse(raw) == PaperRef(kind, value)


def test_paper_reference_requires_explicit_kind():
    with pytest.raises(ValueError, match="needs a prefix"):
        PaperRef.parse("2410.24164")


@pytest.mark.parametrize("raw", ["arxiv:../../target", "arxiv:/tmp/target", "arxiv:2413.00001"])
def test_arxiv_reference_rejects_unsafe_or_invalid_ids(raw):
    with pytest.raises(ValueError, match="invalid arXiv"):
        PaperRef.parse(raw)


def test_direct_source_fetch_does_not_resolve_through_s2(monkeypatch):
    monkeypatch.setattr(metadata, "resolve_s2", lambda ref: pytest.fail("unexpected discovery lookup"))
    monkeypatch.setattr(
        metadata,
        "fetch_arxiv",
        lambda arxiv_id: {"source": "arxiv", "url": "fixture", "status": "ok", "response": {"id": arxiv_id}},
    )

    results = metadata.fetch_all("arxiv:2410.24164", {"arxiv"})

    assert results[0]["response"]["id"] == "2410.24164"


def test_s2_metadata_requests_citation_fields(monkeypatch):
    requested = {}

    def get_json(url, params=None, headers=None):
        requested.update(params or {})
        return {"paperId": "fixture"}

    monkeypatch.setattr(metadata, "_get_json", get_json)

    result = metadata.resolve_s2(PaperRef("arxiv", "2410.24164"))

    assert result["status"] == "ok"
    fields = set(requested["fields"].split(","))
    assert {"citationCount", "influentialCitationCount", "referenceCount"} <= fields


def test_s2_search_requests_citation_fields(monkeypatch):
    requested = {}

    def get_json(url, params=None, headers=None):
        requested.update(params or {})
        return {"data": []}

    monkeypatch.setattr(metadata, "_get_json", get_json)

    result = metadata.search("s2", "fixture")

    assert result["status"] == "ok"
    fields = set(requested["fields"].split(","))
    assert {"citationCount", "influentialCitationCount", "referenceCount"} <= fields


@pytest.mark.parametrize(
    ("source", "parameter"),
    [("dblp", "h"), ("crossref", "rows"), ("openreview", "limit"), ("s2", "limit")],
)
def test_search_passes_limit_to_remote_source(monkeypatch, source, parameter):
    requests = []

    def get_json(url, params=None, headers=None):
        requests.append(params or {})
        return {"notes": []} if source == "openreview" else {"data": []}

    monkeypatch.setattr(metadata, "_get_json", get_json)
    monkeypatch.setattr(dblp_index, "search", lambda query, limit: [])

    result = metadata.search(source, " fixture ", limit=5)

    assert result["status"] == "ok"
    assert requests[0][parameter] == 5


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("ViCA: Efficient Multimodal LLMs", "ViCA Efficient Multimodal LLMs"),
        ("Ratios 1:1: Efficient Matching", "Ratios 1:1 Efficient Matching"),
        ("title: Efficient Matching", "title Efficient Matching"),
    ],
)
def test_dblp_search_sanitizes_title_colon(monkeypatch, query, expected):
    requested = {}
    monkeypatch.setattr(dblp_index, "search", lambda query, limit: [])
    monkeypatch.setattr(metadata, "_get_json", lambda url, params=None, headers=None: requested.update(params or {}))

    result = metadata.search("dblp", query, limit=5)

    assert result["status"] == "ok"
    assert requested["q"] == expected


def test_dblp_search_preserves_source_tokens(monkeypatch):
    requested = {}
    monkeypatch.setattr(dblp_index, "search", lambda query, limit: [])
    monkeypatch.setattr(metadata, "_get_json", lambda url, params=None, headers=None: requested.update(params or {}))

    metadata.search("dblp", "author:Michael Ley: title:Data Mining:")

    assert requested["q"] == "author:Michael Ley: title:Data Mining:"


def test_legacy_arxiv_search_passes_limit(monkeypatch):
    requested = {}

    def get_text(url, params=None):
        requested.update(params or {})
        return '<feed xmlns="http://www.w3.org/2005/Atom" />'

    monkeypatch.setattr(metadata, "_get_text", get_text)

    result = metadata.search("arxiv", "fixture", limit=5)

    assert result["status"] == "ok"
    assert requested["max_results"] == 5


@pytest.mark.parametrize(("query", "limit"), [(" ", 10), ("fixture", 0), ("fixture", 101)])
def test_search_rejects_invalid_common_arguments(query, limit):
    with pytest.raises(ValueError):
        metadata.search("crossref", query, limit=limit)


def test_unavailable_source_is_reported(monkeypatch):
    monkeypatch.setattr(metadata, "resolve_s2", lambda ref: {"source": "semantic_scholar", "status": "error"})

    results = metadata.fetch_all("arxiv:2410.24164", {"crossref"})

    assert results == [{"source": "crossref", "status": "unavailable", "reason": "no DOI after discovery"}]


def test_acl_doi_survives_incomplete_discovery(monkeypatch):
    monkeypatch.setattr(
        metadata,
        "resolve_s2",
        lambda ref: {
            "source": "semantic_scholar",
            "status": "ok",
            "response": {"externalIds": {}, "title": "Fixture"},
        },
    )
    monkeypatch.setattr(
        metadata,
        "fetch_acl",
        lambda acl_id: {"source": "acl_anthology", "status": "ok", "response": {"id": acl_id}},
    )

    results = metadata.fetch_all(
        "doi:10.18653/v1/2020.acl-main.1",
        {"semantic_scholar", "acl_anthology"},
    )

    assert results[-1]["response"]["id"] == "2020.acl-main.1"


def test_request_retries_rate_limits(monkeypatch):
    attempts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"ok"

    def open_request(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) < 3:
            raise urllib.error.HTTPError(request.full_url, 429, "rate limited", {}, None)
        return Response()

    monkeypatch.setattr(metadata.urllib.request, "urlopen", open_request)
    monkeypatch.setattr(metadata.time, "sleep", lambda seconds: None)

    assert metadata.request("https://example.test/data") == b"ok"
    assert len(attempts) == 3


def test_retry_delay_honors_retry_after():
    error = urllib.error.HTTPError("https://example.test", 429, "limited", {"Retry-After": "17"}, None)

    assert metadata._retry_delay(error, 0) == 17


def test_unauthenticated_s2_rate_limit_suggests_configured_key(monkeypatch):
    def rate_limited(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "rate limited", {}, None)

    monkeypatch.setattr(metadata.urllib.request, "urlopen", rate_limited)
    monkeypatch.setattr(metadata.time, "sleep", lambda seconds: None)

    with pytest.raises(urllib.error.HTTPError, match="paperstack config set semantic-scholar.api-key"):
        metadata.request("https://api.semanticscholar.org/graph/v1/paper/test")


def test_authenticated_s2_rate_limit_does_not_suggest_configured_key(monkeypatch):
    def rate_limited(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "rate limited", {}, None)

    monkeypatch.setattr(metadata.urllib.request, "urlopen", rate_limited)
    monkeypatch.setattr(metadata.time, "sleep", lambda seconds: None)

    with pytest.raises(urllib.error.HTTPError, match="^HTTP Error 429: rate limited$"):
        metadata.request(
            "https://api.semanticscholar.org/graph/v1/paper/test",
            headers={"x-api-key": "configured"},
        )


def test_openreview_falls_back_to_v1(monkeypatch):
    monkeypatch.setattr(metadata, "_post_json", lambda *args, **kwargs: {"notes": []})

    def get_json(url, params=None, headers=None):
        return {"notes": [{"id": "paper-id"}]}

    monkeypatch.setattr(metadata, "_get_json", get_json)

    result = metadata.fetch_openreview("paper-id")

    assert result["status"] == "ok"
    assert result["response"]["notes"][0]["id"] == "paper-id"


def test_openreview_uses_exact_id_search(monkeypatch):
    requests = []

    def post_json(url, payload, headers=None):
        requests.append((url, payload, headers))
        return {"notes": [{"id": "paper-id", "forum": "paper-id"}]}

    monkeypatch.setattr(metadata, "_post_json", post_json)

    result = metadata.fetch_openreview("paper-id")

    assert result["status"] == "ok"
    assert requests[0][1]["ids"] == ["paper-id"]


def test_openreview_exact_title_and_status_filter(monkeypatch):
    notes = [
        {
            "id": "accepted",
            "forum": "accepted",
            "content": {
                "title": {"value": "ViCTOR: Visual Token Compression"},
                "venue": {"value": "ICLR 2026 Conference"},
            },
        },
        {
            "id": "unrelated",
            "forum": "unrelated",
            "content": {"title": {"value": "Compound Tokens"}},
        },
    ]
    requests = []

    def get_json(url, params=None, headers=None):
        requests.append((url, params))
        return {"notes": notes}

    monkeypatch.setattr(metadata, "_get_json", get_json)

    result = metadata.search(
        "openreview",
        "ViCTOR - Visual Token Compression",
        exact_title=True,
        openreview_status="accepted",
    )

    assert [note["id"] for note in result["response"]["matches"]] == ["accepted"]
    assert requests[0][1] == {
        "limit": 20,
        "source": "forum",
        "cache": "true",
        "term": "ViCTOR - Visual Token Compression",
        "type": "exact",
        "content": "title",
    }


def test_openreview_status_filter_paginates_before_truncating(monkeypatch):
    requests = []

    def get_json(url, params=None, headers=None):
        requests.append(params or {})
        if (params or {}).get("offset", 0) == 0:
            return {"notes": [{"id": str(index), "forum": str(index), "content": {}} for index in range(20)]}
        return {
            "notes": [
                {
                    "id": "accepted",
                    "forum": "accepted",
                    "content": {"decision": {"value": "Accept"}},
                }
            ]
        }

    monkeypatch.setattr(metadata, "_get_json", get_json)

    result = metadata.search("openreview", "fixture", limit=1, openreview_status="accepted")

    assert result["response"]["matches"][0]["id"] == "accepted"
    assert requests[1]["offset"] == 20


def test_openreview_v1_paginates_until_exact_title_match(monkeypatch):
    requests = []

    def get_json(url, params=None, headers=None):
        requests.append((url, params or {}))
        if "api2" in url:
            return {"notes": []}
        if (params or {}).get("offset", 0) == 0:
            return {
                "notes": [
                    {"id": str(index), "forum": str(index), "content": {"title": "Partial match"}}
                    for index in range(20)
                ]
            }
        return {"notes": [{"id": "exact", "forum": "exact", "content": {"title": "Exact Title"}}]}

    monkeypatch.setattr(metadata, "_get_json", get_json)

    result = metadata.search("openreview", "Exact Title", limit=1, exact_title=True)

    assert result["response"]["matches"][0]["id"] == "exact"
    assert requests[-1][1]["offset"] == 20


def test_openreview_request_does_not_require_writable_cache(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"ok"

    monkeypatch.setattr(metadata.Path, "mkdir", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read only")))
    monkeypatch.setattr(metadata.urllib.request, "urlopen", lambda request, timeout: Response())

    assert metadata.request("https://api2.openreview.net/notes/search") == b"ok"


@pytest.mark.parametrize(
    ("note", "status"),
    [
        ({"invitations": ["Venue/-/Withdrawn_Submission"]}, "withdrawn"),
        ({"content": {"decision": {"value": "Accept (Poster)"}}}, "accepted"),
        ({"id": "paper", "forum": "paper", "content": {}}, "submission"),
    ],
)
def test_openreview_status_inference(note, status):
    assert metadata._openreview_status(note) == status


def test_offline_dblp_metadata_miss_does_not_use_network(monkeypatch):
    monkeypatch.setattr(dblp_index, "lookup", lambda **kwargs: [])
    monkeypatch.setattr(dblp_index, "search", lambda *args, **kwargs: [])
    monkeypatch.setattr(metadata, "_get_text", lambda *args, **kwargs: pytest.fail("unexpected network request"))

    results = metadata.fetch_all("doi:10.1000/missing", {"dblp"}, local_only=True)

    assert results[0]["status"] == "no_match"


def test_offline_dblp_search_miss_does_not_use_network(monkeypatch):
    monkeypatch.setattr(dblp_index, "search", lambda *args, **kwargs: [])
    monkeypatch.setattr(metadata, "_get_json", lambda *args, **kwargs: pytest.fail("unexpected network request"))

    result = metadata.search("dblp", "missing title", local_only=True)

    assert result["status"] == "no_match"
