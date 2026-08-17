"""Bounded Semantic Scholar search, author metadata, and graph traversal."""

from __future__ import annotations

import re
import urllib.parse

from . import credentials, metadata

BASE_URL = "https://api.semanticscholar.org/graph/v1"
SEARCH_FIELDS = (
    "paperId,externalIds,title,abstract,venue,year,authors,citationCount,influentialCitationCount,referenceCount"
)
AUTHOR_FIELDS = "authorId,name,affiliations,citationCount,hIndex,paperCount,url"
GRAPH_FIELDS = "paperId,externalIds,title,year,authors,citationCount,venue"
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*$")
_PREFIXES = {
    "arxiv": "ARXIV",
    "doi": "DOI",
    "corpus": "CorpusId",
    "corpusid": "CorpusId",
    "acl": "ACL",
    "pmid": "PMID",
    "mag": "MAG",
}


def headers() -> dict[str, str]:
    result = {"Accept": "application/json"}
    if key := credentials.get(credentials.SEMANTIC_SCHOLAR_API_KEY):
        result["x-api-key"] = key
    return result


def fields(value: str | None, default: str) -> str:
    items = [item.strip() for item in (value or default).split(",") if item.strip()]
    if not items or len(items) > 50 or any(not _FIELD.fullmatch(item) for item in items):
        raise ValueError("fields must contain 1-50 comma-separated Semantic Scholar field names")
    return ",".join(dict.fromkeys(items))


def identifier(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("paper ID is required")
    if ":" in value:
        prefix, rest = value.split(":", 1)
        if not rest:
            raise ValueError("paper ID suffix is required")
        mapped = _PREFIXES.get(prefix.lower())
        if mapped:
            value = f"{mapped}:{rest}"
        elif prefix.lower() == "s2":
            value = rest
    if len(value) > 500 or any(ord(char) < 32 for char in value):
        raise ValueError("invalid paper ID")
    return urllib.parse.quote(value, safe="")


def _result(url: str, call) -> dict:
    result = metadata._safe(
        lambda: metadata._result("semantic_scholar", url, call()),
        "semantic_scholar",
        url,
    )
    if result["status"] == "error" and "HTTP Error 429" in result.get("error", ""):
        result["error"] = (
            "Semantic Scholar rate limit exceeded (HTTP 429); wait before retrying or run "
            "`paperstack config set semantic-scholar.api-key` for higher limits"
        )
    return result


def _get(url: str, params: dict) -> dict:
    request_headers = headers()
    return _result(url, lambda: metadata._get_json(url, params, request_headers))


def search(
    query: str,
    *,
    selected_fields: str | None = None,
    limit: int = 10,
    offset: int = 0,
    year: str | None = None,
    fields_of_study: list[str] | None = None,
    open_access: bool = False,
) -> dict:
    if not query.strip():
        raise ValueError("Semantic Scholar search query is required")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if not 0 <= offset <= 9999:
        raise ValueError("offset must be between 0 and 9999")
    params: dict[str, str | int] = {
        "query": query.strip(),
        "fields": fields(selected_fields, SEARCH_FIELDS),
        "limit": limit,
        "offset": offset,
    }
    if year:
        if not re.fullmatch(r"\d{4}(?:-\d{4})?", year):
            raise ValueError("year must be YYYY or YYYY-YYYY")
        params["year"] = year
    if fields_of_study:
        if len(fields_of_study) > 20 or any(not item.strip() or len(item) > 100 for item in fields_of_study):
            raise ValueError("field-of-study accepts at most 20 non-empty values")
        params["fieldsOfStudy"] = ",".join(fields_of_study)
    if open_access:
        params["openAccessPdf"] = ""
    url = f"{BASE_URL}/paper/search"
    return _get(url, params)


def authors(
    paper_id: str,
    *,
    selected_fields: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if not 0 <= offset <= 9999:
        raise ValueError("offset must be between 0 and 9999")
    url = f"{BASE_URL}/paper/{identifier(paper_id)}/authors"
    params = {"fields": fields(selected_fields, AUTHOR_FIELDS), "limit": limit, "offset": offset}
    return _get(url, params)


def graph(
    paper_id: str,
    direction: str,
    *,
    selected_fields: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    if direction not in ("citations", "references"):
        raise ValueError("direction must be citations or references")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if not 0 <= offset <= 9999:
        raise ValueError("offset must be between 0 and 9999")
    url = f"{BASE_URL}/paper/{identifier(paper_id)}/{direction}"
    params = {"fields": fields(selected_fields, GRAPH_FIELDS), "limit": limit, "offset": offset}
    result = _get(url, params)
    if result["status"] != "ok":
        return result
    key = "citingPaper" if direction == "citations" else "citedPaper"
    response = result.get("response", {})
    normalized = []
    for item in response.get("data", []):
        paper_data = item.get(key) or {}
        normalized.append(
            {
                "paper_id": paper_data.get("paperId"),
                "external_ids": paper_data.get("externalIds") or {},
                "title": paper_data.get("title"),
                "year": paper_data.get("year"),
                "authors": paper_data.get("authors") or [],
                "citation_count": paper_data.get("citationCount"),
                "venue": paper_data.get("venue"),
                "contexts": item.get("contexts") or [],
                "intents": item.get("intents") or [],
                "is_influential": item.get("isInfluential"),
            }
        )
    result["response"] = {
        "direction": direction,
        "offset": offset,
        "next": response.get("next"),
        "data": normalized,
    }
    return result
