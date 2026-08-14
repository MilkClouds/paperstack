# Releasing

1. Update the version in `pyproject.toml` and move relevant changelog items into that version.
2. Run `uv lock`, `uv sync --locked --all-groups`, lint, tests, and `uv build`.
3. Inspect wheel/sdist contents, including all first- and third-party license files.
4. Test clean base and `full` wheel installs and build the synthetic viewer.
5. Push a signed `vX.Y.Z` tag whose version exactly matches `pyproject.toml`.
6. The release workflow publishes with PyPI trusted publishing and attaches distributions plus checksums to GitHub.

Configure the `pypi` GitHub environment with protection rules and register this repository/workflow as a trusted
publisher on PyPI before the first automated release.
