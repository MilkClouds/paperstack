"""Console entry point with project-local environment loading."""

from __future__ import annotations

from dotenv import find_dotenv, load_dotenv


def load_environment() -> None:
    """Load the nearest .env without replacing exported variables."""
    if path := find_dotenv(usecwd=True):
        load_dotenv(path, override=False)


def main() -> int:
    load_environment()

    # Import after loading because CLI configuration is initialized at import time.
    from paperstack.cli import main as cli_main

    return cli_main()
