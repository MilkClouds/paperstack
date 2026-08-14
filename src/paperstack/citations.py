"""Batch citation-count updates for paper entries."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from . import metadata

S2_BATCH_API = "https://api.semanticscholar.org/graph/v1/paper/batch"
BATCH_SIZE = 500


def arxiv_id(raw: object) -> str | None:
    """Return an unversioned arXiv ID from a CURIE or arxiv.org URL."""
    value = str(raw or "").strip()
    match = re.fullmatch(r"arxiv:([^?#]+)", value, re.IGNORECASE)
    if not match:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#]+?)(?:\.pdf)?(?:[?#]|$)", value, re.IGNORECASE)
    if not match:
        return None
    value = re.sub(r"v\d+$", "", match.group(1))
    try:
        return metadata.PaperRef.parse(f"arxiv:{value}").value
    except ValueError:
        return None


def collect(entries: list[dict]) -> list[str]:
    return sorted(
        {
            paper_id
            for entry in entries
            if entry.get("kind", "paper") == "paper" and (paper_id := arxiv_id(entry.get("id")))
        }
    )


def fetch(arxiv_ids: list[str]) -> dict[str, int]:
    """Fetch citation counts in aligned Semantic Scholar batches."""
    counts: dict[str, int] = {}
    headers = {"Content-Type": "application/json"}
    if api_key := os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = api_key

    for start in range(0, len(arxiv_ids), BATCH_SIZE):
        batch = arxiv_ids[start : start + BATCH_SIZE]
        query = urllib.parse.urlencode({"fields": "citationCount"})
        payload = json.dumps({"ids": [f"ARXIV:{paper_id}" for paper_id in batch]}).encode()
        response = json.loads(metadata.request(f"{S2_BATCH_API}?{query}", headers=headers, data=payload))
        if not isinstance(response, list):
            detail = response.get("error") if isinstance(response, dict) else None
            raise TypeError(f"unexpected Semantic Scholar batch response{f': {detail}' if detail else ''}")
        for paper_id, paper in zip(batch, response, strict=True):
            if paper is not None and not isinstance(paper, dict):
                raise TypeError("unexpected paper in Semantic Scholar batch response")
            if paper is not None and isinstance(paper.get("citationCount"), int):
                counts[paper_id] = paper["citationCount"]
    return counts


def load(path: Path) -> dict:
    if not path.is_file():
        return {"last_updated": None, "papers": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("papers"), dict):
        raise TypeError(f"{path} must contain a papers object")
    last_updated = value.get("last_updated")
    if last_updated is not None and not isinstance(last_updated, str):
        raise TypeError(f"{path} last_updated must be a string or null")
    papers = value["papers"]
    if any(
        not isinstance(paper_id, str) or not isinstance(count, int) or isinstance(count, bool) or count < 0
        for paper_id, count in papers.items()
    ):
        raise TypeError(f"{path} papers must map IDs to non-negative integers")
    return {"last_updated": last_updated, "papers": papers}


def update(root: Path, entries: list[dict], *, live: bool) -> tuple[dict, int]:
    """Refresh or prune citation data and return the document and change count."""
    path = root / "entries" / "citations.json"
    cached = load(path)
    cached_papers = cached["papers"]
    paper_ids = collect(entries)
    fetched = fetch(paper_ids) if live else {}
    papers = {
        paper_id: fetched.get(paper_id, cached_papers.get(paper_id))
        for paper_id in paper_ids
        if paper_id in fetched or paper_id in cached_papers
    }
    changed = sum(cached_papers.get(paper_id) != count for paper_id, count in papers.items())
    changed += sum(paper_id not in papers for paper_id in cached_papers)
    document = {
        "last_updated": datetime.now(UTC).date().isoformat() if live and fetched else cached["last_updated"],
        "papers": papers,
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document, changed
