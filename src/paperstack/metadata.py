"""Mechanical paper metadata retrieval with provenance and no source selection."""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from filelock import FileLock

from . import credentials

ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
SOURCES = ("semantic_scholar", "dblp", "crossref", "openreview", "acl_anthology", "arxiv")
SEARCH_SOURCES = ("s2", "dblp", "crossref", "openreview", "arxiv")
S2_FIELDS = (
    "paperId,externalIds,title,abstract,venue,year,authors,citationCount,influentialCitationCount,referenceCount,"
    "publicationDate,publicationTypes,fieldsOfStudy,s2FieldsOfStudy,journal,openAccessPdf"
)


@dataclass(frozen=True)
class PaperRef:
    kind: str
    value: str

    @classmethod
    def parse(cls, raw: str) -> PaperRef:
        if ":" not in raw:
            raise ValueError("paper reference needs a prefix: arxiv:, doi:, dblp:, or openreview:")
        kind, value = raw.strip().split(":", 1)
        if kind not in ("arxiv", "doi", "dblp", "openreview") or not value:
            raise ValueError("paper reference needs a prefix: arxiv:, doi:, dblp:, or openreview:")
        if kind == "arxiv":
            value = re.sub(r"v\d+$", "", value)
            modern = re.fullmatch(r"\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}", value)
            legacy = re.fullmatch(r"[A-Za-z][A-Za-z.-]*/\d{2}(?:0[1-9]|1[0-2])\d{3}", value)
            if not (modern or legacy):
                raise ValueError("invalid arXiv reference")
        return cls(kind, value)


_last_request: dict[str, float] = {}
_OPENREVIEW_HOSTS = {"api.openreview.net", "api2.openreview.net"}


@contextmanager
def _request_slot(key: str):
    if key != "openreview":
        yield None
        return
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    root = base / "paperstack" / "http"
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = FileLock(root / "openreview.lock", timeout=120)
        lock.acquire()
    except OSError:
        yield None
        return
    try:
        yield root / "openreview.timestamp"
    finally:
        lock.release()


def _shared_elapsed(path: Path | None) -> float:
    if path is None:
        return float("inf")
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return float("inf")


def _mark_request(path: Path | None) -> None:
    if path is not None:
        try:
            path.touch()
        except OSError:
            pass


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    value = exc.headers.get("Retry-After") if exc.headers else None
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = -1
        if delay >= 0:
            return min(delay, 120)
    return min(2**attempt, 30)


def request(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    data: bytes | None = None,
) -> bytes:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    host = urllib.parse.urlparse(url).netloc
    request_key = "openreview" if host in _OPENREVIEW_HOSTS else host
    interval = 3.0 if "arxiv.org" in host else 1.1 if "dblp.org" in host else 0.5
    request_headers = {
        "User-Agent": "paperstack (+https://github.com/MilkClouds/paperstack)",
        **(headers or {}),
    }
    req = urllib.request.Request(url, headers=request_headers, data=data)
    with _request_slot(request_key) as shared_timestamp:
        for attempt in range(3):
            elapsed = min(
                time.monotonic() - _last_request.get(request_key, 0.0),
                _shared_elapsed(shared_timestamp),
            )
            if elapsed < interval:
                time.sleep(interval - elapsed)
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    _last_request[request_key] = time.monotonic()
                    _mark_request(shared_timestamp)
                    return response.read()
            except urllib.error.HTTPError as exc:
                _last_request[request_key] = time.monotonic()
                _mark_request(shared_timestamp)
                if exc.code != 429:
                    raise
                if attempt == 2:
                    has_api_key = any(name.lower() == "x-api-key" for name in request_headers)
                    if host == "api.semanticscholar.org" and not has_api_key:
                        message = (
                            f"{exc.reason}; configure semantic-scholar.api-key with "
                            "`paperstack config set semantic-scholar.api-key` for more reliable access"
                        )
                        raise urllib.error.HTTPError(exc.url, exc.code, message, exc.headers, exc.fp) from exc
                    raise
                time.sleep(_retry_delay(exc, attempt))
    raise RuntimeError("unreachable request retry state")


def _get_json(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    return json.loads(request(url, params, headers))


def _get_text(url: str, params: dict | None = None) -> str:
    return request(url, params).decode(errors="replace")


def _post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    return json.loads(request(url, headers=request_headers, data=json.dumps(payload).encode()))


def _result(source: str, url: str, response: dict | None = None, error: str | None = None) -> dict:
    out = {"source": source, "url": url, "status": "ok" if error is None else "error"}
    if response is not None:
        out["response"] = response
    if error is not None:
        out["error"] = error
    return out


def _safe(call, source: str, url: str) -> dict:
    try:
        return call()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, ET.ParseError) as exc:
        return _result(source, url, error=str(exc))


