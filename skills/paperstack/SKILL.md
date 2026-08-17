---
name: paperstack
description: Use the Paperstack CLI to select research corpora, search or read authored reviews, inspect source-backed paper metadata and contents, author and validate typed entries, manage the optional DBLP index, and build corpus viewers. Use when a task mentions Paperstack, a Paperstack corpus, critical reads, paper metadata or source inspection, citation updates, or corpus validation and publishing.
---

# Paperstack

Use `paperstack` as the interface to independently authored research corpora and external paper records.

## Start safely

1. Run `paperstack --help`.
2. If it is missing, ask before running `uv tool install paperstack-cli`.
3. Run `paperstack corpus list` before assuming which corpus is active.
4. Register an existing corpus with `paperstack corpus add NAME --path PATH` or `--repo OWNER/REPO` only when the user identifies it. Use `paperstack corpus init` only when asked to create one.

Never remove a profile, purge a remote cache, overwrite an entry, or fetch citation updates without explicit user intent.

## Route the task

- Use `paperstack review ...` for authored entries and editorial judgments.
- Use `paperstack paper ...` for external metadata, search results, arXiv source, and PDFs.
- Use `paperstack index dblp ...` for the optional local DBLP index.
- Use `paperstack viewer ...` to build or serve the selected corpus.

Do not present an authored review as source metadata or an external record as an editorial judgment. Prefer scoped `--json` output when another command or agent will consume the result. Use `--offline` when the user requires no network access.

## Read and inspect

Start with the narrowest command that answers the request:

```bash
paperstack review search "flow matching"
paperstack review show arxiv:2410.24164 --json
paperstack review list --quality good --tag vla
paperstack paper metadata arxiv:2106.09685
paperstack paper search 'ti:"flow matching" AND abs:robot' --source arxiv --category cs.RO --sort date
paperstack paper read arxiv:2106.09685 --outline
paperstack paper read arxiv:2106.09685 --section 3
paperstack paper bibtex arxiv:2106.09685
paperstack paper cache list
```

Use `paperstack paper pdf` only when the source workflow needs a PDF. Install the PDF extra with `uv tool install 'paperstack-cli[pdf]'` only after the user agrees.

Use raw arXiv query syntax with `paperstack paper search --source arxiv`; add category, date, limit, and sort flags
instead of routing through an arXiv MCP server. Use `paperstack paper watch add|list|remove|check` for persistent topic
watches. Use `paperstack paper read --offline` and `paperstack paper cache list` for local-only paper access.

## Author entries

Before editing an authored corpus, read its nearest `AGENTS.md` and documented entry-authoring guide. Generate the canonical scaffold instead of copying another entry:

```bash
paperstack review init KEY --kind paper --id arxiv:NNNN.NNNNN --title "Verbatim title" --editor NAME
paperstack review init KEY --kind talk --id URL --title TITLE --speaker NAME --channel CHANNEL --published YYYY-MM-DD --editor NAME
paperstack review init KEY --kind post --id URL --title TITLE --publisher PUBLISHER --published YYYY-MM-DD --editor NAME
```

Read the primary source before writing. Keep factual summary, critical interpretation, and provenance distinct. Preserve the corpus's existing vocabulary and editorial voice; do not invent affiliations, venues, citation counts, or quality grades.

After any edit, run:

```bash
paperstack review check --style
```

Report validation failures without weakening the corpus contract to make them pass.

## Build and maintain

Use `paperstack viewer build` for static output and `paperstack viewer serve --port 8000` only when the user wants a local server. Check `paperstack index dblp status` before installing or updating the DBLP index. Treat `paperstack review citations --fetch` as a write: inspect its diff and validate the corpus afterward.

For unfamiliar options, run `paperstack <group> <command> --help` rather than guessing.
