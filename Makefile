.PHONY: all lint test example clean help
.DEFAULT_GOAL := help

all: lint test             ## lint and test the CLI

lint:                      ## ruff check and format
	@uv run --group lint ruff check .
	@uv run --group lint ruff format --check .

test:                      ## Python unit tests
	@uv run --group test pytest -q

example:                   ## validate and build the synthetic corpus
	@PAPERSTACK_DIR="$$PWD/examples/corpus" uv run paperstack review check
	@PAPERSTACK_DIR="$$PWD/examples/corpus" uv run paperstack viewer build

clean:                     ## remove generated local state
	@rm -rf _site dist .pytest_cache .ruff_cache src/paperstack/__pycache__ tests/__pycache__ uv.lock

help:                      ## list targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'
