"""Resolve and persist provider credentials without exposing their values."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from dotenv import dotenv_values


class CredentialsError(ValueError):
    """Raised when credential configuration is invalid."""


SEMANTIC_SCHOLAR_API_KEY = "semantic-scholar.api-key"
_SPECS = {
    SEMANTIC_SCHOLAR_API_KEY: ("semantic_scholar", "api_key", "SEMANTIC_SCHOLAR_API_KEY"),
}
_EXPORTED_KEYS: set[str] = set()
_DOTENV_PATH: Path | None = None


def names() -> tuple[str, ...]:
    return tuple(_SPECS)


def credentials_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "paperstack" / "credentials.json"


def set_environment_context(exported_keys: set[str], dotenv_path: Path | None) -> None:
    """Remember which process values predated project-local dotenv loading."""
    global _DOTENV_PATH, _EXPORTED_KEYS
    _EXPORTED_KEYS = exported_keys
    _DOTENV_PATH = dotenv_path


def _empty() -> dict:
    return {"version": 1, "providers": {}}


def _load() -> dict:
    path = credentials_path()
    if not path.is_file():
        return _empty()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CredentialsError(f"credential store is unreadable: {path}") from exc
    if not isinstance(document, dict) or document.get("version") != 1:
        raise CredentialsError(f"credential store has an unsupported format: {path}")
    providers = document.get("providers")
    if not isinstance(providers, dict):
        raise CredentialsError(f"credential store has an invalid schema: {path}")
    for provider, values in providers.items():
        if not isinstance(provider, str) or not isinstance(values, dict):
            raise CredentialsError(f"credential store has an invalid schema: {path}")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
            raise CredentialsError(f"credential store has an invalid schema: {path}")
    return document


def _save(document: dict) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".credentials-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _spec(name: str) -> tuple[str, str, str]:
    try:
        return _SPECS[name]
    except KeyError as exc:
        raise CredentialsError(f"unknown credential: {name}") from exc


def get(name: str) -> str | None:
    provider, key, environment = _spec(name)
    if value := os.environ.get(environment):
        return value
    value = _load()["providers"].get(provider, {}).get(key)
    return value or None


def set_value(name: str, value: str) -> None:
    provider, key, _ = _spec(name)
    value = value.strip()
    if not value or "\n" in value or "\r" in value:
        raise CredentialsError("credential must be one nonempty line")
    document = _load()
    document["providers"].setdefault(provider, {})[key] = value
    _save(document)


def unset(name: str) -> bool:
    provider, key, _ = _spec(name)
    document = _load()
    values = document["providers"].get(provider, {})
    if key not in values:
        return False
    del values[key]
    if not values:
        del document["providers"][provider]
    _save(document)
    return True


def source(name: str) -> str | None:
    provider, key, environment = _spec(name)
    if os.environ.get(environment):
        if environment in _EXPORTED_KEYS:
            return "environment"
        if _DOTENV_PATH and dotenv_values(_DOTENV_PATH).get(environment):
            return "project .env"
        return "environment"
    if _load()["providers"].get(provider, {}).get(key):
        return "credentials file"
    return None


def warnings() -> list[str]:
    messages = []
    if _DOTENV_PATH and _DOTENV_PATH.is_file():
        mode = stat.S_IMODE(_DOTENV_PATH.stat().st_mode)
        if mode & 0o077:
            messages.append(f"{_DOTENV_PATH} is readable by group or others; run chmod 600 {_DOTENV_PATH}")
    path = credentials_path()
    if path.is_file() and stat.S_IMODE(path.stat().st_mode) & 0o077:
        messages.append(f"{path} is readable by group or others; run chmod 600 {path}")
    return messages
