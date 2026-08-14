# DBLP index releases

Paperstack owns the venue catalog, DBLP synchronization, Parquet builder, validation, installation, and lookup. A
snapshot has one repository-owned asset: `dblp.parquet` in a `MilkClouds/paperstack` Release.

## Incremental monthly refresh

Download the previous Parquet asset, then re-fetch recent venue-years directly from DBLP. A successful venue-year
fetch replaces that slice before merging by DBLP key, so upstream corrections and deletions do not leave stale rows:

```bash
gh release download dblp-index-PREVIOUS --pattern dblp.parquet --dir /tmp/dblp-previous \
  --repo MilkClouds/paperstack
uv run paperstack index dblp build /tmp/dblp.parquet \
  --snapshot YYYY.MM \
  --base /tmp/dblp-previous/dblp.parquet \
  --year 2025 --year 2026
sha256sum /tmp/dblp.parquet
gh release create dblp-index-YYYY.MM.DD /tmp/dblp.parquet \
  --repo MilkClouds/paperstack \
  --title "DBLP index — YYYY.MM.DD"
```

Use `--venue` repeatedly to refresh only selected venues. Omit `--venue` to use the repository-owned catalog in
`src/paperstack/dblp_catalog.py`. A recent year that DBLP has not ingested returns no records and leaves the base
snapshot unchanged. Re-run recent years every release because DBLP can add proceedings after the first successful
fetch.

## Full rebuild

Omit `--base` and year filters to fetch every configured venue-year directly from DBLP:

```bash
uv run paperstack index dblp build /tmp/dblp.parquet --snapshot YYYY.MM
```

This is intentionally slower and serves as a reproducibility and disaster-recovery path. `--base-url` can select a
DBLP mirror if the primary endpoint rate-limits a bulk build. Add or change venues only in
`src/paperstack/dblp_catalog.py`; no external repository or intermediate archive is part of the release chain.

The Parquet builder stores only structured lookup fields, Zstd-compresses the file, and embeds its snapshot, source
URL, schema version, coverage, refreshed venue-year counts, and record count. It does not store raw BibTeX. The
builder and installer reject schema or metadata mismatches and fewer than 250,000 records.

Installation and update use public GitHub Release URLs and do not require `gh`. Local assets are immutable and named
by SHA-256; `current.json` is replaced atomically.

Verify the Release asset digest, a clean `paperstack index dblp install`, and an older installation followed by
`paperstack index dblp update`. Update `SNAPSHOT`, `INDEX_URL`, and `INDEX_SHA256` for new installs; existing clients
discover the newest paperstack Release through `update`.
