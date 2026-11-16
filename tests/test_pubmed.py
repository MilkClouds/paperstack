import json
import sys
import urllib.error

import pytest

from paperstack import cli, metadata, pubmed
from paperstack.content import pmc

ARTICLE = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
<ArticleTitle>A <i>clinical</i> trial</ArticleTitle><Journal><Title>Medicine</Title>
<JournalIssue><PubDate><MedlineDate>2024 Jan-Feb</MedlineDate></PubDate></JournalIssue></Journal>
<Abstract><AbstractText Label="METHODS">A <b>randomized</b> trial.</AbstractText>
<AbstractText Label="RESULTS">Improved outcomes.</AbstractText></Abstract>
<AuthorList><Author><LastName>Kim</LastName><ForeName>A</ForeName></Author>
<Author><CollectiveName>Trial Group</CollectiveName></Author></AuthorList>
<PublicationTypeList><PublicationType>Clinical Trial</PublicationType></PublicationTypeList></Article>
<MeshHeadingList><MeshHeading><DescriptorName UI="D001" MajorTopicYN="Y">Asthma</DescriptorName>
</MeshHeading></MeshHeadingList><CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn">
<PMID>456</PMID><RefSource>Notice</RefSource></CommentsCorrections></CommentsCorrectionsList>
</MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/trial</ArticleId>
<ArticleId IdType="pmc">PMC789</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""

JATS = b"""<pmc-articleset><article><front><article-meta><article-id pub-id-type="pmc">789</article-id>
<title-group><article-title>A clinical trial</article-title></title-group>
<permissions><license>CC BY</license></permissions><abstract><p>The abstract.</p></abstract>
</article-meta></front><body><p>Body introduction.</p><sec><title>Methods</title><p>Randomized trial.</p>
<sec><title>Participants</title><p>Adults.</p></sec></sec><sec><title>Results</title><p>Improvement.</p>
<table-wrap><label>Table 1</label><table><tbody><tr><th>Group</th><th>Count</th></tr>
<tr><td>A</td><td>10</td></tr></tbody></table></table-wrap><fig><caption><p>Figure caption.</p></caption></fig>
</sec></body><back><ack><p>Thanks.</p></ack><ref-list><ref>Reference citation.</ref></ref-list></back>
</article></pmc-articleset>"""


@pytest.mark.parametrize(
    ("raw", "kind", "value"),
    [
        ("pmid:000123", "pmid", "123"),
        ("pmcid:pmc00789", "pmcid", "PMC789"),
        ("pmcid:789", "pmcid", "PMC789"),
    ],
)
def test_references(raw, kind, value):
    assert metadata.PaperRef.parse(raw) == metadata.PaperRef(kind, value)


@pytest.mark.parametrize("raw", ["pmid:0", "pmid:-1", "pmid:abc", "pmcid:../789", "pmcid:PMC", "pmid:123/4"])
def test_invalid_references(raw):
    with pytest.raises(ValueError):
        metadata.PaperRef.parse(raw)


def test_pubmed_search_preserves_query_and_metadata(monkeypatch):
    calls = []

    def get(url, params):
        calls.append(params)
        return {"esearchresult": {"count": "3", "idlist": ["123", "999"], "querytranslation": "expanded"}}

    monkeypatch.setattr(metadata, "_get_json", get)
    monkeypatch.setattr(metadata, "request", lambda *args: ARTICLE)
    result = pubmed.search("asthma[MeSH Terms] AND clinical trial[pt]", limit=2, offset=1, sort="date")
    assert calls[0]["term"] == "asthma[MeSH Terms] AND clinical trial[pt]"
    assert calls[0]["retstart"] == 1 and calls[0]["sort"] == "pub date"
    data = result["response"]
    assert data["total"] == 3 and data["effective_query"] == "expanded"
    assert data["unavailable_pmids"] == ["999"]
    row = data["matches"][0]
    assert row["title"] == "A clinical trial"
    assert row["abstract"] == "METHODS: A randomized trial.\nRESULTS: Improved outcomes."
    assert row["authors"] == ["A Kim", "Trial Group"]
    assert row["published"] == "2024 Jan-Feb"
    assert row["mesh_terms"][0]["major_topic"] is True
    assert row["notices"][0] == {"type": "RetractionIn", "pmid": "456", "citation": "Notice"}
    assert row["pmcid"] == "PMC789" and row["doi"] == "10.1/trial"


