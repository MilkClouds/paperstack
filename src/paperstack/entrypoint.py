"""Console entry point with project-local environment loading."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from . import credentials


def load_environment() -> None:
    """Load the nearest .env without replacing exported variables."""
    exported_keys = set(os.environ)
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)
    credentials.set_environment_context(exported_keys, Path(path) if path else None)


def main() -> int:
    load_environment()

    # Import after loading because CLI configuration is initialized at import time.
    from paperstack.cli import main as cli_main

    return cli_main()
