"""Build the selected-venue DBLP index from DBLP's XML dump."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

import polars as pl

from . import dblp_dump, dblp_index
from .dblp_catalog import VENUES

DUMP_URL = "https://dblp.org/xml/dblp.xml.gz"


def build(
    output: Path,
    *,
    snapshot: str,
    dump: Path,
    selected_venues: Iterable[str] | None = None,
    minimum_records: int = dblp_index.MIN_RECORDS,
) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(output)
    venue_names = list(selected_venues or VENUES)
    unknown = sorted(set(venue_names) - VENUES.keys())
    if unknown:
        raise ValueError(f"unknown DBLP venues: {', '.join(unknown)}")
    rows: dict[str, tuple] = {}
    coverage: dict[str, dict[str, int]] = {}
    for (venue, year), entries in sorted(dblp_dump.load(dump, venue_names).items()):
        for bibtex in entries:
            row = dblp_index._row("", bibtex)
            rows[row[2] or f"title:{row[0]}"] = row
        coverage.setdefault(venue, {})[str(year)] = len(entries)
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