def fetch_arxiv(arxiv_id: str) -> dict:
    url = "https://export.arxiv.org/api/query"

    def run() -> dict:
        root = ET.fromstring(_get_text(url, {"id_list": arxiv_id, "max_results": "1"}))
        entry = root.find("atom:entry", ARXIV_NS)
        if entry is None:
            return _result("arxiv", url, error="not found")
        authors = [node.findtext("atom:name", "", ARXIV_NS) for node in entry.findall("atom:author", ARXIV_NS)]
        return _result(
            "arxiv",
            f"https://arxiv.org/abs/{arxiv_id}",
            {
                "id": arxiv_id,
                "title": " ".join((entry.findtext("atom:title", "", ARXIV_NS) or "").split()),
                "authors": authors,
                "published": entry.findtext("atom:published", "", ARXIV_NS),
                "updated": entry.findtext("atom:updated", "", ARXIV_NS),
                "comment": entry.findtext("arxiv:comment", None, ARXIV_NS),
                "categories": [node.get("term") for node in entry.findall("atom:category", ARXIV_NS)],
            },
        )

    return _safe(run, "arxiv", url)


def resolve_s2(ref: PaperRef) -> dict:
    prefix = {"arxiv": "ARXIV", "doi": "DOI", "dblp": "DBLP", "openreview": "URL"}[ref.kind]
    value = f"https://openreview.net/forum?id={ref.value}" if ref.kind == "openreview" else ref.value
    ident = urllib.parse.quote(f"{prefix}:{value}", safe="")
    url = f"https://api.semanticscholar.org/graph/v1/paper/{ident}"
    api_key = credentials.get(credentials.SEMANTIC_SCHOLAR_API_KEY)
    headers = {"x-api-key": api_key} if api_key else {}
    params = {"fields": S2_FIELDS}
    return _safe(lambda: _result("semantic_scholar", url, _get_json(url, params, headers)), "semantic_scholar", url)


def fetch_crossref(doi: str) -> dict:
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}"

    def run() -> dict:
        message = _get_json(url).get("message", {})
        fields = {
            key: message.get(key)
            for key in (
                "title",
                "author",
                "published",
                "container-title",
                "DOI",
                "type",
                "page",
                "volume",
                "issue",
                "publisher",
                "event",
            )
        }
        return _result("crossref", url, fields)

    return _safe(run, "crossref", url)


def fetch_dblp(*, key: str | None, doi: str | None, title: str | None, local_only: bool = False) -> dict:
    from . import dblp_index

    if hits := dblp_index.lookup(key=key, doi=doi):
        return _result("dblp", hits[0]["url"], {"matches": hits, "method": "local_index"})
    if title and (hits := dblp_index.search(title)):
        source = hits[0]["url"] if len(hits) == 1 else str(dblp_index.index_path())
        return _result("dblp", source, {"matches": hits, "method": "local_index"})
    if local_only:
        return {
            "source": "dblp",
            "url": str(dblp_index.index_path()),
            "status": "no_match",
            "reason": "not found in local index",
        }
    if key:
        url = f"https://dblp.org/rec/{key}.bib?param=0"
    elif doi:
        url = f"https://dblp.org/doi/{doi}.bib?param=0"
    elif title:
        return search("dblp", title)
    else:
        return _result("dblp", "https://dblp.org", error="no DBLP key, DOI, or title")
    return _safe(lambda: _result("dblp", url, {"bibtex": _get_text(url).strip()}), "dblp", url)


def fetch_openreview(openreview_id: str) -> dict:
    page = f"https://openreview.net/forum?id={openreview_id}"
    token = os.environ.get("OPENREVIEW_ACCESS_TOKEN")
    headers = {"Cookie": f"openreview.accessToken={token}"} if token else {}
    endpoint = "https://api2.openreview.net/notes/search"
    result = _safe(
        lambda: _result(
            "openreview",
            page,
            _post_json(endpoint, {"ids": [openreview_id], "source": "all", "limit": 10}, headers),
        ),
        "openreview",
        endpoint,
    )
    if result["status"] == "ok" and result.get("response", {}).get("notes"):
        return result
    errors = [result.get("error", f"no note at {endpoint}")]
    endpoint = "https://api.openreview.net/notes"
    result = _safe(
        lambda: _result("openreview", page, _get_json(endpoint, {"id": openreview_id}, headers)),
        "openreview",
        endpoint,
    )
    if result["status"] == "ok" and result.get("response", {}).get("notes"):
        return result
    errors.append(result.get("error", f"no note at {endpoint}"))
    return _result("openreview", page, error="; ".join(errors))


