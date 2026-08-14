"""Build a selected-venue DBLP index directly from DBLP."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.error import URLError

import polars as pl

from . import dblp_index
from .dblp_catalog import VENUES, toc_query, years

PAGE_SIZE = 100
MAX_PAGES = 80
RETRIES = 5


def _parse_entries(text: str) -> list[str]:
    starts = list(re.finditer(r"(?m)^@\w+\{", text))
    return [text[item.start() : starts[index + 1].start()].strip() for index, item in enumerate(starts[:-1])] + (
        [text[starts[-1].start() :].strip()] if starts else []
    )


def _get(url: str, params: dict[str, str]) -> str:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "paperstack (+https://github.com/MilkClouds/paperstack)"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read().decode()


def _fetch_query(
    query: str,
    *,
    base_url: str,
    get: Callable[[str, dict[str, str]], str] = _get,
    delay: float = 0.0,
) -> list[str]:
    entries: list[str] = []
    endpoint = f"{base_url.rstrip('/')}/search/publ/api"
    for page in range(MAX_PAGES):
        error: Exception | None = None
        for attempt in range(RETRIES):
            try:
                text = get(
                    endpoint,
                    {"q": query, "h": str(PAGE_SIZE), "f": str(page * PAGE_SIZE), "format": "bib"},
                )
                break
            except (OSError, TimeoutError, URLError) as exc:
                error = exc
                if attempt + 1 < RETRIES:
                    time.sleep(min(2**attempt, 8))
        else:
            raise RuntimeError(f"DBLP query failed after {RETRIES} attempts: {query}") from error
        parsed = _parse_entries(text)
        if not parsed:
            break
        entries.extend(parsed)
        if delay:
            time.sleep(delay)
    else:
        raise RuntimeError(f"DBLP query exceeded {MAX_PAGES} pages: {query}")
    return entries


def fetch_venue_year(
    venue: str,
    year: int,
    *,
    base_url: str = "https://dblp.org",
    get: Callable[[str, dict[str, str]], str] = _get,
    delay: float = 0.0,
) -> list[str]:
    definition = VENUES[venue]
    entries = _fetch_query(toc_query(venue, year), base_url=base_url, get=get, delay=delay)
    if not entries and (search_venue := definition.get("search_venue")):
        entries = _fetch_query(f"venue:{search_venue}: year:{year}:", base_url=base_url, get=get, delay=delay)
    if not entries:
        for suffix in definition.get("suffixes", []):
            if suffix and (entries := _fetch_query(toc_query(venue, year, suffix), base_url=base_url, get=get)):
                break
    if not entries and definition.get("type") != "journals":
        for part in range(1, 50):
            query = toc_query(venue, year).replace(".bht:", f"-{part}.bht:")
            part_entries = _fetch_query(query, base_url=base_url, get=get, delay=delay)
            if not part_entries:
                break
            entries.extend(part_entries)
    for suffix in definition.get("extra_tocs", []):
        entries.extend(_fetch_query(toc_query(venue, year, suffix), base_url=base_url, get=get, delay=delay))
    return entries


def _base_rows(base: Path | None) -> dict[str, tuple]:
    if base is None:
        return {}
    if pl.read_parquet_schema(base) != dblp_index.SCHEMA:
        raise RuntimeError("base DBLP Parquet schema mismatch")
    return {row[2] or f"title:{row[0]}": row for row in pl.read_parquet(base).select(dblp_index.COLUMNS).iter_rows()}


def _replace_venue_year(rows: dict[str, tuple], venue: str, year: int) -> None:
    definition = VENUES[venue]
    namespace = "journals" if definition.get("type") == "journals" else "conf"
    prefix = f"DBLP:{namespace}/{definition['dir']}/"
    stale = [key for key, row in rows.items() if str(row[2] or "").startswith(prefix) and row[5] == year]
    for key in stale:
        del rows[key]


def build(
    output: Path,
    *,
    snapshot: str,
    base: Path | None = None,
    selected_venues: Iterable[str] | None = None,
    selected_years: Iterable[int] | None = None,
    base_url: str = "https://dblp.org",
    minimum_records: int = dblp_index.MIN_RECORDS,
    delay: float = 0.0,
    fetch: Callable[..., list[str]] = fetch_venue_year,
) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(output)
    venue_names = list(selected_venues or VENUES)
    unknown = sorted(set(venue_names) - VENUES.keys())
    if unknown:
        raise ValueError(f"unknown DBLP venues: {', '.join(unknown)}")
    year_filter = set(selected_years) if selected_years is not None else None
    rows = _base_rows(base)
    coverage: dict[str, dict[str, int]] = {}
    for venue in venue_names:
        for year in years(venue):
            if year_filter is not None and year not in year_filter:
                continue
            bibtex_entries = fetch(venue, year, base_url=base_url, delay=delay)
            if not bibtex_entries:
                continue
            _replace_venue_year(rows, venue, year)
            for bibtex in bibtex_entries:
                row = dblp_index._row("", bibtex)
                rows[row[2] or f"title:{row[0]}"] = row
            coverage.setdefault(venue, {})[str(year)] = len(bibtex_entries)
    if len(rows) < minimum_records:
        raise RuntimeError(f"only {len(rows)} records; expected at least {minimum_records}")
    release_url = (
        f"https://github.com/{dblp_index.RELEASE_REPO}/releases/download/dblp-index-{snapshot}/{dblp_index.ASSET_NAME}"
    )
    metadata = {
        "snapshot": snapshot,
        "source": release_url,
        "records": str(len(rows)),
        "minimum_records": str(minimum_records),
        "coverage": "selected CS venues",
        "schema_version": dblp_index.SCHEMA_VERSION,
        "venue_catalog": json.dumps(coverage, sort_keys=True, separators=(",", ":")),
    }
    staged = output.with_name(f"{output.name}.new")
    staged.unlink(missing_ok=True)
    try:
        frame = pl.DataFrame(list(rows.values()), schema=dblp_index.SCHEMA, orient="row").sort("dblp_key")
        frame.write_parquet(
            staged,
            compression="zstd",
            compression_level=9,
            statistics="full",
            row_group_size=16_384,
            metadata=metadata,
        )
        dblp_index._validate_index(
            staged,
            dblp_index.Snapshot(snapshot, release_url, "", minimum_records=minimum_records),
        )
        os.replace(staged, output)
    finally:
        staged.unlink(missing_ok=True)
    return {"path": str(output), "records": len(rows), "refreshed": coverage}