def test_empty_search_does_not_fetch(monkeypatch):
    monkeypatch.setattr(metadata, "_get_json", lambda *args: {"esearchresult": {"count": "0", "idlist": []}})
    monkeypatch.setattr(metadata, "request", lambda *args: pytest.fail("unexpected fetch"))
    assert pubmed.search("missing")["response"]["matches"] == []


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"offset": -1}, {"offset": 9999}, {"sort": "wrong"}])
def test_invalid_search(kwargs):
    with pytest.raises(ValueError):
        pubmed.search("trial", **kwargs)


def test_api_error_and_xml_error(monkeypatch):
    monkeypatch.setattr(metadata, "_get_json", lambda *args: {"error": "Service unavailable"})
    assert pubmed.search("trial")["status"] == "error"
    monkeypatch.setattr(metadata, "request", lambda *args: b"<eFetchResult><ERROR>Invalid ID</ERROR></eFetchResult>")
    assert pubmed.fetch(metadata.PaperRef("pmid", "123"))["status"] == "error"


def test_direct_pubmed_metadata_needs_no_s2(monkeypatch):
    monkeypatch.setattr(metadata, "resolve_s2", lambda *args: pytest.fail("unexpected S2 request"))
    monkeypatch.setattr(metadata, "request", lambda *args: ARTICLE)
    result = metadata.fetch_all("pmid:123", {"pubmed"})
    assert result[0]["response"]["title"] == "A clinical trial"
    assert pubmed.fetch(metadata.PaperRef("pmid", "999"))["status"] == "unavailable"


def test_europe_resolution_requires_exact_identifier(monkeypatch):
    monkeypatch.setattr(
        metadata,
        "_get_json",
        lambda *args: {
            "hitCount": 1,
            "resultList": {"result": [{"id": "123", "source": "MED", "doi": "10.1/other", "pmcid": "PMC789"}]},
        },
    )
    assert pubmed.europe_fetch(metadata.PaperRef("doi", "10.1/trial"))["status"] == "unavailable"
    assert pubmed.europe_fetch(metadata.PaperRef("pmid", "123"))["status"] == "ok"


def test_pmc_fulltext_contains_nested_sections_tables_and_back_matter():
    document = pmc.parse(JATS, "PMC789")
    output = pmc.render({**document, "source_url": "fixture"})
    assert "Group | Count" in output and "A | 10" in output
    assert "Figure caption." in output and "Reference citation." in output and "Thanks." in output
    assert "CC BY" in document["licenses"]
    assert "1.1.1 Participants" in pmc.render(document, outline=True)
    selected = pmc.render({**document, "source_url": "fixture"}, section="1.1")
    assert "Adults." in selected and "Improvement." not in selected


@pytest.mark.parametrize(
    "raw",
    [
        JATS.replace(b">789<", b">790<"),
        JATS.replace(b"<body>", b"<other>").replace(b"</body>", b"</other>"),
        b"<error/>",
    ],
)
def test_pmc_rejects_wrong_or_missing_fulltext(raw):
    with pytest.raises(ValueError):
        pmc.parse(raw, "PMC789")


def test_pmc_fallback_cache_and_offline(monkeypatch, tmp_path):
    calls = []

    def request(url, params):
        calls.append(url)
        if "efetch" in url:
            raise urllib.error.HTTPError(url, 403, "not available", {}, None)
        return JATS

    monkeypatch.setattr(metadata, "request", request)
    ref = metadata.PaperRef.parse("pmcid:PMC789")
    online = pmc.load(ref, tmp_path)
    assert len(calls) == 2 and "fullTextXML" in online["source_url"]
    monkeypatch.setattr(metadata, "request", lambda *args: pytest.fail("offline request"))
    assert pmc.load(ref, tmp_path, offline=True) == online
    with pytest.raises(ValueError, match="cannot be used together"):
        pmc.load(ref, tmp_path, offline=True, refresh=True)
    cached = next((tmp_path / "pmc").glob("*.json"))
    data = json.loads(cached.read_text())
    data["xml"] = data["xml"].replace("Adults.", "Changed.")
    cached.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="no usable cached"):
        pmc.load(ref, tmp_path, offline=True)


