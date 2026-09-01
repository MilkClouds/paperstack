# Paperstack

[![CI](https://github.com/MilkClouds/paperstack/actions/workflows/ci.yml/badge.svg)](https://github.com/MilkClouds/paperstack/actions/workflows/ci.yml) [![PyPI version](https://img.shields.io/pypi/v/paperstack-cli.svg)](https://pypi.org/project/paperstack-cli/) [![Python versions](https://img.shields.io/pypi/pyversions/paperstack-cli.svg)](https://pypi.org/project/paperstack-cli/) [![License](https://img.shields.io/pypi/l/paperstack-cli.svg)](https://github.com/MilkClouds/paperstack/blob/main/LICENSE)

Paperstack is an open-source CLI for maintaining, querying, and publishing an independently authored research corpus.
The software and corpus are separate: Paperstack can be public while each corpus and its editorial judgments remain
under its maintainer's control.

It can search typed reviews, initialize and validate entries, inspect external paper metadata, read arXiv sources,
manage a selected-venue DBLP index, and build a static viewer. A synthetic corpus lives in
[`examples/corpus`](https://github.com/MilkClouds/paperstack/tree/main/examples/corpus); no assessment of real work is
bundled with the package.

## Install

```bash
uv tool install paperstack-cli
```

PDF conversion is optional:

```bash
uv tool install 'paperstack-cli[pdf]'
```

The PDF extra uses `pdf-inspector` for native extraction and selective OCR.
Clean text PDFs need no external runtime, while routed OCR pages require the pinned PDFium and ONNX Runtime libraries described in the [OCR runtime guide](https://github.com/firecrawl/pdf-inspector/blob/main/docs/ocr-runtime.md).
The first routed page downloads and verifies the pinned OCR model set unless an offline model directory is configured.
When the shared libraries are not on the platform search path, point `pdf-inspector` at the extracted files:

```bash
export PDFIUM_LIB_PATH=/absolute/path/to/libpdfium.so
export ORT_DYLIB_PATH=/absolute/path/to/libonnxruntime.so
```

The runtime guide lists the matching downloads and filenames for Linux, macOS, and Windows.

## AI agent skill

Paperstack ships an [Agent Skills](https://agentskills.io)-compatible skill for Codex, Claude Code, and other
compatible agents. Install the CLI first, then add the skill globally with the open
[`skills`](https://github.com/vercel-labs/skills) CLI:

```bash
npx skills add MilkClouds/paperstack --skill paperstack -g
```

The installer detects supported agents such as Codex and Claude Code; pass `-a codex -a claude-code` to target them
explicitly, or omit `-g` for a project-local installation. The shared [`SKILL.md`](skills/paperstack/SKILL.md) keeps
corpus selection, source inspection, entry authoring, validation, and viewer workflows consistent without exposing a
private corpus. Use `npx skills update paperstack -g` to update it later.

## Choose a corpus

Register either a local working tree or a GitHub repository. The first registered corpus becomes active.

```bash
paperstack corpus add work --path ~/research/reviews
paperstack corpus init new --path ~/research/new-corpus
paperstack corpus add private --repo OWNER/private-reviews
paperstack corpus list
paperstack corpus use private
paperstack corpus remove work
paperstack corpus remove private --purge-cache --yes
```

Private GitHub repositories use the existing `gh` login without storing its token:

```bash
gh auth login
paperstack review sync
```

`PAPERSTACK_DIR` and `PAPERSTACK_REPO` remain available for automation. Explicit environment variables take priority,
then a surrounding corpus working tree, then the active profile. Profiles are stored in
`${XDG_CONFIG_HOME:-~/.config}/paperstack/config.json`.

Provider credentials can be stored once without adding them to a project `.env`:

```bash
paperstack config set semantic-scholar.api-key
paperstack config status
paperstack config paths
paperstack config unset semantic-scholar.api-key
```

`set` prompts without echoing the value; use `--stdin` to pipe it from a password manager. Credentials are written
atomically to `${XDG_CONFIG_HOME:-~/.config}/paperstack/credentials.json`, with mode `0600` on POSIX systems.
Exported environment variables take priority, followed by the nearest `.env`, then the credential store. GitHub
Actions can therefore keep using repository secrets through environment variables.

## Review corpus

```bash
paperstack review show example2026paperstack --brief
paperstack review show arxiv:2410.24164 --json
paperstack review list --quality good --tag vla
paperstack review search "flow matching"
paperstack review init <key> --id arxiv:NNNN.NNNNN --title "Verbatim title" --editor <name>
paperstack review check --style
paperstack review audit
paperstack review citations --fetch
```

Paper entries, talks, and posts use separate contracts under `entries/{papers,talks,posts}/`. The complete schemas,
writing guide, and source-reading workflow are in
[Entry authoring](https://github.com/MilkClouds/paperstack/blob/main/docs/ENTRIES.md).

## Inspect papers

These commands use external source records and do not select a citation or make an editorial judgment:

```bash
paperstack paper search "Attention Is All You Need" --source dblp
paperstack paper search "Exact Paper Title" --source openreview --exact-title --openreview-status accepted
paperstack paper search "robot learning" --source semantic-scholar --year 2024-2026
paperstack paper search "robot learning" --source semantic-scholar --normalized-json
paperstack paper search 'ti:"robot learning"' --source arxiv --category cs.RO --sort date
paperstack paper metadata arxiv:2106.09685
paperstack paper metadata doi:10.1109/CVPR.2016.90 --source crossref
paperstack paper citations arxiv:2106.09685 --limit 50
paperstack paper references doi:10.48550/arXiv.2106.09685 --limit 50
paperstack paper read arxiv:2604.23073 --outline
paperstack paper read arxiv:2604.23073 --section 6
paperstack paper pdf arxiv:2602.09017
```

`metadata` accepts `arxiv:`, `doi:`, `dblp:`, and `openreview:` references. `read` and `pdf` require an `arxiv:`
reference. `authors`, `citations`, and `references` use Semantic Scholar and also accept its `s2:`, `corpus:`, `acl:`,
`pmid:`, and `mag:` identifiers. Structured commands expose scoped `--json` flags; networked commands expose scoped
`--offline` flags. Paperstack reports source records but does not synthesize a citation entry or choose which version
of a work should be cited.

Use `--normalized-json` on `metadata` or `search` for a stable `{status, papers, errors}` envelope. Each paper has
`title`, `authors`, `year`, `venue`, `publication_status`, `source`, `source_id`, and `source_url`; use `--json` when
the provider's complete raw response is required. Publication status describes that source record, not a resolved
claim about every version of the work.

OpenReview exact-title search uses its title-only exact mode and verifies a normalized title match locally. Status
filtering is conservative because venues encode decisions and withdrawals differently; Paperstack infers it from
public invitation, venue, decision, and status fields rather than treating it as a universal field.

## Build a viewer

```bash
paperstack viewer build
paperstack viewer serve --port 8000
```

The viewer is generated from the selected corpus. Source Markdown remains available alongside the rendered pages.

## DBLP index

The optional selected-venue index accelerates DBLP search and is required by `review audit`:

```bash
paperstack index dblp status
paperstack index dblp install
paperstack index dblp update
```

Installed snapshots are content-addressed and verified by SHA-256, schema, and embedded metadata before an atomic
pointer switch. See
[DBLP snapshot releases](https://github.com/MilkClouds/paperstack/blob/main/docs/DBLP_RELEASES.md) for maintenance.

## Configuration

| Variable | Purpose |
|---|---|
| `PAPERSTACK_DIR` | Explicit local corpus working tree |
| `PAPERSTACK_REPO` | Explicit GitHub corpus in `OWNER/REPO` form |
| `PAPERSTACK_TTL` | Remote corpus cache refresh interval; default `3600` seconds |
| `PAPERSTACK_PAPERS_DIR` | arXiv source and PDF cache |
| `SEMANTIC_SCHOLAR_API_KEY` | Optional higher-rate Semantic Scholar access |
| `OPENREVIEW_ACCESS_TOKEN` | Optional OpenReview access cookie value |
| `XDG_CONFIG_HOME` | Corpus profile and credential configuration root |
| `XDG_CACHE_HOME` | Remote corpus and paper cache root |
| `XDG_DATA_HOME` | DBLP index root |

The console entry point also loads the nearest `.env` without overriding exported variables. Review exit codes are
`0` for hits, `1` for no match, `2` for ambiguity, and `3` for unavailable data or invalid configuration.

## Development

```bash
uv run --group lint ruff check .
uv run --group lint ruff format --check .
uv run --group test pytest -q
PAPERSTACK_DIR=examples/corpus uv run paperstack viewer build --output /tmp/paperstack-site
```

Paperstack is licensed under Apache-2.0. Corpora are separate works and may use their own terms.