def fetch_acl(acl_id: str) -> dict:
    url = f"https://aclanthology.org/{acl_id}.bib"
    return _safe(lambda: _result("acl_anthology", url, {"bibtex": _get_text(url).strip()}), "acl_anthology", url)


def fetch_all(
    raw_ref: str | PaperRef,
    enabled: set[str] | None = None,
    *,
    local_only: bool = False,
) -> list[dict]:
    ref = raw_ref if isinstance(raw_ref, PaperRef) else PaperRef.parse(raw_ref)
    enabled = enabled or set(SOURCES)
    results: list[dict] = []
    ids = {"doi": None, "arxiv": None, "dblp": None, "openreview": None, "acl": None, "title": None}
    ids[ref.kind] = ref.value
    if ref.kind == "doi" and (match := re.match(r"^10\.18653/v1/(.+)$", ref.value)):
        ids["acl"] = match.group(1)
    available = {
        "arxiv": bool(ids["arxiv"]),
        "crossref": bool(ids["doi"]),
        "dblp": bool(ids["dblp"] or ids["doi"] or ids["title"]),
        "openreview": bool(ids["openreview"]),
        "acl_anthology": bool(ids["acl"]),
    }
    needs_resolution = "semantic_scholar" in enabled or any(
        not available.get(source, False) for source in enabled if source != "semantic_scholar"
    )
    s2 = resolve_s2(ref) if needs_resolution else None
    if "semantic_scholar" in enabled and s2 is not None:
        results.append(s2)
    if s2 is not None and s2["status"] == "ok":
        data = s2.get("response", {})
        external = data.get("externalIds") or {}
        ids.update(
            {
                "doi": ids["doi"] or external.get("DOI"),
                "arxiv": ids["arxiv"] or external.get("ArXiv"),
                "dblp": ids["dblp"] or external.get("DBLP"),
                "acl": ids["acl"] or external.get("ACL"),
                "title": data.get("title"),
            }
        )
    if "dblp" in enabled:
        results.append(
            fetch_dblp(
                key=ids["dblp"],
                doi=ids["doi"],
                title=ids["title"],
                local_only=local_only,
            )
        )
    if "crossref" in enabled and ids["doi"]:
        results.append(fetch_crossref(ids["doi"]))
    elif "crossref" in enabled:
        results.append({"source": "crossref", "status": "unavailable", "reason": "no DOI after discovery"})
    if "openreview" in enabled and ids["openreview"]:
        results.append(fetch_openreview(ids["openreview"]))
    elif "openreview" in enabled:
        results.append({"source": "openreview", "status": "unavailable", "reason": "no OpenReview ID after discovery"})
    if "acl_anthology" in enabled and ids["acl"]:
        results.append(fetch_acl(ids["acl"]))
    elif "acl_anthology" in enabled:
        results.append({"source": "acl_anthology", "status": "unavailable", "reason": "no ACL ID after discovery"})
    if "arxiv" in enabled and ids["arxiv"]:
        results.append(fetch_arxiv(ids["arxiv"]))
    elif "arxiv" in enabled:
        results.append({"source": "arxiv", "status": "unavailable", "reason": "no arXiv ID after discovery"})
    return results


def _content_value(note: dict, name: str):
    value = (note.get("content") or {}).get(name)
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def _normalized_title(value: object) -> str:
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def _openreview_status(note: dict) -> str:
    venue = _content_value(note, "venue") or _content_value(note, "venueid") or _content_value(note, "venue_id")
    fields = [*note.get("invitations", []), venue, _content_value(note, "decision"), _content_value(note, "status")]
    text = " ".join(str(value) for value in fields if value).casefold()
    if "withdraw" in text:
        return "withdrawn"
    if "reject" in text:
        return "rejected"
    if "accept" in text or venue and not any(word in str(venue).casefold() for word in ("submission", "submitted")):
        return "accepted"
    return "submission"


