import hashlib
import json
import sys
import urllib.error
from types import SimpleNamespace

import pytest

from paperstack import cli
from paperstack.content import arxiv_pdf, sci_hub

DOI = "10.1109/jbhi.2021.3117784"
REF = "doi:" + DOI
LANDING = "https://mirror.example/" + DOI
HTML = f'''<html><meta name="citation_doi" content="{DOI}">
<meta name="citation_pdf_url" content="/storage/paper.pdf#view=FitH">
<object data="/storage/paper.pdf#view=FitH"></object></html>'''


def run(monkeypatch, tmp_path, *args):
    monkeypatch.setenv("PAPERSTACK_PAPERS_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["paperstack", "paper", *args])
    return cli.main()


def test_metadata_pdf_has_provenance_and_no_unverified_pdf_claim():
    result = sci_hub.parse_page(HTML, LANDING, sci_hub.reference(REF))
    assert result["response"]["pdf_url"] == "https://mirror.example/storage/paper.pdf"
    assert result["response"]["landing_url"] == LANDING
    assert result["response"]["identity_check"] == "provider_page"
    assert not result["response"]["pdf_identity_verified"]


@pytest.mark.parametrize(
    "tag",
    [
        '<iframe src="/storage/paper.pdf#view=FitH">',
        '<embed src="//cdn.example/paper.pdf">',
        '<a href="paper.pdf">Download</a>',
    ],
)
def test_scinet_and_legacy_pdf_elements(tag):
    html = f'<a href="https://doi.org/{DOI}">{DOI}</a>{tag}'
    result = sci_hub.parse_page(html, "https://sci-net.xyz/article/123", sci_hub.reference(REF))
    assert result["status"] == "ok"
    assert result["response"]["pdf_url"].startswith("https://")
    assert "#" not in result["response"]["pdf_url"]


@pytest.mark.parametrize(
    ("html", "status"),
    [
        ("I do not have any papers matching your request :(", "unavailable"),
        ("Alas, the following paper is not yet available in my database", "unavailable"),
        ('<div translate="ko:isrobot">로봇 확인</div>', "blocked"),
        ("Are you a robot?", "blocked"),
        ('<script src="/cf-chl-test"></script>', "blocked"),
        ("An unexpected landing page", "error"),
        ('<meta name="citation_doi" content="10.1234/other"><iframe src="paper.pdf">', "error"),
        ('<iframe src="http://insecure.example/paper.pdf">', "error"),
        ('<iframe src="https://user:password@example.com/paper.pdf">', "error"),
    ],
)
def test_misses_challenges_and_wrong_identity(html, status):
    assert sci_hub.parse_page(html, LANDING, sci_hub.reference(REF))["status"] == status


def test_unidentified_redirect_does_not_resolve_pdf():
    result = sci_hub.parse_page('<iframe src="paper.pdf">', "https://mirror.example/unrelated", sci_hub.reference(REF))
    assert "identity_unverified" in result["reason"]


@pytest.mark.parametrize("raw", ["pmid:123", "arxiv:2410.24164", "doi:abc", "doi:10.1234/with space"])
def test_invalid_reference(raw):
    with pytest.raises(ValueError):
        sci_hub.reference(raw)


@pytest.mark.parametrize(
    "url",
    [
        "http://mirror.example",
        "https://user@mirror.example",
        "https://mirror.example/path",
        "https://mirror.example?x=1",
    ],
)
def test_invalid_mirror(url):
    with pytest.raises(ValueError):
        sci_hub.mirror_url(url)


def test_cached_lookup_offline_refresh_and_mirror_isolation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sci_hub, "_fetch", lambda url: calls.append(url) or (HTML, LANDING))
    online = sci_hub.locate(REF.upper().replace("DOI:", "doi:"), tmp_path, mirror="https://mirror.example")
    assert calls == [LANDING]
    assert sci_hub.locate(REF, tmp_path, mirror="https://mirror.example", offline=True) == online
    assert sci_hub.locate(REF, tmp_path, mirror="https://other.example", offline=True)["status"] == "unavailable"
    sci_hub.locate(REF, tmp_path, mirror="https://mirror.example", refresh=True)
    assert len(calls) == 2
    with pytest.raises(ValueError, match="cannot be used together"):
        sci_hub.locate(REF, tmp_path, offline=True, refresh=True)
    cached = next((tmp_path / "sci-hub").glob("*.json"))
    cached.write_text("{}")
    assert sci_hub.locate(REF, tmp_path, mirror="https://mirror.example", offline=True)["status"] == "unavailable"
    assert len(calls) == 2


