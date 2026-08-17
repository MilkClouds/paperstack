"""Bounded arXiv discovery with source-backed metadata."""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from . import metadata

ATOM = "http://www.w3.org/2005/Atom"
ARXIV = "http://arxiv.org/schemas/atom"
NS = {"atom": ATOM, "arxiv": ARXIV}
MAX_RESULTS = 100
_CATEGORY = re.compile(r"^[a-z-]+(?:\.[A-Za-z-]+)?$")


def _date(value: str, *, end: bool = False) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid date {value!r}; use YYYY-MM-DD or ISO 8601") from exc
    if len(value) == 10:
        parsed = parsed.replace(hour=23 if end else 0, minute=59 if end else 0)
    elif parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y%m%d%H%M")


def _query(
    query: str,
    *,
    categories: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    parts = []
    if query.strip():
        parts.append(f"({query.strip()})")
    if categories:
        invalid = [item for item in categories if not _CATEGORY.fullmatch(item)]
        if invalid:
            raise ValueError(f"invalid arXiv category: {invalid[0]}")
        parts.append("(" + " OR ".join(f"cat:{item}" for item in categories) + ")")
    if date_from or date_to:
        start = _date(date_from) if date_from else "199107010000"
        end = _date(date_to, end=True) if date_to else datetime.now(UTC).strftime("%Y%m%d%H%M")
        if start > end:
            raise ValueError("date-from must not be after date-to")
        parts.append(f"submittedDate:[{start}+TO+{end}]")
    if not parts:
        raise ValueError("arXiv search needs a query, category, or date range")
    return " AND ".join(parts)


def _text(entry: ET.Element, name: str) -> str | None:
    value = entry.findtext(name, None, NS)
    return " ".join(value.split()) if value else None


def _entry(entry: ET.Element) -> dict:
    raw_id = _text(entry, "atom:id") or ""
    versioned = raw_id.rsplit("/abs/", 1)[-1]
    arxiv_id = re.sub(r"v\d+$", "", versioned)
    links = {node.get("title") or node.get("rel"): node.get("href") for node in entry.findall("atom:link", NS)}
    categories = [node.get("term") for node in entry.findall("atom:category", NS) if node.get("term")]
    primary = entry.find("arxiv:primary_category", NS)
    authors = [_text(node, "atom:name") for node in entry.findall("atom:author", NS)]
    return {
        "id": arxiv_id,
        "versioned_id": versioned,
        "title": _text(entry, "atom:title"),
        "authors": [author for author in authors if author],
        "abstract": _text(entry, "atom:summary"),
        "categories": categories,
        "primary_category": primary.get("term") if primary is not None else (categories[0] if categories else None),
        "published": _text(entry, "atom:published"),
        "updated": _text(entry, "atom:updated"),
        "comment": _text(entry, "arxiv:comment"),
        "journal_ref": _text(entry, "arxiv:journal_ref"),
        "doi": _text(entry, "arxiv:doi"),
        "url": f"https://arxiv.org/abs/{versioned}",
        "pdf_url": links.get("pdf") or f"https://arxiv.org/pdf/{versioned}",
    }


def parse_feed(raw: bytes | str) -> list[dict]:
    root = ET.fromstring(raw)
    return [_entry(entry) for entry in root.findall("atom:entry", NS)]


def search(
    query: str,
    *,
    categories: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 10,
    sort: str = "relevance",
) -> dict:
    if not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")
    if sort not in ("relevance", "date"):
        raise ValueError("sort must be relevance or date")
    built = _query(query, categories=categories, date_from=date_from, date_to=date_to)
    params = urllib.parse.urlencode(
        {
            "search_query": built,
            "max_results": limit,
            "sortBy": "submittedDate" if sort == "date" else "relevance",
            "sortOrder": "descending",
        }
    )
    params = params.replace("%2BTO%2B", "+TO+")
    url = f"https://export.arxiv.org/api/query?{params}"
    return metadata._safe(
        lambda: metadata._result(
            "arxiv",
            url,
            {"query": built, "matches": parse_feed(metadata.request(url))},
        ),
        "arxiv",
        url,
    )