def search(
    source: str,
    query: str,
    *,
    limit: int = 10,
    local_only: bool = False,
    exact_title: bool = False,
    openreview_status: str | None = None,
) -> dict:
    query = query.strip()
    if not query:
        raise ValueError("paper search query is required")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if (exact_title or openreview_status) and source != "openreview":
        raise ValueError("exact title and OpenReview status filters require the openreview source")
    if openreview_status not in (None, "submission", "accepted", "withdrawn"):
        raise ValueError("OpenReview status must be submission, accepted, or withdrawn")
    if source == "dblp":
        from . import dblp_index

        if hits := dblp_index.search(query, limit=limit):
            return _result("dblp", str(dblp_index.index_path()), {"query": query, "matches": hits})
        if local_only:
            return {
                "source": "dblp",
                "url": str(dblp_index.index_path()),
                "status": "no_match",
                "reason": "not found in local index",
            }
        url = "https://dblp.org/search/publ/api"
        has_field_token = re.search(r"(?:^|\s)(?:author|title|venue|year|type|stream|toc):[^:]+:", query)
        remote_query = query if has_field_token else re.sub(r":\s+", " ", query)
        return _safe(
            lambda: _result("dblp", url, _get_json(url, {"q": remote_query, "format": "json", "h": limit})),
            "dblp",
            url,
        )
    if source == "crossref":
        url = "https://api.crossref.org/works"
        return _safe(
            lambda: _result("crossref", url, _get_json(url, {"query.title": query, "rows": limit})),
            "crossref",
            url,
        )
    if source == "arxiv":
        url = "https://export.arxiv.org/api/query"

        def arxiv_search() -> dict:
            root = ET.fromstring(_get_text(url, {"search_query": f'ti:"{query}"', "max_results": limit}))
            matches = [
                {
                    "id": entry.findtext("atom:id", "", ARXIV_NS),
                    "title": " ".join((entry.findtext("atom:title", "", ARXIV_NS) or "").split()),
                    "published": entry.findtext("atom:published", "", ARXIV_NS),
                }
                for entry in root.findall("atom:entry", ARXIV_NS)
            ]
            return _result("arxiv", url, {"query": query, "matches": matches})

        return _safe(arxiv_search, "arxiv", url)
    if source == "openreview":
        token = os.environ.get("OPENREVIEW_ACCESS_TOKEN")
        headers = {"Cookie": f"openreview.accessToken={token}"} if token else {}
        endpoints = (
            ("https://api2.openreview.net/notes/search", True),
            ("https://api.openreview.net/notes/search", False),
        )
        matches = []
        errors = []
        for endpoint, is_v2 in endpoints:
            endpoint_matches = []
            local_filter = bool(openreview_status or (exact_title and not is_v2))
            page_size = min(max(limit * 2, 20), 100) if local_filter else limit
            params = {"limit": page_size, "source": "forum", "cache": "true"}
            if exact_title and is_v2:
                params.update({"term": query, "type": "exact", "content": "title"})
            else:
                params["query"] = query
            for offset in range(0, 1000, page_size):
                page_params = {**params, "offset": offset} if offset else params
                result = _safe(
                    lambda endpoint=endpoint, page_params=page_params: _result(
                        "openreview",
                        endpoint,
                        _get_json(endpoint, page_params, headers),
                    ),
                    "openreview",
                    endpoint,
                )
                if result["status"] != "ok":
                    errors.append(result.get("error", endpoint))
                    break
                notes = result.get("response", {}).get("notes", [])
                matches.extend(notes)
                endpoint_matches.extend(notes)
                if not local_filter or len(notes) < page_size:
                    break
                eligible = endpoint_matches
                if exact_title:
                    wanted = _normalized_title(query)
                    eligible = [
                        note for note in eligible if _normalized_title(_content_value(note, "title")) == wanted
                    ]
                if openreview_status:
                    eligible = [note for note in eligible if _openreview_status(note) == openreview_status]
                eligible_ids = {note.get("forum") or note.get("id") for note in eligible}
                if len(eligible_ids) >= limit:
                    break
        if not matches and errors:
            return _result("openreview", endpoints[0][0], error="; ".join(errors))
        unique = {}
        for note in matches:
            unique[note.get("forum") or note.get("id") or json.dumps(note, sort_keys=True)] = note
        selected = list(unique.values())
        if exact_title:
            wanted = _normalized_title(query)
            selected = [note for note in selected if _normalized_title(_content_value(note, "title")) == wanted]
        if openreview_status:
            selected = [note for note in selected if _openreview_status(note) == openreview_status]
        return _result(
            "openreview",
            endpoints[0][0],
            {
                "query": query,
                "matches": selected[:limit],
                "api_endpoints": [endpoint for endpoint, _ in endpoints],
            },
        )
    if source == "s2":
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        api_key = credentials.get(credentials.SEMANTIC_SCHOLAR_API_KEY)
        headers = {"x-api-key": api_key} if api_key else {}
        params = {"query": query, "limit": limit, "fields": S2_FIELDS}
        return _safe(
            lambda: _result("semantic_scholar", url, _get_json(url, params, headers)), "semantic_scholar", url
        )
    raise ValueError(f"unknown metadata source: {source}")


def print_results(results: list[dict] | dict, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return
    items = results if isinstance(results, list) else [results]
    for item in items:
        print(f"{item['source']}: {item['status']}")
        if item.get("url"):
            print(f"  source: {item['url']}")
        if item.get("reason"):
            print(f"  reason: {item['reason']}")
        if item.get("error"):
            print(f"  error: {item['error']}")
        elif item.get("response") is not None:
            print(json.dumps(item["response"], indent=2, ensure_ascii=False))
