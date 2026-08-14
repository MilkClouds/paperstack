# Corpus separation

Paperstack treats the CLI and authored corpora as independent works. A corpus can remain private without modifying or
forking the CLI.

## Corpus repository

A minimal corpus contains:

```text
entries/
  papers/
  posts/
  talks/
  collections.json
  citations.json
```

Research notes and other private material may live beside `entries/`; the CLI ignores them. CI for a private corpus can
install the public package and validate or publish only that working tree:

```yaml
- uses: astral-sh/setup-uv@v9.0.0
- run: uv tool install paperstack-cli
- run: PAPERSTACK_DIR="$PWD" paperstack review check --style
- run: PAPERSTACK_DIR="$PWD" paperstack viewer build
```

For local development, register the clone once:

```bash
paperstack corpus add personal --path /path/to/private-corpus
```

For read-only access from another machine, register the private GitHub repository and authenticate through `gh`:

```bash
gh auth login
paperstack corpus add personal --repo OWNER/private-corpus
paperstack review sync
```

Paperstack stores the repository name in its configuration and caches only typed `entries/` content. GitHub
credentials remain owned by `gh`.

## Publishing the CLI safely

Do not make a former corpus repository public after deleting its current entries: Git history still contains them.
Create the public CLI repository from an allowlist of code, tests, documentation, viewer assets, and synthetic examples.
Keep the original corpus repository private.
