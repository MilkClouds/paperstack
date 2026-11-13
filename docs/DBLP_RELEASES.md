# DBLP index releases

Paperstack owns the venue catalog, DBLP synchronization, Parquet builder, validation, installation, and lookup. A
snapshot has one repository-owned asset: `dblp.parquet` in a `MilkClouds/paperstack` Release.

## Monthly rebuild from the XML dump

DBLP serves its search API and record pages behind a browser proof-of-work check, and its FAQ asks bulk users to
download the XML dump and query it locally. Rebuild every configured venue-year from the dump:

```bash
curl -o /tmp/dblp.xml.gz https://dblp.org/xml/dblp.xml.gz
uv run paperstack index dblp build /tmp/dblp.parquet --snapshot YYYY.MM.DD --dump /tmp/dblp.xml.gz
sha256sum /tmp/dblp.parquet
gh release create dblp-index-YYYY.MM.DD /tmp/dblp.parquet \
  --repo MilkClouds/paperstack \
  --title "DBLP index — YYYY.MM.DD"
```

Records are matched to the catalog by their table-of-contents page, so workshop and companion volumes stay out.
Dump fields are Unicode and unwrapped, while API BibTeX uses LaTeX escapes and line wrapping; rebuild from the dump
rather than merging it into an API-built `--base`, so one snapshot has one field style.

## Search API refresh

Without `--dump`, the builder queries DBLP's search API per venue-year, optionally merging into `--base` and limited by
`--venue` and `--year`. A recent year that DBLP has not ingested returns no records and leaves the base unchanged.
This path works only while the API answers non-browser clients; `--base-url` selects a mirror. Add or change venues
only in `src/paperstack/dblp_catalog.py`.

The Parquet builder stores only structured lookup fields, Zstd-compresses the file, and embeds its snapshot, source
URL, schema version, coverage, refreshed venue-year counts, and record count. It does not store raw BibTeX. The
builder and installer reject schema or metadata mismatches and fewer than 250,000 records.

Installation and update use public GitHub Release URLs and do not require `gh`. Local assets are immutable and named
by SHA-256; `current.json` is replaced atomically.

Verify the Release asset digest, a clean `paperstack index dblp install`, and an older installation followed by
`paperstack index dblp update`. Update `SNAPSHOT`, `INDEX_URL`, and `INDEX_SHA256` for new installs; existing clients
discover the newest paperstack Release through `update`.