def test_cli_pubmed_search_and_pmc_read(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(metadata, "_get_json", lambda *args: {"esearchresult": {"count": "1", "idlist": ["123"]}})
    monkeypatch.setattr(
        metadata, "request", lambda url, params: ARTICLE if params and params.get("db") == "pubmed" else JATS
    )
    monkeypatch.setattr(
        sys, "argv", ["paperstack", "paper", "search", "trial[ti]", "--source", "pubmed", "--json", "--offset", "1"]
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["response"]["matches"][0]["pmid"] == "123"
    monkeypatch.setenv("PAPERSTACK_PAPERS_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["paperstack", "paper", "read", "pmid:123", "--outline"])
    assert cli.main() == 0
    assert "1.1 Methods" in capsys.readouterr().out
    monkeypatch.setattr(metadata, "request", lambda *args: pytest.fail("offline request"))
    monkeypatch.setattr(sys, "argv", ["paperstack", "paper", "read", "pmid:123", "--offline", "--section", "1.2"])
    assert cli.main() == 0
    assert "Improvement." in capsys.readouterr().out


def test_no_pmc_does_not_return_abstract_as_fulltext(monkeypatch, tmp_path):
    monkeypatch.setattr(
        metadata, "request", lambda *args: ARTICLE.replace(b'<ArticleId IdType="pmc">PMC789</ArticleId>', b"")
    )
    monkeypatch.setattr(metadata, "_get_json", lambda *args: {"hitCount": 0, "resultList": {"result": []}})
    with pytest.raises(ValueError, match="No PMC full text"):
        pmc.load(metadata.PaperRef.parse("pmid:123"), tmp_path)


def test_missing_search_payload_is_structured_error(monkeypatch):
    monkeypatch.setattr(metadata, "_get_json", lambda *args: {})
    assert pubmed.search("trial")["status"] == "error"


def test_pubmed_doi_resolution_skips_records_without_pmid(monkeypatch):
    monkeypatch.setattr(
        pubmed,
        "europe_fetch",
        lambda ref: {
            "status": "ok",
            "response": {
                "matches": [
                    {"source": "PMC", "id": "PMC789"},
                    {"source": "MED", "id": "123"},
                ]
            },
        },
    )
    monkeypatch.setattr(metadata, "request", lambda *args: ARTICLE)
    assert pubmed.fetch(metadata.PaperRef("doi", "10.1/trial"))["response"]["pmid"] == "123"


def test_doi_read_and_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pubmed, "europe_fetch", lambda ref: {"status": "ok", "response": {"matches": [{"pmcid": "PMC789"}]}}
    )
    monkeypatch.setattr(metadata, "request", lambda *args: JATS)
    ref = metadata.PaperRef("doi", "10.1/trial")
    original = pmc.load(ref, tmp_path)
    monkeypatch.setattr(metadata, "request", lambda *args: JATS.replace(b"Adults.", b"Children."))
    assert pmc.load(ref, tmp_path) == original
    refreshed = pmc.load(ref, tmp_path, refresh=True)
    assert "Children." in pmc.render(refreshed)
    assert pmc.load(ref, tmp_path, offline=True) == refreshed


@pytest.mark.parametrize("source", ["pubmed", "europe-pmc"])
def test_biomedical_search_offline_never_requests_network(monkeypatch, source):
    monkeypatch.setattr(metadata, "request", lambda *args: pytest.fail("offline request"))
    monkeypatch.setattr(sys, "argv", ["paperstack", "paper", "search", "trial", "--source", source, "--offline"])
    with pytest.raises(SystemExit):
        cli.main()


def test_pmc_keeps_parent_text_after_nested_section_and_includes_floats():
    raw = JATS.replace(b"<p>Adults.</p></sec>", b"<p>Adults.</p></sec><p>After participants.</p>")
    raw = raw.replace(
        b"</article>",
        b"""<floats-group><fig><caption><p>Floating figure caption.</p></caption></fig>
<table-wrap><table><tbody><tr><td>Floating table cell</td></tr></tbody></table></table-wrap>
</floats-group></article>""",
    )
    document = {**pmc.parse(raw, "PMC789"), "source_url": "fixture"}
    rendered = pmc.render(document)
    assert rendered.index("Randomized trial.") < rendered.index("Adults.") < rendered.index("After participants.")
    assert "Floating figure caption." in rendered and "Floating table cell" in rendered
    assert "3 Figures and tables" in pmc.render(document, outline=True)
    selected = pmc.render(document, section="1.1")
    assert selected.index("Adults.") < selected.index("After participants.")
    assert "Improvement." not in selected and "Floating table cell" not in selected
