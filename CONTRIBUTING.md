# Contributing

Use Python 3.11.4 or newer and `uv`. Keep synthetic fixtures free of judgments about real people or work.

```bash
uv sync --locked --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Changes to entry schemas must update `docs/ENTRIES.md` and the synthetic corpus. Changes to vendored files must update
their license and provenance together. Pull requests should explain user-visible behavior and include regression
tests.
