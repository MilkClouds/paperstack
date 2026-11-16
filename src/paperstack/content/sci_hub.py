"""Explicit Sci-Hub DOI lookup with cached provenance; no automatic fallback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from ..metadata import PaperRef

DEFAULT_URL = "https://sci-hub.ru"
MAX_HTML_BYTES = 2 * 1024 * 1024


def _https(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Sci-Hub URLs must use HTTPS without credentials")
    return urllib.parse.urldefrag(url)[0]


def mirror_url(value: str | None = None) -> str:
    url = _https(value or os.environ.get("PAPERSTACK_SCI_HUB_URL") or DEFAULT_URL)
    parsed = urllib.parse.urlsplit(url)
    if parsed.path not in ("", "/") or parsed.query:
        raise ValueError("Sci-Hub mirror must be an HTTPS origin, without a path or query")
    return url.rstrip("/")


def reference(raw: str | PaperRef) -> PaperRef:
    ref = PaperRef.parse(raw) if isinstance(raw, str) else raw
    if ref.kind != "doi" or not re.fullmatch(r"10\.[0-9]{4,9}/[^\s]+", ref.value):
        raise ValueError("Sci-Hub lookup requires a doi:10.NNNN/... reference")
    return PaperRef("doi", ref.value.lower())


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pdfs: list[str] = []
        self.dois: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            name = attrs.get("name", "").lower()
            if name == "citation_doi" and attrs.get("content"):
                self.dois.append(attrs["content"].strip().lower())
            if name == "citation_pdf_url" and attrs.get("content"):
                self.pdfs.insert(0, attrs["content"])
        value = attrs.get({"object": "data", "embed": "src", "iframe": "src", "a": "href"}.get(tag, ""))
        if value:
            if urllib.parse.urlsplit(value).path.lower().endswith(".pdf"):
                self.pdfs.append(value)
            if tag == "a" and urllib.parse.urlsplit(value).hostname in ("doi.org", "dx.doi.org"):
                self.dois.append(urllib.parse.unquote(urllib.parse.urlsplit(value).path).lstrip("/").lower())

    def handle_data(self, data):
        self.text.append(data)


def _result(status, url, *, reason=None, response=None):
    result = {"source": "sci_hub", "status": status, "url": url}
    if reason:
        result["reason"] = reason
    if response:
        result["response"] = response
    return result


def parse_page(html: str, page_url: str, ref: PaperRef) -> dict:
    page_url = _https(page_url)
    page = _Page()
    page.feed(html)
    if page.dois and ref.value not in page.dois:
        return _result("error", page_url, reason="identity_mismatch: page DOI differs from requested DOI")
    final_doi = urllib.parse.unquote(urllib.parse.urlsplit(page_url).path).lstrip("/").lower()
    for candidate in page.pdfs:
        try:
            pdf_url = _https(urllib.parse.urljoin(page_url, candidate))
        except ValueError:
            continue
        if ref.value not in page.dois and final_doi != ref.value:
            return _result("error", page_url, reason="identity_unverified: page does not identify the requested DOI")
        return _result(
            "ok",
            page_url,
            response={
                "doi": ref.value,
                "pdf_url": pdf_url,
                "landing_url": page_url,
                "identity_check": "provider_page",
                "pdf_identity_verified": False,
            },
        )
    text = " ".join(page.text).casefold()
    if any(marker in text for marker in ("not have any papers", "not yet available", "article not found")):
        return _result("unavailable", page_url, reason="not_found: provider has no available paper for this DOI")
    if any(
        marker in html.casefold()
        for marker in (
            "captcha",
            "cf-chl-",
            "ko:isrobot",
            "are you a robot",
            "verify you are human",
            "unusual traffic",
        )
    ):
        return _result("blocked", page_url, reason="browser_verification: open the landing URL in a browser")
    return _result("error", page_url, reason="unrecognized_page: no supported PDF link or availability response")


def _fetch(url: str) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; paperstack)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(MAX_HTML_BYTES + 1)
        if len(raw) > MAX_HTML_BYTES:
            raise ValueError("Sci-Hub landing page exceeds the 2 MiB limit")
        return raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace"), response.geturl()


def locate(raw: str | PaperRef, cache: Path, *, mirror=None, offline=False, refresh=False) -> dict:
    ref = reference(raw)
    mirror = mirror_url(mirror)
    if offline and refresh:
        raise ValueError("--offline and --refresh cannot be used together")
    identity = f"doi:{ref.value}"
    key = hashlib.sha256(f"{identity}\n{mirror}".encode()).hexdigest()
    path = cache / "sci-hub" / f"{key}.json"
    url = mirror + "/" + urllib.parse.quote(ref.value, safe="/")
    if not refresh and path.is_file():
        try:
            saved = json.loads(path.read_text())
            result = saved["result"]
            data = result["response"]
            valid = (
                saved["version"] == 1
                and saved["reference"] == identity
                and saved["mirror"] == mirror
                and result["status"] == "ok"
                and result["source"] == "sci_hub"
                and data["doi"] == ref.value
            )
            if valid:
                _https(data["pdf_url"])
                _https(data["landing_url"])
                return result
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if offline:
        return _result("unavailable", url, reason="cache_miss: no valid cached Sci-Hub lookup; run online first")
    try:
        html, final_url = _fetch(url)
        result = parse_page(html, final_url, ref)
    except urllib.error.HTTPError as exc:
        status = "blocked" if exc.code in (401, 403, 418, 429) else "error"
        return _result(status, exc.geturl() or url, reason=f"http_error: HTTP {exc.code} {exc.reason}")
    except (OSError, ValueError) as exc:
        return _result("error", url, reason=f"lookup_failed: {exc}")
    if result["status"] == "ok":
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump({"version": 1, "reference": identity, "mirror": mirror, "result": result}, handle)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return result
