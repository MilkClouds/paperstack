"""Persist named corpus sources without storing credentials."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised when the corpus registry is invalid."""


@dataclass(frozen=True)
class Corpus:
    name: str
    kind: str
    location: str


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "paperstack" / "config.json"


def _empty() -> dict:
    return {"version": 1, "active": None, "corpora": {}}


def load() -> dict:
    path = config_path()
    if not path.is_file():
        return _empty()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"corpus config is unreadable: {path}") from exc
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ConfigError(f"corpus config has an unsupported format: {path}")
    registry = document.get("corpora")
    active = document.get("active")
    if not isinstance(registry, dict) or active is not None and not isinstance(active, str):
        raise ConfigError(f"corpus config has an invalid schema: {path}")
    if active is not None and active not in registry:
        raise ConfigError(f"active corpus {active!r} is not registered: {path}")
    for name, item in registry.items():
        if not _valid_name(name) or not isinstance(item, dict):
            raise ConfigError(f"corpus config has an invalid entry: {name!r}")
        if item.get("kind") not in ("path", "repo") or not isinstance(item.get("location"), str):
            raise ConfigError(f"corpus config has an invalid entry: {name!r}")
    return document


def _save(document: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=path.parent)
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


def _valid_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*", name))


def valid_repo(repo: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo))


def entries() -> list[Corpus]:
    document = load()
    return [Corpus(name, item["kind"], item["location"]) for name, item in sorted(document["corpora"].items())]


def active() -> Corpus | None:
    document = load()
    name = document["active"]
    if name is None:
        return None
    item = document["corpora"][name]
    return Corpus(name, item["kind"], item["location"])


def add(name: str, *, kind: str, location: str) -> Corpus:
    if not _valid_name(name):
        raise ConfigError("corpus name must contain lowercase letters, digits, or hyphens")
    if kind not in ("path", "repo"):
        raise ConfigError(f"unsupported corpus kind: {kind}")
    if kind == "repo" and not valid_repo(location):
        raise ConfigError("GitHub repository must have OWNER/REPO form")
    document = load()
    if name in document["corpora"]:
        raise ConfigError(f"corpus already exists: {name}")
    document["corpora"][name] = {"kind": kind, "location": location}
    if document["active"] is None:
        document["active"] = name
    _save(document)
    return Corpus(name, kind, location)


def use(name: str) -> Corpus:
    document = load()
    if name not in document["corpora"]:
        raise ConfigError(f"unknown corpus: {name}")
    document["active"] = name
    _save(document)
    item = document["corpora"][name]
    return Corpus(name, item["kind"], item["location"])


def remove(name: str) -> Corpus:
    document = load()
    if name not in document["corpora"]:
        raise ConfigError(f"unknown corpus: {name}")
    item = document["corpora"].pop(name)
    if document["active"] == name:
        document["active"] = next(iter(sorted(document["corpora"])), None)
    _save(document)
    return Corpus(name, item["kind"], item["location"])
