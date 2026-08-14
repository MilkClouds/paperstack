"""Install and query the optional selected-venue DBLP index."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

import polars as pl
from filelock import FileLock

SNAPSHOT = "2026.08.13"
RELEASE_REPO = "MilkClouds/paperstack"
ASSET_NAME = "dblp.parquet"
MIN_RECORDS = 250_000
SCHEMA_VERSION = "1"
INDEX_URL = f"https://github.com/{RELEASE_REPO}/releases/download/dblp-index-{SNAPSHOT}/{ASSET_NAME}"
INDEX_SHA256 = "2656b1ea5423bfc93be48c4f75a6df94704cde155a064075e9e18fa6ef8938fa"

COLUMNS = (
    "normalized_title",
    "title",
    "dblp_key",
    "authors",
    "venue",
    "year",
    "entry_type",
    "doi",
    "url",
)
SCHEMA = pl.Schema(
    {
        "normalized_title": pl.String,
        "title": pl.String,
        "dblp_key": pl.String,
        "authors": pl.String,
        "venue": pl.String,
        "year": pl.Int64,
        "entry_type": pl.String,
        "doi": pl.String,
        "url": pl.String,
    }
)
RESULT_COLUMNS = COLUMNS[1:]


@dataclass(frozen=True)
class Snapshot:
    version: str
    url: str
    sha256: str
    minimum_records: int = MIN_RECORDS
    asset_id: int | None = None


PINNED_SNAPSHOT = Snapshot(SNAPSHOT, INDEX_URL, INDEX_SHA256)


def data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "paperstack" / "indexes" / "dblp"


def _current_path() -> Path:
    return data_dir() / "current.json"


def _current() -> dict[str, str]:
    try:
        current = json.loads(_current_path().read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("DBLP index pointer is unreadable") from exc
    if not isinstance(current, dict):
        raise TypeError("DBLP index pointer is not an object")
    required = {"file", "snapshot", "source", "sha256"}
    if not required <= current.keys():
        raise RuntimeError("DBLP index pointer is incomplete")
    if not re.fullmatch(r"dblp-[0-9a-f]{64}\.parquet", str(current["file"])):
        raise RuntimeError("DBLP index pointer has an invalid file name")
    if not re.fullmatch(r"[0-9a-f]{64}", str(current["sha256"])):
        raise RuntimeError("DBLP index pointer has an invalid SHA-256")
    if current["file"] != f"dblp-{current['sha256']}.parquet":
        raise RuntimeError("DBLP index pointer file and SHA-256 do not match")
    return {key: str(value) for key, value in current.items()}


def index_path() -> Path:
    try:
        name = _current()["file"]
    except (RuntimeError, TypeError):
        name = ASSET_NAME
    return data_dir() / name


def _legacy_paths() -> tuple[Path, ...]:
    return data_dir() / "dblp.sqlite3", data_dir() / "manifest.json", data_dir() / ASSET_NAME


def normalize_title(title: str) -> str:
    return "".join(character.lower() for character in title if character.isalnum())


def _field(bibtex: str, name: str) -> str | None:
    match = re.search(rf"^\s*{name}\s*=\s*", bibtex, re.MULTILINE)
    if not match:
        return None
    start = match.end()
    if start >= len(bibtex):
        return None
    opening = bibtex[start]
    if opening == "{":
        depth = 0
        position = start
        while position < len(bibtex):
            character = bibtex[position]
            if character == "\\" and position + 1 < len(bibtex) and bibtex[position + 1] in "{}":
                position += 2
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return bibtex[start + 1 : position].strip()
            position += 1
        return None
    if opening == '"':
        position = start + 1
        while position < len(bibtex):
            character = bibtex[position]
            if character == "\\":
                position += 2
                continue
            if character == '"':
                return bibtex[start + 1 : position].strip()
            position += 1
        return None
    end = re.search(r"[,\n}]", bibtex[start:])
    return bibtex[start : start + end.start()].strip() if end else bibtex[start:].strip()


def _key(bibtex: str) -> str | None:
    match = re.match(r"@\w+\{([^,]+),", bibtex)
    return match.group(1).strip() if match else None


def _entry_type(bibtex: str) -> str | None:
    match = re.match(r"@(\w+)\{", bibtex)
    return match.group(1).lower() if match else None


def _row(normalized: str, bibtex: str) -> tuple:
    raw_title = _field(bibtex, "title") or normalized
    title = re.sub(r"[{}]", "", raw_title).rstrip(".")
    year = _field(bibtex, "year")
    key = _key(bibtex)
    doi = _field(bibtex, "doi")
    source_url = (
        f"https://dblp.org/rec/{key.removeprefix('DBLP:')}.html"
        if key
        else f"https://dblp.org/search?q={quote_plus(doi)}"
        if doi
        else _field(bibtex, "url")
    )
    return (
        normalize_title(title) or normalized,
        title,
        key,
        _field(bibtex, "author"),
        _field(bibtex, "booktitle") or _field(bibtex, "journal"),
        int(year) if year and year.isdigit() else None,
        _entry_type(bibtex),
        doi,
        source_url,
    )


@contextmanager
def _lock():
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(root / "install.lock"):
        yield


def installed() -> bool:
    try:
        name = _current()["file"]
    except (RuntimeError, TypeError):
        return False
    path = data_dir() / name
    return path.is_file() and path.stat().st_size > 0


def _release_version(tag: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"dblp-index-(\d{4})\.(\d{2})(?:\.(\d{2}))?", tag)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)) if match else None


def _github_json(endpoint: str) -> object:
    request = urllib.request.Request(
        f"https://api.github.com/{endpoint}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "paperstack (+https://github.com/MilkClouds/paperstack)",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read(10 * 1024 * 1024 + 1)
    except TimeoutError as exc:
        raise RuntimeError("GitHub Release lookup timed out") from exc
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub Release lookup failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub Release lookup failed ({exc.reason})") from exc
    if len(payload) > 10 * 1024 * 1024:
        raise RuntimeError("GitHub Release response is unexpectedly large")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub Release response is not valid JSON") from exc


def _github_releases() -> list[dict]:
    releases = _github_json(f"repos/{RELEASE_REPO}/releases?per_page=30")
    if not isinstance(releases, list):
        raise TypeError("GitHub Release response is not a list")
    return releases


def _github_release(tag: str) -> dict:
    release = _github_json(f"repos/{RELEASE_REPO}/releases/tags/{tag}")
    if not isinstance(release, dict):
        raise TypeError("GitHub Release response is not an object")
    return release


def latest_snapshot() -> Snapshot:
    candidates = []
    for release in _github_releases():
        version = _release_version(str(release.get("tag_name", "")))
        if version is None or release.get("draft") or release.get("prerelease"):
            continue
        asset = next((item for item in release.get("assets", []) if item.get("name") == ASSET_NAME), None)
        digest = str((asset or {}).get("digest", ""))
        if asset is None or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            continue
        candidates.append(
            (
                version,
                Snapshot(
                    str(release["tag_name"]).removeprefix("dblp-index-"),
                    str(asset["browser_download_url"]),
                    digest.removeprefix("sha256:"),
                    asset_id=int(asset["id"]),
                ),
            )
        )
    if not candidates:
        raise RuntimeError(f"no {ASSET_NAME} asset with a SHA-256 digest was found in {RELEASE_REPO} releases")
    return max(candidates, key=lambda item: item[0])[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_index(path: Path, snapshot: Snapshot | None = None) -> tuple[dict[str, str], int]:
    try:
        schema = pl.read_parquet_schema(path)
        metadata = pl.read_parquet_metadata(path)
        records = pl.scan_parquet(path).select(pl.len()).collect().item()
    except Exception as exc:
        raise RuntimeError("DBLP Parquet index is unreadable") from exc
    if schema != SCHEMA:
        raise RuntimeError("DBLP Parquet schema mismatch")
    required = {"snapshot", "source", "records", "minimum_records", "coverage", "schema_version"}
    if not required <= metadata.keys():
        raise RuntimeError("DBLP Parquet metadata is incomplete")
    if metadata["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError("DBLP Parquet schema version mismatch")
    if metadata["coverage"] != "selected CS venues":
        raise RuntimeError("DBLP Parquet coverage metadata mismatch")
    try:
        declared_records = int(metadata["records"])
        declared_minimum = int(metadata["minimum_records"])
    except ValueError as exc:
        raise RuntimeError("DBLP Parquet record metadata is invalid") from exc
    if records != declared_records:
        raise RuntimeError("DBLP Parquet record metadata mismatch")
    minimum = snapshot.minimum_records if snapshot else declared_minimum
    if records < minimum:
        raise RuntimeError(f"DBLP Parquet has only {records} records; expected at least {minimum}")
    if snapshot and metadata["snapshot"] != snapshot.version:
        raise RuntimeError("DBLP Parquet snapshot metadata mismatch")
    if snapshot and metadata["source"] != snapshot.url:
        raise RuntimeError("DBLP Parquet source metadata mismatch")
    return metadata, records


def _version(version: object) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d{4})\.(\d{2})(?:\.(\d{2}))?", str(version))
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)) if match else None


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(staged: Path, snapshot: Snapshot, metadata: dict[str, str]) -> Path:
    root = data_dir()
    name = f"dblp-{snapshot.sha256}.parquet"
    published = root / name
    os.replace(staged, published)
    _fsync_directory(root)
    pointer = {
        "file": name,
        "snapshot": snapshot.version,
        "source": metadata["source"],
        "sha256": snapshot.sha256,
    }
    pending = root / "current.json.new"
    try:
        with pending.open("w") as handle:
            json.dump(pointer, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, _current_path())
        _fsync_directory(root)
    finally:
        pending.unlink(missing_ok=True)
    return published


def _private_asset_id(snapshot: Snapshot) -> int:
    release = _github_release(f"dblp-index-{snapshot.version}")
    asset = next((item for item in release.get("assets", []) if item.get("name") == ASSET_NAME), None)
    if str((asset or {}).get("digest", "")) != f"sha256:{snapshot.sha256}":
        raise RuntimeError(f"cannot resolve the pinned {ASSET_NAME} asset in {RELEASE_REPO}")
    return int(asset["id"])


def _download(snapshot: Snapshot, staged: Path) -> str:
    digest = hashlib.sha256()
    request = urllib.request.Request(
        snapshot.url,
        headers={"User-Agent": "paperstack (+https://github.com/MilkClouds/paperstack)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response, staged.open("wb") as handle:
            while chunk := response.read(1 << 20):
                handle.write(chunk)
                digest.update(chunk)
    except TimeoutError as exc:
        raise RuntimeError("DBLP snapshot download timed out") from exc
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"DBLP snapshot download failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DBLP snapshot download failed ({exc.reason})") from exc
    return digest.hexdigest()


def install(
    *,
    snapshot: Snapshot = PINNED_SNAPSHOT,
    force: bool = False,
    only_if_newer: bool = False,
) -> dict:
    """Download, verify, and atomically publish a Parquet snapshot."""
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot.sha256):
        raise ValueError("DBLP snapshot SHA-256 is invalid")
    with _lock():
        if installed():
            try:
                current = status()
            except RuntimeError:
                if not force:
                    raise
            else:
                current_version = _version(current.get("snapshot"))
                target_version = _version(snapshot.version)
                current_is_newer = (
                    current_version is not None and target_version is not None and current_version > target_version
                )
                same_release = current.get("snapshot") == snapshot.version and current.get("sha256") == snapshot.sha256
                if only_if_newer and (current_is_newer or same_release):
                    return {**current, "updated": False}
                if not force:
                    return current
        staged = data_dir() / f"{ASSET_NAME}.new"
        staged.unlink(missing_ok=True)
        try:
            if _download(snapshot, staged) != snapshot.sha256:
                raise RuntimeError("DBLP snapshot checksum mismatch")
            metadata, count = _validate_index(staged, snapshot)
            with staged.open("r+b") as handle:
                os.fsync(handle.fileno())
            published = _publish(staged, snapshot, metadata)
            for legacy in _legacy_paths():
                legacy.unlink(missing_ok=True)
            info = {
                "snapshot": snapshot.version,
                "source": metadata["source"],
                "sha256": snapshot.sha256,
                "records": count,
                "coverage": metadata["coverage"],
                "path": str(published),
            }
            if only_if_newer:
                info["updated"] = True
            return info
        finally:
            staged.unlink(missing_ok=True)


def update() -> dict:
    return install(snapshot=latest_snapshot(), force=True, only_if_newer=True)


def install_file(path: Path) -> dict:
    """Verify and atomically install a locally built snapshot."""
    metadata, records = _validate_index(path)
    digest = _sha256(path)
    snapshot = Snapshot(
        metadata["snapshot"], metadata["source"], digest, minimum_records=int(metadata["minimum_records"])
    )
    with _lock():
        staged = data_dir() / f"{ASSET_NAME}.new"
        staged.unlink(missing_ok=True)
        try:
            shutil.copyfile(path, staged)
            if _sha256(staged) != digest:
                raise RuntimeError("DBLP snapshot checksum mismatch after copying")
            with staged.open("r+b") as handle:
                os.fsync(handle.fileno())
            published = _publish(staged, snapshot, metadata)
        finally:
            staged.unlink(missing_ok=True)
    return {
        "snapshot": snapshot.version,
        "source": metadata["source"],
        "sha256": digest,
        "records": records,
        "coverage": metadata["coverage"],
        "path": str(published),
        "installed": True,
    }


def status() -> dict:
    if not _current_path().is_file():
        return {"installed": False, "snapshot_available": SNAPSHOT}
    current = _current()
    path = data_dir() / current["file"]
    if not path.is_file():
        raise RuntimeError("DBLP index pointer target is missing")
    metadata, records = _validate_index(path)
    digest = _sha256(path)
    if current["snapshot"] != metadata["snapshot"] or current["source"] != metadata["source"]:
        raise RuntimeError("DBLP index pointer does not match its Parquet metadata")
    if current["sha256"] != digest:
        raise RuntimeError("DBLP index checksum mismatch")
    return {
        "snapshot": metadata["snapshot"],
        "source": metadata["source"],
        "sha256": digest,
        "coverage": metadata["coverage"],
        "schema_version": metadata["schema_version"],
        "installed": True,
        "path": str(path),
        "bytes": path.stat().st_size,
        "records": records,
    }


def remove() -> None:
    with _lock():
        _current_path().unlink(missing_ok=True)
        for path in data_dir().glob("dblp-*.parquet"):
            path.unlink(missing_ok=True)
        for legacy in _legacy_paths():
            legacy.unlink(missing_ok=True)


def _scan() -> pl.LazyFrame:
    path = index_path()
    try:
        schema = pl.read_parquet_schema(path)
    except Exception as exc:
        raise RuntimeError("DBLP Parquet index is unreadable") from exc
    if schema != SCHEMA:
        raise RuntimeError("DBLP Parquet schema mismatch")
    return pl.scan_parquet(path)


def _records(frame: pl.DataFrame) -> list[dict]:
    return frame.select(RESULT_COLUMNS).to_dicts()


def _collect(frame: pl.LazyFrame) -> pl.DataFrame:
    try:
        return frame.collect()
    except Exception as exc:
        raise RuntimeError("DBLP Parquet index is unreadable") from exc


def lookup(*, key: str | None = None, doi: str | None = None) -> list[dict]:
    if not installed() or not (key or doi):
        return []
    if key:
        values = (key, f"DBLP:{key}")
        query = _scan().filter(pl.col("dblp_key").is_in(values))
    else:
        query = _scan().filter(pl.col("doi").str.to_lowercase() == doi.lower())
    return _records(_collect(query))


def _search_normalized(norm: str, limit: int) -> list[dict]:
    exact = _collect(_scan().filter(pl.col("normalized_title") == norm).head(limit))
    if exact.height or len(norm) < 10:
        return _records(exact)
    matches = (
        _scan()
        .filter(pl.col("normalized_title").str.contains(norm, literal=True))
        .with_columns(
            pl.col("normalized_title").str.starts_with(norm).alias("_prefix"),
            pl.col("normalized_title").str.len_chars().alias("_length"),
        )
        .sort(["_prefix", "_length"], descending=[True, False])
        .head(limit)
    )
    return _records(_collect(matches))


def search(title: str, limit: int = 10) -> list[dict]:
    if not installed() or not (norm := normalize_title(title)):
        return []
    return _search_normalized(norm, limit)


def search_many(titles: list[str], limit: int = 10) -> list[list[dict]]:
    """Resolve a title batch with at most two Parquet scans."""
    if not installed():
        return [[] for _ in titles]
    norms = [normalize_title(title) for title in titles]
    wanted = list({norm for norm in norms if norm})
    exact: dict[str, list[dict]] = {}
    if wanted:
        frame = _collect(_scan().filter(pl.col("normalized_title").is_in(wanted)))
        for row in frame.to_dicts():
            norm = row.pop("normalized_title")
            exact.setdefault(norm, []).append(row)
    missing = [norm for norm in wanted if norm not in exact and len(norm) >= 10]
    fallback: dict[str, list[dict]] = {}
    if missing:
        pattern = "|".join(re.escape(norm) for norm in missing)
        candidates = _collect(_scan().filter(pl.col("normalized_title").str.contains(pattern))).to_dicts()
        for norm in missing:
            matches = [row for row in candidates if norm in row["normalized_title"]]
            matches.sort(key=lambda row: (not row["normalized_title"].startswith(norm), len(row["normalized_title"])))
            fallback[norm] = [{key: row[key] for key in RESULT_COLUMNS} for row in matches[:limit]]
    return [(exact.get(norm) or fallback.get(norm) or [])[:limit] for norm in norms]
