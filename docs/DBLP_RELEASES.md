# DBLP index releases

Paperstack owns the venue catalog, DBLP synchronization, Parquet builder, validation, installation, and lookup. A
snapshot has one repository-owned asset: `dblp.parquet` in a `MilkClouds/paperstack` Release.

## Monthly release

DBLP serves its search API and record pages behind a browser proof-of-work check, and its FAQ asks bulk users to
download the XML dump and query it locally. The `DBLP index` workflow (`.github/workflows/dblp-index.yml`) runs on
the 2nd of each month and on demand: it downloads `https://dblp.org/xml/dblp.xml.gz`, builds every catalog venue-year
from it and publishes `dblp-index-YYYY.MM.DD`. The same build runs locally:

```bash
curl -o /tmp/dblp.xml.gz https://dblp.org/xml/dblp.xml.gz
uv run paperstack index dblp build /tmp/dblp.parquet --snapshot YYYY.MM.DD --dump /tmp/dblp.xml.gz
uv run paperstack index dblp install-file /tmp/dblp.parquet
```

Records are matched to the catalog by their table-of-contents page, so workshop and companion volumes stay out; a
build takes about three minutes and 1.5 GB of memory. Add or change venues only in `src/paperstack/dblp_catalog.py`.

The Parquet builder stores only structured lookup fields, Zstd-compresses the file, and embeds its snapshot, source
URL, schema version, coverage, refreshed venue-year counts, and record count. It does not store raw BibTeX. The
builder and installer reject schema or metadata mismatches and fewer than 250,000 records.

Installation and update use public GitHub Release URLs and do not require `gh`. Local assets are immutable and named
by SHA-256; `current.json` is replaced atomically.

`install` fetches the snapshot pinned in `SNAPSHOT`, `INDEX_URL`, and `INDEX_SHA256`; `update` discovers the newest
Release, so monthly releases reach clients without a code change. Bump the pin when a release should become the
default for new installs.