def test_failures_are_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(sci_hub, "_fetch", lambda url: ("Are you a robot?", LANDING))
    assert sci_hub.locate(REF, tmp_path)["status"] == "blocked"
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("code", [403, 418, 429])
def test_http_blocks_remain_distinct_from_missing(tmp_path, monkeypatch, code):
    def fetch(url):
        raise urllib.error.HTTPError(url, code, "Blocked", {}, None)

    monkeypatch.setattr(sci_hub, "_fetch", fetch)
    assert sci_hub.locate(REF, tmp_path)["status"] == "blocked"


def test_environment_and_explicit_mirror(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("PAPERSTACK_SCI_HUB_URL", "https://env.example")
    monkeypatch.setattr(sci_hub, "_fetch", lambda url: calls.append(url) or ("not yet available", url))
    sci_hub.locate(REF, tmp_path)
    sci_hub.locate(REF, tmp_path, mirror="https://explicit.example")
    assert calls == ["https://env.example/" + DOI, "https://explicit.example/" + DOI]


def test_cli_read_converts_and_reuses_verified_cache_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sci_hub, "_fetch", lambda url: (HTML, LANDING))
    converted = []

    def convert(key, **kwargs):
        converted.append(kwargs)
        directory = tmp_path / key
        directory.mkdir(parents=True)
        pdf, md = b"%PDF-test", b"Paper content " * 30
        (directory / "paper.pdf").write_bytes(pdf)
        (directory / "paper.md").write_bytes(md)
        (directory / "meta.json").write_text(
            json.dumps(
                {
                    "converter": "pdf-inspector",
                    "quality": "complete",
                    "bytes": len(md),
                    "sha256": hashlib.sha256(md).hexdigest(),
                    "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
                }
            )
        )
        return True

    monkeypatch.setattr(arxiv_pdf, "convert", convert)
    assert run(monkeypatch, tmp_path, "read", REF, "--source", "sci-hub", "--max-chars", "13") == 0
    assert capsys.readouterr().out.strip() == "Paper content"
    assert converted[0]["provenance"]["landing_url"] == LANDING
    assert converted[0]["paper_ref"] == REF
    monkeypatch.setattr(sci_hub, "_fetch", lambda url: pytest.fail("offline network request"))
    assert run(monkeypatch, tmp_path, "read", REF, "--source", "sci-hub", "--offline") == 0
    assert len(converted) == 1
    (next(tmp_path.glob("sci-hub-external-*")) / "paper.pdf").write_bytes(b"corrupted")
    with pytest.raises(SystemExit):
        run(monkeypatch, tmp_path, "read", REF, "--source", "sci-hub", "--offline")


@pytest.mark.parametrize(
    "flags", [["--outline"], ["--pdf-url", "https://example.com/a.pdf"], ["--refresh", "--offline"]]
)
def test_conflicts_fail_before_network(tmp_path, monkeypatch, flags):
    monkeypatch.setattr(sci_hub, "_fetch", lambda url: pytest.fail("unexpected network"))
    with pytest.raises(SystemExit):
        run(monkeypatch, tmp_path, "read", REF, "--source", "sci-hub", *flags)


def test_default_doi_read_does_not_call_sci_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(sci_hub, "locate", lambda *args, **kwargs: pytest.fail("implicit Sci-Hub lookup"))
    with pytest.raises(SystemExit):
        run(monkeypatch, tmp_path, "read", REF)


def test_pdf_conversion_persists_retrieval_provenance(tmp_path, monkeypatch):
    from test_arxiv_pdf import _native_result

    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(arxiv_pdf, "_fetch", lambda url: b"%PDF-test")
    monkeypatch.setitem(
        sys.modules, "pdf_inspector", SimpleNamespace(process_pdf_with_ocr=lambda *a, **k: _native_result())
    )
    provenance = {"source": "sci_hub", "landing_url": LANDING, "doi": DOI}
    assert arxiv_pdf.convert("fixture", url="https://example.com/paper.pdf", paper_ref=REF, provenance=provenance)
    meta = json.loads((tmp_path / "fixture" / "meta.json").read_text())
    assert meta["retrieval"] == provenance
    assert meta["paper_ref"] == REF
    assert "arxiv_id" not in meta


def test_redirected_http_failure_preserves_browser_landing_url(tmp_path, monkeypatch):
    landing = "https://sci-net.xyz/" + DOI

    def fetch(url):
        raise urllib.error.HTTPError(landing, 403, "Forbidden", {}, None)

    monkeypatch.setattr(sci_hub, "_fetch", fetch)
    result = sci_hub.locate(REF, tmp_path)
    assert result["status"] == "blocked"
    assert result["url"] == landing
