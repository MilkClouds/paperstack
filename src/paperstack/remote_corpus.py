"""Private GitHub corpus caching with atomic, least-data publication."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock, Timeout

SHA_FILE = ".paperstack-sha"
MANIFEST_FILE = ".paperstack-cache.json"
CACHE_FORMAT = 2
MAX_FILES = 10_000
MAX_BYTES = 200 * 1024 * 1024


def _warn(message: str) -> None:
    print(f"paperstack: {message}", file=sys.stderr)


def is_corpus(root: Path) -> bool:
    return (root / "entries").is_dir()


@dataclass(frozen=True)
class RemoteCache:
    repo: str

    @property
    def root(self) -> Path:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        digest = hashlib.sha256(self.repo.lower().encode()).hexdigest()[:24]
        return base / "paperstack" / digest

    @property
    def corpus(self) -> Path:
        return self.root / "corpus"

    @property
    def staged(self) -> Path:
        return self.root / "corpus.old"

    @property
    def checked(self) -> Path:
        return self.root / "last-checked"

    @property
    def lock(self) -> Path:
        return self.root / "lock"

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def size_bytes(self) -> int:
        if not self.corpus.is_dir():
            return 0
        return sum(path.stat().st_size for path in self.corpus.rglob("*") if path.is_file())


def gh(*args: str, binary: bool = False) -> bytes | str | None:
    try:
        process = subprocess.run(["gh", *args], capture_output=True, timeout=120, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0:
        return None
    return process.stdout if binary else process.stdout.decode().strip()


def remote_sha(repo: str) -> str | None:
    return gh("api", f"repos/{repo}/commits/HEAD", "--jq", ".sha") or None


def local_sha(cache: RemoteCache) -> str | None:
    marker = cache.corpus / SHA_FILE
    return marker.read_text().strip() if marker.is_file() else None


def cache_valid(cache: RemoteCache, path: Path | None = None) -> bool:
    corpus = path or cache.corpus
    manifest = corpus / MANIFEST_FILE
    if not is_corpus(corpus) or not manifest.is_file():
        return False
    try:
        value = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return value == {"format": CACHE_FORMAT, "repo": cache.repo.lower()}


@contextlib.contextmanager
def cache_lock(cache: RemoteCache):
    cache.ensure_root()
    lock = FileLock(cache.lock, mode=0o600)
    try:
        try:
            lock.acquire(timeout=0)
        except Timeout:
            yield False
            return
        yield True
    finally:
        if lock.is_locked:
            lock.release()


def install(cache: RemoteCache, staged: Path) -> bool:
    cache.ensure_root()
    try:
        shutil.rmtree(cache.staged, ignore_errors=True)
        if cache.corpus.exists():
            os.replace(cache.corpus, cache.staged)
        os.replace(staged, cache.corpus)
    except OSError as exc:
        _warn(f"could not publish the downloaded corpus ({exc})")
        if not cache.corpus.exists() and cache.staged.exists():
            with contextlib.suppress(OSError):
                os.replace(cache.staged, cache.corpus)
        return False
    shutil.rmtree(cache.staged, ignore_errors=True)
    return True


def recover(cache: RemoteCache) -> bool:
    if cache_valid(cache) or not cache_valid(cache, cache.staged):
        return False
    with cache_lock(cache) as mine:
        if not mine or cache_valid(cache) or not cache_valid(cache, cache.staged):
            return False
        try:
            os.replace(cache.staged, cache.corpus)
        except OSError as exc:
            _warn(f"could not recover the cached corpus ({exc})")
            return False
    _warn("recovered the cached corpus from an interrupted sync")
    return True


def sync(repo: str, force: bool = False) -> bool:
    cache = RemoteCache(repo)
    with cache_lock(cache) as mine:
        if not mine:
            _warn("another paperstack is syncing; using the cache as it stands")
            return cache_valid(cache)
        return _sync(cache, force)


def _sync(cache: RemoteCache, force: bool) -> bool:
    sha = remote_sha(cache.repo)
    if not sha:
        return False
    if not force and local_sha(cache) == sha and cache_valid(cache):
        cache.checked.touch()
        os.chmod(cache.checked, 0o600)
        return True

    temporary = Path(tempfile.mkdtemp(dir=cache.root, prefix=".staging-"))
    published = temporary / "published"
    archive_path = temporary / "source.tar.gz"
    try:
        try:
            with archive_path.open("wb") as output:
                process = subprocess.run(
                    ["gh", "api", f"repos/{cache.repo}/tarball/{sha}"],
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=120,
                    check=False,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        if process.returncode != 0:
            return False
        try:
            with tarfile.open(archive_path, mode="r:gz") as archive:
                selected = []
                total = 0
                root_name = None
                seen = set()
                for member in archive:
                    parts = Path(member.name).parts
                    if len(parts) < 2:
                        continue
                    root_name = root_name or parts[0]
                    if parts[0] != root_name:
                        raise ValueError("archive has multiple roots")
                    relative = parts[1:]
                    allowed_json = relative in (("entries", "collections.json"), ("entries", "citations.json"))
                    allowed_entry = (
                        len(relative) == 3
                        and relative[0] == "entries"
                        and relative[1] in ("papers", "talks", "posts")
                        and relative[2].endswith(".md")
                    )
                    if not (allowed_json or allowed_entry):
                        continue
                    if not member.isfile() or relative in seen:
                        raise ValueError("archive contains an unsafe or duplicate corpus member")
                    seen.add(relative)
                    total += member.size
                    if len(seen) > MAX_FILES or total > MAX_BYTES:
                        raise ValueError("corpus archive exceeds the cache safety limit")
                    selected.append((member, relative))
                published.mkdir(mode=0o700)
                for directory in ("papers", "talks", "posts"):
                    (published / "entries" / directory).mkdir(parents=True, mode=0o700)
                for member, relative in selected:
                    target = published.joinpath(*relative)
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError("archive member is unreadable")
                    with source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    os.chmod(target, 0o600)
        except (tarfile.TarError, OSError, EOFError) as exc:
            _warn(f"the downloaded archive is unreadable ({exc})")
            return False
        except ValueError as exc:
            _warn(f"the downloaded archive is unsafe ({exc})")
            return False
        (published / SHA_FILE).write_text(sha + "\n")
        (published / MANIFEST_FILE).write_text(json.dumps({"format": CACHE_FORMAT, "repo": cache.repo.lower()}) + "\n")
        os.chmod(published / SHA_FILE, 0o600)
        os.chmod(published / MANIFEST_FILE, 0o600)
        if not install(cache, published):
            return False
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    cache.checked.touch()
    os.chmod(cache.checked, 0o600)
    return True


def stale(cache: RemoteCache, ttl: int) -> bool:
    return not cache.checked.is_file() or time.time() - cache.checked.stat().st_mtime > ttl


def purge(repo: str) -> bool:
    cache = RemoteCache(repo)
    if not cache.root.exists():
        return False
    shutil.rmtree(cache.root)
    return True
