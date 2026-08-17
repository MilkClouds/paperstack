"""Bounded arXiv discovery, citation export, cache inspection, and watches."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

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


def _bibtex_escape(value: str) -> str:
    sentinel = "\0BACKSLASH\0"
    return value.replace("\\", sentinel).replace("{", r"\{").replace("}", r"\}").replace(sentinel, r"\textbackslash{}")


def bibtex(record: dict) -> str:
    arxiv_id = record["id"]
    key = "arxiv_" + re.sub(r"[^A-Za-z0-9]+", "_", arxiv_id).strip("_")
    fields = [
        ("title", record.get("title")),
        ("author", " and ".join(record.get("authors") or [])),
        ("year", (record.get("published") or "")[:4]),
        ("eprint", arxiv_id),
        ("archivePrefix", "arXiv"),
        ("primaryClass", record.get("primary_category")),
        ("doi", record.get("doi")),
        ("url", f"https://arxiv.org/abs/{arxiv_id}"),
    ]
    body = ",\n".join(f"  {name} = {{{_bibtex_escape(str(value))}}}" for name, value in fields if value)
    return f"@misc{{{key},\n{body}\n}}"


def export_bibtex(paper_ids: list[str]) -> dict:
    records = []
    for paper_id in paper_ids:
        url = "https://export.arxiv.org/api/query"
        raw = metadata._get_text(url, {"id_list": paper_id, "max_results": 1})
        matches = parse_feed(raw)
        if not matches:
            raise ValueError(f"arXiv paper not found: {paper_id}")
        records.append(matches[0])
    return {
        "source": "arxiv",
        "url": "https://export.arxiv.org/api/query",
        "status": "ok",
        "records": records,
        "bibtex": "\n\n".join(bibtex(record) for record in records),
    }


def cached_papers(root: Path) -> list[dict]:
    if not root.is_dir():
        return []
    records = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        source_files = list((path / "src").rglob("*.tex")) if (path / "src").is_dir() else []
        pdf = path / "paper.md"
        if source_files or pdf.is_file():
            records.append(
                {
                    "id": path.name,
                    "source": bool(source_files),
                    "pdf": pdf.is_file(),
                    "pdf_chars": pdf.stat().st_size if pdf.is_file() else 0,
                }
            )
    return records


def watches_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "paperstack" / "arxiv-watches.json"


def load_watches(path: Path | None = None) -> dict:
    path = path or watches_path()
    if not path.is_file():
        return {"topics": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("topics"), list):
        raise TypeError(f"invalid arXiv watch document: {path}")
    return payload


def save_watches(payload: dict, path: Path | None = None) -> None:
    path = path or watches_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".arxiv-watches-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def add_watch(topic: str, *, categories: list[str] | None = None, limit: int = 10, path: Path | None = None) -> dict:
    _query(topic, categories=categories)
    if not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")
    payload = load_watches(path)
    now = datetime.now(UTC).isoformat()
    current = next((item for item in payload["topics"] if item["topic"] == topic), None)
    record = {
        "topic": topic,
        "categories": categories or [],
        "limit": limit,
        "last_checked": current.get("last_checked") if current else None,
        "created_at": current.get("created_at", now) if current else now,
        "updated_at": now,
    }
    if current:
        payload["topics"][payload["topics"].index(current)] = record
    else:
        payload["topics"].append(record)
    save_watches(payload, path)
    return record


def remove_watch(topic: str, *, path: Path | None = None) -> bool:
    payload = load_watches(path)
    kept = [item for item in payload["topics"] if item["topic"] != topic]
    if len(kept) == len(payload["topics"]):
        return False
    payload["topics"] = kept
    save_watches(payload, path)
    return True


def _newer_than_checkpoint(published: str | None, checkpoint: str | None) -> bool:
    if not checkpoint:
        return True
    if not published:
        return False
    try:
        return datetime.fromisoformat(published) > datetime.fromisoformat(checkpoint)
    except (TypeError, ValueError):
        return False


def check_watches(topic: str | None = None, *, path: Path | None = None) -> dict:
    payload = load_watches(path)
    selected = [item for item in payload["topics"] if topic is None or item["topic"] == topic]
    if topic is not None and not selected:
        raise ValueError(f"unknown arXiv watch: {topic}")
    checked = datetime.now(UTC).isoformat()
    alerts = []
    for record in selected:
        result = search(
            record["topic"],
            categories=record.get("categories") or None,
            date_from=record.get("last_checked"),
            limit=record["limit"],
            sort="date",
        )
        if result["status"] != "ok":
            raise RuntimeError(result.get("error", "arXiv alert search failed"))
        matches = [
            item
            for item in result["response"]["matches"]
            if _newer_than_checkpoint(item.get("published"), record.get("last_checked"))
        ]
        alerts.append(
            {
                "topic": record["topic"],
                "last_checked": record.get("last_checked"),
                "new_paper_count": len(matches),
                "new_papers": matches,
            }
        )
        record["last_checked"] = checked
        record["updated_at"] = checked
    save_watches(payload, path)
    return {"checked_at": checked, "alerts": alerts}
