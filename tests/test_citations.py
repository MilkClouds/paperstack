import json

import pytest

from paperstack import citations


def test_arxiv_id_accepts_curies_and_urls():
    assert citations.arxiv_id("arxiv:2410.24164v2") == "2410.24164"
    assert citations.arxiv_id("https://arxiv.org/abs/hep-th/9901001") == "hep-th/9901001"
    assert citations.arxiv_id("https://example.test/paper") is None


def test_fetch_uses_aligned_batch_response(monkeypatch):
    requests = []

    def request(url, headers=None, data=None):
        requests.append((url, headers, json.loads(data)))
        return json.dumps([{"citationCount": 12}, None]).encode()

    monkeypatch.setattr(citations.metadata, "request", request)

    result = citations.fetch(["2401.00001", "2401.00002"])

    assert result == {"2401.00001": 12}
    assert requests[0][2] == {"ids": ["ARXIV:2401.00001", "ARXIV:2401.00002"]}


def test_fetch_uses_stored_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    citations.credentials.set_value(citations.credentials.SEMANTIC_SCHOLAR_API_KEY, "stored-key")
    requests = []
    monkeypatch.setattr(
        citations.metadata,
        "request",
        lambda url, headers=None, data=None: requests.append(headers) or b'[{"citationCount":1}]',
    )

    citations.fetch(["2401.00001"])

    assert requests == [{"Content-Type": "application/json", "x-api-key": "stored-key"}]


def test_fetch_rejects_error_document(monkeypatch):
    monkeypatch.setattr(citations.metadata, "request", lambda *args, **kwargs: b'{"error":"bad IDs"}')

    with pytest.raises(TypeError, match="bad IDs"):
        citations.fetch(["2401.00001"])


def test_update_keeps_cached_count_when_fetch_misses(tmp_path, monkeypatch):
    (tmp_path / "entries").mkdir()
    (tmp_path / "entries" / "citations.json").write_text(
        json.dumps({"last_updated": "2026-01-01", "papers": {"2401.00001": 4, "2301.00001": 9}})
    )
    entries = [{"id": "arxiv:2401.00001"}, {"id": "arxiv:2401.00002"}]
    monkeypatch.setattr(citations, "fetch", lambda paper_ids: {"2401.00002": 7})

    document, changed = citations.update(tmp_path, entries, live=True)

    assert document["papers"] == {"2401.00001": 4, "2401.00002": 7}
    assert document["last_updated"]
    assert changed == 2


def test_update_without_fetch_only_prunes_cache(tmp_path, monkeypatch):
    (tmp_path / "entries").mkdir()
    (tmp_path / "entries" / "citations.json").write_text(
        json.dumps({"last_updated": "2026-01-01", "papers": {"2401.00001": 4, "2301.00001": 9}})
    )
    monkeypatch.setattr(
        citations, "fetch", lambda paper_ids: (_ for _ in ()).throw(AssertionError("unexpected fetch"))
    )

    document, changed = citations.update(tmp_path, [{"id": "arxiv:2401.00001"}], live=False)

    assert document == {"last_updated": "2026-01-01", "papers": {"2401.00001": 4}}
    assert changed == 1


def test_update_does_not_overwrite_invalid_cache(tmp_path):
    (tmp_path / "entries").mkdir()
    path = tmp_path / "entries" / "citations.json"
    path.write_text("<<<<<<< merge conflict")

    with pytest.raises(json.JSONDecodeError):
        citations.update(tmp_path, [{"id": "arxiv:2401.00001"}], live=False)

    assert path.read_text() == "<<<<<<< merge conflict"


def test_collect_ignores_non_paper_entries():
    entries = [
        {"kind": "paper", "id": "arxiv:2401.00001"},
        {"kind": "talk", "id": "arxiv:2401.00002"},
        {"kind": "post", "id": "https://example.test/post"},
    ]

    assert citations.collect(entries) == ["2401.00001"]
