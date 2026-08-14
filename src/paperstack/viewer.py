"""Build and serve the static corpus viewer."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import ClassVar

from . import entry_types


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable JSON: {path}") from exc


def _collections(root: Path, keys: set[str]) -> dict:
    path = root / "entries" / "collections.json"
    document = _read_json(path)
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError(f"invalid collections document: {path}")
    collections = document.get("collections")
    if not isinstance(collections, list):
        raise TypeError(f"collections must be an array: {path}")
    seen: set[str] = set()
    published = False
    for collection in collections:
        if not isinstance(collection, dict):
            raise TypeError(f"collection is not an object: {path}")
        identifier = collection.get("id")
        status = collection.get("status")
        members = collection.get("entries")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError(f"collection has an invalid or duplicate id: {identifier!r}")
        if status not in ("published", "draft"):
            raise ValueError(f"collection {identifier!r} has an invalid status")
        if not isinstance(collection.get("title"), str) or not collection["title"]:
            raise ValueError(f"collection {identifier!r} has no title")
        if not isinstance(collection.get("description"), str) or not collection["description"]:
            raise ValueError(f"collection {identifier!r} has no description")
        if not isinstance(members, list) or not members or len(members) != len(set(members)):
            raise ValueError(f"collection {identifier!r} has invalid or duplicate members")
        missing = sorted(set(members) - keys)
        if missing:
            raise ValueError(f"collection {identifier!r} references missing entries: {', '.join(missing)}")
        seen.add(identifier)
        published = published or status == "published"
    if collections and not published:
        raise ValueError("at least one collection must be published")
    return document


def _citations(root: Path) -> dict:
    path = root / "entries" / "citations.json"
    document = _read_json(path)
    if not isinstance(document, dict) or not isinstance(document.get("papers"), dict):
        raise TypeError(f"invalid citations document: {path}")
    return document


def _asset_root() -> Path:
    packaged = files("paperstack").joinpath("viewer_assets")
    if packaged.is_dir():
        return Path(str(packaged))
    development = Path(__file__).parents[2] / "scripts" / "build"
    if development.is_dir():
        return development
    raise RuntimeError("viewer assets are missing from this installation")


def build(root: Path, output: Path) -> int:
    from .cli import load

    entries = load(root)
    keys = {entry["key"] for entry in entries}
    collections = _collections(root, keys)
    citations = _citations(root)
    destination = output.expanduser().resolve()
    corpus = root.resolve()
    entry_root = corpus / "entries"
    anchor = Path(destination.anchor)
    if (
        destination in (anchor, Path.home().resolve(), Path.cwd().resolve(), corpus, entry_root)
        or destination in corpus.parents
        or entry_root in destination.parents
    ):
        raise ValueError("viewer output would replace a broad path, the corpus, or authored entries")
    backup = destination.parent / f".{destination.name}.backup"
    if backup.exists() and not destination.exists():
        marker = backup / ".paperstack-viewer"
        if not backup.is_dir() or not marker.is_file() or marker.read_text(encoding="utf-8") != "1\n":
            raise ValueError(f"viewer backup is not owned by Paperstack: {backup}")
        os.replace(backup, destination)
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"viewer output is not a directory: {destination}")
    if destination.exists() and any(destination.iterdir()):
        marker = destination / ".paperstack-viewer"
        if not marker.is_file() or marker.read_text(encoding="utf-8") != "1\n":
            raise ValueError(f"viewer output is a nonempty directory not created by Paperstack: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(dir=destination.parent, prefix=f".{destination.name}.tmp-"))
    try:
        _write_site(root, staged, entries, collections, citations)
        shutil.rmtree(backup, ignore_errors=True)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(staged, destination)
        except OSError:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    return len(entries)


def _write_site(root: Path, destination: Path, entries: list[dict], collections: dict, citations: dict) -> None:
    (destination / "assets").mkdir(parents=True, exist_ok=True)
    for directory in entry_types.ENTRY_TYPES.values():
        (destination / "entries" / directory.directory).mkdir(parents=True)
    for entry in entries:
        source = root / "entries" / entry["path"]
        shutil.copy2(source, destination / "entries" / entry["path"])
    data = {
        "version": collections["version"],
        "collections": collections["collections"],
        "citations": citations,
        "entries": entries,
    }
    (destination / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8"
    )
    assets = _asset_root()
    site = assets / "site" if (assets / "site").is_dir() else assets
    vendor = assets / "vendor" if (assets / "vendor").is_dir() else assets
    for name in ("index.html", "entry.html"):
        shutil.copy2(site / name, destination / name)
    for name in ("app.js", "style.css"):
        shutil.copy2(site / name, destination / "assets" / name)
    shutil.copy2(site / "favicon.svg", destination / "favicon.svg")
    shutil.copy2(vendor / "marked.min.js", destination / "assets" / "marked.min.js")
    if (vendor / "marked.LICENSE").is_file():
        shutil.copy2(vendor / "marked.LICENSE", destination / "assets" / "marked.LICENSE")
    (destination / ".paperstack-viewer").write_text("1\n", encoding="utf-8")


class Handler(SimpleHTTPRequestHandler):
    extensions_map: ClassVar[dict[str, str]] = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".md": "text/plain; charset=utf-8",
    }


def serve(directory: Path, host: str, port: int) -> None:
    handler = partial(Handler, directory=directory)
    ThreadingHTTPServer((host, port), handler).serve_forever()
