"""Read curated entries and inspect source-backed paper records from one CLI.

Review lookup uses a clone, `$PAPERSTACK_DIR`, or a GitHub-backed cache.
Remote corpus access uses gh authentication without reading or storing its token.
Exit codes: 0 hit, 1 no match, 2 ambiguous, 3 unavailable corpus.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from filelock import FileLock, Timeout

from .entry_types import (
    ENTRY_TYPES,
    FrontLoader,
    entry_paths,
    kind_for_path,
    render_scaffold,
    split_front,
)


def warn(msg: str) -> None:
    print(f"paperstack: {msg}", file=sys.stderr)


FAIL = 3


def die(msg: str, code: int = FAIL) -> None:
    warn(msg)
    raise SystemExit(code)


def _ttl() -> int:
    """Parse lazily so invalid configuration does not break --help."""
    raw = os.environ.get("PAPERSTACK_TTL", "3600")
    try:
        return int(raw)
    except ValueError:
        warn(f"PAPERSTACK_TTL={raw!r} is not a whole number of seconds; using 3600")
        return 3600


TTL = _ttl()

SHA_FILE = ".paperstack-sha"  # Moves atomically with the corpus.
CACHE_FORMAT_FILE = ".paperstack-cache-v2"


@dataclass(frozen=True)
class RemoteCache:
    repo: str

    @property
    def root(self) -> Path:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        owner, name = self.repo.lower().split("/", 1)
        return base / "paperstack" / "repos" / owner / name

    @property
    def _old_cache_root(self) -> Path:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        return base / "paperstack" / self.repo.replace("/", "_")

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


def gh(*args: str, binary: bool = False) -> bytes | str | None:
    """Return gh stdout, or None on failure."""
    try:
        p = subprocess.run(["gh", *args], capture_output=True, timeout=120, check=False)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    if p.returncode != 0:
        return None
    return p.stdout if binary else p.stdout.decode().strip()


def remote_sha(repo: str) -> str | None:
    return gh("api", f"repos/{repo}/commits/HEAD", "--jq", ".sha") or None


def local_sha(cache: RemoteCache) -> str | None:
    f = cache.corpus / SHA_FILE
    return f.read_text().strip() if f.is_file() else None


def is_corpus(d: Path) -> bool:
    return (d / "entries").is_dir()


def valid(d: Path) -> bool:
    return bool(entry_paths(d))


def cache_valid(cache: RemoteCache, path: Path | None = None) -> bool:
    root = path or cache.corpus
    return is_corpus(root) and (root / CACHE_FORMAT_FILE).is_file()


@contextlib.contextmanager
def cache_lock(cache: RemoteCache):
    """Serialize cache writes; yield False when the lock is busy."""
    cache.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(cache.root, 0o700)
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
    """Publish a staged corpus without exposing partial data."""
    cache.root.mkdir(parents=True, exist_ok=True)
    try:
        shutil.rmtree(cache.staged, ignore_errors=True)
        if cache.corpus.exists():
            os.replace(cache.corpus, cache.staged)
        os.replace(staged, cache.corpus)
    except OSError as e:
        warn(f"could not publish the downloaded corpus ({e})")
        if not cache.corpus.exists() and cache.staged.exists():
            with contextlib.suppress(OSError):
                os.replace(cache.staged, cache.corpus)
        return False
    shutil.rmtree(cache.staged, ignore_errors=True)
    return True


def recover(cache: RemoteCache) -> bool:
    """Restore the backup left by an interrupted install."""
    if cache_valid(cache) or not cache_valid(cache, cache.staged):
        return False
    with cache_lock(cache) as mine:
        if not mine or cache_valid(cache) or not cache_valid(cache, cache.staged):
            return False
        try:
            os.replace(cache.staged, cache.corpus)
        except OSError as e:
            warn(f"could not recover the cached corpus ({e})")
            return False
    warn("recovered the cached corpus from an interrupted sync")
    return True


def sync(repo: str, force: bool = False) -> bool:
    """Update the cache from main."""
    cache = RemoteCache(repo)
    with cache_lock(cache) as mine:
        if not mine:
            warn("another paperstack is syncing; using the cache as it stands")
            return cache_valid(cache)
        return _sync(cache, force)


def _sync(cache: RemoteCache, force: bool) -> bool:
    sha = remote_sha(cache.repo)
    if not sha:
        return False
    cache.checked.parent.mkdir(parents=True, exist_ok=True)
    if not force and local_sha(cache) == sha and cache_valid(cache):
        cache.checked.touch()
        shutil.rmtree(cache._old_cache_root, ignore_errors=True)
        return True

    # Pin the archive to the recorded commit.
    blob = gh("api", f"repos/{cache.repo}/tarball/{sha}", binary=True)
    if not blob:
        return False

    tmp = Path(tempfile.mkdtemp(dir=cache.root, prefix=".staging-"))
    try:
        published = tmp / "published"
        for directory in ("papers", "talks", "posts"):
            (published / "entries" / directory).mkdir(parents=True, mode=0o700)
        try:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
                root_name = None
                saw_entries = False
                seen = set()
                for member in tar:
                    parts = Path(member.name).parts
                    if len(parts) < 2:
                        continue
                    root_name = root_name or parts[0]
                    if parts[0] != root_name:
                        return False
                    relative = parts[1:]
                    saw_entries = saw_entries or relative[:1] == ("entries",)
                    allowed = relative in (("entries", "collections.json"), ("entries", "citations.json")) or (
                        len(relative) == 3
                        and relative[:2] in (("entries", "papers"), ("entries", "talks"), ("entries", "posts"))
                        and relative[2].endswith(".md")
                    )
                    if not allowed:
                        continue
                    if not member.isfile() or relative in seen:
                        return False
                    seen.add(relative)
                    source = tar.extractfile(member)
                    if source is None:
                        return False
                    target = published.joinpath(*relative)
                    with source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    os.chmod(target, 0o600)
        except (tarfile.TarError, OSError, EOFError) as e:
            warn(f"the downloaded archive is unreadable ({e})")
            return False
        if not saw_entries:
            return False
        (published / SHA_FILE).write_text(sha + "\n")
        (published / CACHE_FORMAT_FILE).write_text("2\n")
        os.chmod(published / SHA_FILE, 0o600)
        os.chmod(published / CACHE_FORMAT_FILE, 0o600)
        if not install(cache, published):
            return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # Only successful syncs refresh the TTL.
    cache.checked.touch()
    shutil.rmtree(cache._old_cache_root, ignore_errors=True)
    return True


def stale(cache: RemoteCache) -> bool:
    if not cache.checked.is_file():
        return True
    return time.time() - cache.checked.stat().st_mtime > TTL


def git_toplevel() -> Path | None:
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return Path(p.stdout.strip()) if p.returncode == 0 else None


def _active_corpus():
    from . import corpora

    try:
        return corpora.active()
    except corpora.ConfigError as exc:
        die(str(exc))


def _remote_repo() -> str | None:
    from . import corpora

    if repo := os.environ.get("PAPERSTACK_REPO"):
        if not corpora.valid_repo(repo):
            die("PAPERSTACK_REPO must have OWNER/REPO form")
        return repo
    selected = _active_corpus()
    return selected.location if selected and selected.kind == "repo" else None


def _resolve_remote(repo: str, *, offline: bool, force_sync: bool) -> Path:
    cache = RemoteCache(repo)
    recover(cache)
    have = cache_valid(cache)
    if offline:
        if not have:
            die(f"no cached copy of {repo} and --offline was given; drop --offline to fetch one")
        return cache.corpus
    if (force_sync or not have or stale(cache)) and not sync(repo, force=force_sync):
        if not have:
            die(
                f"cannot reach {repo} and there is no cached copy.\n"
                "  private repositories use gh authentication: check `gh auth status`, then `gh auth login`"
            )
        warn(f"cannot reach {repo}; using the cached copy from {(local_sha(cache) or 'unknown')[:7]}")
    if not cache_valid(cache):
        die("the cached corpus is unusable and could not be replaced")
    return cache.corpus


def resolve(offline: bool = False, force_sync: bool = False) -> Path:
    if env := os.environ.get("PAPERSTACK_DIR"):
        d = Path(env).expanduser()
        if not is_corpus(d):
            die(f"PAPERSTACK_DIR={env} has no entries/")
        if force_sync:
            warn("--sync does not apply to PAPERSTACK_DIR; reading it as-is")
        return d

    if os.environ.get("PAPERSTACK_REPO"):
        selected_repo = _remote_repo()
        assert selected_repo is not None
        return _resolve_remote(selected_repo, offline=offline, force_sync=force_sync)

    if (top := git_toplevel()) and is_corpus(top):
        if force_sync:
            warn(f"--sync does not apply to the working tree at {top}; reading it as-is")
        return top

    selected = _active_corpus()
    if selected is None:
        die("no corpus selected; run `paperstack corpus add`, or set PAPERSTACK_DIR or PAPERSTACK_REPO")
    if selected.kind == "path":
        root = Path(selected.location)
        if not is_corpus(root):
            die(f"corpus {selected.name!r} has no entries/: {root}")
        if force_sync:
            warn(f"--sync does not apply to local corpus {selected.name!r}; reading it as-is")
        return root
    return _resolve_remote(selected.location, offline=offline, force_sync=force_sync)


def as_list(v) -> list[str]:
    """Normalize a frontmatter scalar or sequence to strings."""
    if v is None:
        return []
    if isinstance(v, (str, bool, int, float)):
        v = [v]
    return [("yes" if x is True else "no" if x is False else str(x)) for x in v]


def load(root: Path) -> list[dict]:
    out = []
    entry_root = root / "entries"
    for p in entry_paths(root):
        text = p.read_text(encoding="utf-8")
        parts = split_front(text)
        if not parts:
            continue
        fm, body = parts
        try:
            meta = yaml.load(fm, Loader=FrontLoader) or {}
        except yaml.YAMLError:
            warn(f"{p.name}: frontmatter is not valid YAML, skipped")
            continue
        if not isinstance(meta, dict):
            warn(f"{p.name}: frontmatter is not a mapping, skipped")
            continue
        meta["key"] = p.stem
        meta["kind"] = kind_for_path(p)
        meta["path"] = p.relative_to(entry_root).as_posix()
        meta["body"] = body.strip()
        meta["name"] = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), p.stem)
        out.append(meta)
    return out


def quality(e: dict) -> str:
    """Return the grade or ungraded."""
    q = e.get("quality")
    return str(q) if q not in (None, "") else "ungraded"


def kind(e: dict) -> str:
    """Return the entry kind."""
    return str(e.get("kind") or "paper")


def lead(e: dict, label: str) -> str:
    """Extract a labeled lead line."""
    tag = f"**{label}.**"
    for ln in e["body"].splitlines():
        if ln.startswith(tag):
            return ln[len(tag) :].strip()
    return ""


def searchable(e: dict) -> str:
    """Flatten entry values for search."""
    parts: list[str] = []

    def walk(v) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif v is not None:
            parts.append(str(v))

    walk(e)
    return "\n".join(parts).lower()


def find(entries: list[dict], q: str) -> list[dict]:
    """Return the first nonempty tier of increasingly broad matches."""
    ql = q.lower()
    for pick in (
        lambda e: e["key"].lower() == ql,
        lambda e: str(e.get("id", "")).lower() in (ql, f"arxiv:{ql}"),
        lambda e: ql in e["name"].lower() or ql in e["key"].lower(),
        lambda e: ql in str(e.get("title", "")).lower(),
    ):
        if hits := [e for e in entries if pick(e)]:
            return hits
    return []


def strip_body(e: dict) -> dict:
    return {k: v for k, v in e.items() if k != "body"}


def one_line(e: dict) -> str:
    label = quality(e) if kind(e) == "paper" else kind(e)
    bits = [f"{label:<9}", e["key"]]
    if e["name"] != e["key"]:
        bits.append(f"({e['name']})")
    return "  ".join(bits)


def show(e: dict, brief: bool) -> None:
    entry_kind = kind(e)
    label = quality(e) if entry_kind == "paper" else entry_kind
    head = [e["name"], f"  {label}"]
    if entry_kind == "talk":
        fields = (e.get("id"), e.get("published"), ", ".join(as_list(e.get("speaker"))), e.get("channel"))
    elif entry_kind == "post":
        fields = (e.get("id"), e.get("published"), e.get("publisher"))
    else:
        fields = (e.get("id"), e.get("venue"), ", ".join(as_list(e.get("lab"))))
    meta = " · ".join(str(x) for x in fields if x)
    print("".join(head))
    if meta:
        print(meta)
    print(f"tags: {', '.join(as_list(e.get('tags')))}    entries/{e['path']}")
    print()
    lead_labels = (
        ("One-liner", "Why watch it") if entry_kind == "talk" else ("One-liner", "Why read it", "Read it anyway")
    )
    for label in lead_labels:
        if v := lead(e, label):
            print(f"{label}. {v}\n")
    if not brief:
        body = e["body"]
        cut = body.find("## ")
        if cut >= 0:
            print(body[cut:])


def _output(parser: argparse.ArgumentParser, *, normalized: bool = False) -> None:
    group = parser.add_mutually_exclusive_group() if normalized else parser
    group.add_argument("--json", action="store_true", help="machine-readable source response")
    if normalized:
        group.add_argument("--normalized-json", action="store_true", help="common paper records across sources")


def _offline(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--offline", action="store_true", help="never touch the network")


def _review_commands(sub) -> None:
    """Add commands that read or change the authored entry corpus."""
    s = sub.add_parser("show", help="review by key, id, or name")
    s.add_argument("query", help="citation key, CURIE, method name, or title fragment")
    s.add_argument("--brief", action="store_true", help="skip the two long sections")
    _output(s)
    _offline(s)

    s = sub.add_parser("search", help="search review title, tags, and body")
    s.add_argument("query", help="text to find in the authored entry corpus")
    _output(s)
    _offline(s)

    s = sub.add_parser("list", help="filter review frontmatter")
    s.add_argument("--quality")
    s.add_argument("--kind", choices=("paper", "talk", "post"))
    s.add_argument("--tag")
    _output(s)
    _offline(s)

    s = sub.add_parser("sync", help="refresh the review cache")
    s.add_argument("--force", action="store_true")

    s = sub.add_parser("init", help="initialize a typed entry scaffold")
    s.add_argument("key", help="explicit entry key and filename stem")
    s.add_argument("--kind", choices=("paper", "talk", "post"), default="paper")
    s.add_argument("--id", required=True, help="registered CURIE or URL")
    s.add_argument("--title", required=True, help="verified verbatim title")
    s.add_argument("--editor", required=True)
    s.add_argument("--speaker", action="append", help="speaker name; repeat for multiple speakers")
    s.add_argument("--channel")
    s.add_argument("--publisher")
    s.add_argument("--published")

    s = sub.add_parser("check", help="validate the entry corpus")
    s.add_argument("--style", action="store_true", help="include prose-length warnings")
    s = sub.add_parser("audit", help="compare review titles and venues with the local DBLP index")
    _output(s)
    _offline(s)

    s = sub.add_parser("citations", help="update citation counts for arXiv paper entries")
    s.add_argument("--fetch", action="store_true", help="fetch live counts from Semantic Scholar")
    _output(s)


def _corpus_commands(sub) -> None:
    s = sub.add_parser("init", help="create and register an empty local corpus")
    s.add_argument("name")
    s.add_argument("--path", type=Path, required=True)

    s = sub.add_parser("add", help="register a local directory or GitHub repository")
    s.add_argument("name", help="short profile name")
    source = s.add_mutually_exclusive_group(required=True)
    source.add_argument("--path", type=Path, help="local corpus working tree")
    source.add_argument("--repo", help="GitHub repository in OWNER/REPO form")

    s = sub.add_parser("use", help="select the default corpus")
    s.add_argument("name")

    s = sub.add_parser("list", help="list registered corpora")
    _output(s)

    s = sub.add_parser("remove", help="forget a corpus without deleting its data")
    s.add_argument("name")
    s.add_argument("--purge-cache", action="store_true")
    s.add_argument("--yes", action="store_true")


def _config_commands(sub) -> None:
    from . import credentials

    s = sub.add_parser("set", help="store a provider credential")
    s.add_argument("name", choices=credentials.names())
    s.add_argument("--stdin", action="store_true", help="read the credential from standard input")

    s = sub.add_parser("unset", help="remove a stored provider credential")
    s.add_argument("name", choices=credentials.names())

    sub.add_parser("status", help="show credential sources without revealing values")
    sub.add_parser("paths", help="show configuration file locations")


def _run_config(a: argparse.Namespace) -> int:
    from . import corpora, credentials

    try:
        if a.config_cmd == "set":
            value = sys.stdin.read() if a.stdin else getpass.getpass(f"{a.name}: ")
            credentials.set_value(a.name, value)
            print(f"stored {a.name} in {credentials.credentials_path()}")
            if credentials.source(a.name) != "credentials file":
                warn(f"{a.name} is currently overridden by {credentials.source(a.name)}")
            return 0
        if a.config_cmd == "unset":
            if credentials.unset(a.name):
                print(f"removed stored {a.name}")
            else:
                print(f"{a.name} was not stored")
            return 0
        if a.config_cmd == "status":
            for name in credentials.names():
                source = credentials.source(name)
                print(f"{name:<32} {'configured' if source else 'not configured':<14} {source or '-'}")
            for message in credentials.warnings():
                warn(message)
            return 0
        if a.config_cmd == "paths":
            print(f"config: {corpora.config_path()}")
            print(f"credentials: {credentials.credentials_path()}")
            return 0
        raise AssertionError(a.config_cmd)
    except (OSError, credentials.CredentialsError) as exc:
        die(f"configuration failed: {exc}")


def _run_corpus(a: argparse.Namespace) -> int:
    from . import corpora

    try:
        if a.corpus_cmd == "init":
            path = corpora.initialize(a.path)
            item = corpora.add(a.name, kind="path", location=str(path))
            print(f"{item.name}: path {item.location} (initialized)")
            return 0
        if a.corpus_cmd == "add":
            if a.path is not None:
                path = a.path.expanduser().resolve()
                if not is_corpus(path):
                    die(f"corpus path has no entries/: {path}")
                item = corpora.add(a.name, kind="path", location=str(path))
            else:
                item = corpora.add(a.name, kind="repo", location=a.repo)
            active = corpora.active()
            suffix = " (active)" if active and active.name == item.name else ""
            print(f"{item.name}: {item.kind} {item.location}{suffix}")
            return 0
        if a.corpus_cmd == "use":
            item = corpora.use(a.name)
            print(f"{item.name}: {item.kind} {item.location}")
            return 0
        if a.corpus_cmd == "remove":
            selected = next((item for item in corpora.entries() if item.name == a.name), None)
            if selected is None:
                raise corpora.ConfigError(f"unknown corpus: {a.name}")
            if a.purge_cache:
                if selected.kind != "repo":
                    die("--purge-cache applies only to GitHub corpus profiles")
                if not a.yes:
                    die("cache deletion requires --yes")
                cache = RemoteCache(selected.location)
                for path in (cache.root, cache._old_cache_root):
                    try:
                        shutil.rmtree(path)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        die(f"could not delete the corpus cache ({exc})")
            item = corpora.remove(a.name)
            suffix = "; cache deleted" if a.purge_cache else "; data was not deleted"
            print(f"removed {item.name}{suffix}")
            return 0
        if a.corpus_cmd == "list":
            active = corpora.active()
            items = corpora.entries()
            if a.json:
                print(
                    json.dumps(
                        [
                            {
                                "name": item.name,
                                "kind": item.kind,
                                "location": item.location,
                                "active": bool(active and item.name == active.name),
                            }
                            for item in items
                        ],
                        indent=2,
                    )
                )
            elif not items:
                print("no corpora registered", file=sys.stderr)
            else:
                for item in items:
                    marker = "*" if active and item.name == active.name else " "
                    print(f"{marker} {item.name:<16} {item.kind:<4} {item.location}")
            return 0
        raise AssertionError(a.corpus_cmd)
    except corpora.ConfigError as exc:
        die(str(exc))


def _review_init(root: Path, a: argparse.Namespace) -> int:
    if not re.fullmatch(r"[a-z0-9]+", a.key):
        die("review key must contain only lowercase ASCII letters and digits")
    directory = ENTRY_TYPES[a.kind].directory
    path = root / "entries" / directory / f"{a.key}.md"
    if any((root / "entries" / contract.directory / f"{a.key}.md").exists() for contract in ENTRY_TYPES.values()):
        die(f"entry key already exists: {a.key}", 1)
    entries = load(root)
    if any(entry.get("key") == a.key for entry in entries):
        die(f"entry key already exists: {a.key}", 1)
    if any(str(entry.get("id")) == a.id for entry in entries):
        die(f"a review with id {a.id!r} already exists", 1)
    values = (a.id, a.title, a.editor, *(a.speaker or []), a.channel, a.publisher, a.published)
    if any(value and ("\n" in value or "\r" in value) for value in values):
        die("entry metadata must fit on one line")
    if not re.fullmatch(r"([a-z][a-z0-9.]*:[^ ]+|https?://[^ ]+)", a.id):
        die("review id must be a registered CURIE or URL")
    optional = {
        "speaker": a.speaker,
        "channel": a.channel,
        "publisher": a.publisher,
        "published": a.published,
    }
    allowed = {"paper": set(), "talk": {"speaker", "channel", "published"}, "post": {"publisher", "published"}}
    unexpected = sorted(name for name, value in optional.items() if value and name not in allowed[a.kind])
    if unexpected:
        die(f"{a.kind} init does not accept {', '.join(f'--{name}' for name in unexpected)}")
    if a.kind == "talk":
        if not all((a.speaker, a.channel, a.published)):
            die("talk init requires --speaker, --channel, and --published")
    elif a.kind == "post" and not all((a.publisher, a.published)):
        die("post init requires --publisher and --published")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = render_scaffold(
        a.kind,
        entry_id=a.id,
        title=a.title,
        editor=a.editor,
        speaker=a.speaker,
        channel=a.channel,
        publisher=a.publisher,
        published=a.published,
    )
    path.write_text(body, encoding="utf-8")
    print(path)
    return 0


def _writable_review_root(command: str) -> Path:
    if env := os.environ.get("PAPERSTACK_DIR"):
        root = Path(env).expanduser()
    else:
        root = git_toplevel()
        if root is None or not is_corpus(root):
            selected = _active_corpus()
            root = Path(selected.location) if selected and selected.kind == "path" else None
    if root is None or not is_corpus(root):
        die(f"{command} requires a local corpus working tree, path profile, or PAPERSTACK_DIR")
    return root


def _review_audit(entries: list[dict], *, json_output: bool) -> int:
    from . import dblp_index

    if not dblp_index.installed():
        die("review audit requires the local index; run `paperstack index dblp install`")
    report = []
    entries = [entry for entry in entries if kind(entry) == "paper"]
    try:
        matches_by_entry = dblp_index.search_many([str(entry.get("title", "")) for entry in entries])
    except RuntimeError as exc:
        die(f"DBLP index lookup failed: {exc}")
    for entry, matches in zip(entries, matches_by_entry, strict=True):
        item = {
            "key": entry["key"],
            "id": entry.get("id"),
            "title": entry.get("title"),
            "current_venue": entry.get("venue"),
            "status": "no_match" if not matches else "matched" if len(matches) == 1 else "ambiguous",
            "dblp_matches": [
                {key: match.get(key) for key in ("title", "dblp_key", "venue", "year", "entry_type", "doi", "url")}
                for match in matches
            ],
        }
        report.append(item)
    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report:
            if item["status"] == "no_match":
                continue
            match = item["dblp_matches"][0]
            venue = item["current_venue"] or "(absent)"
            print(f"{item['key']}\n  review: {venue}\n  dblp:   {match['venue']} ({match['dblp_key']})")
            if item["status"] == "ambiguous":
                print(f"  status: {len(item['dblp_matches'])} candidates; manual review required")
        counts = {
            status: sum(item["status"] == status for item in report) for status in ("matched", "ambiguous", "no_match")
        }
        print("  ".join(f"{key}: {value}" for key, value in counts.items()), file=sys.stderr)
    return 0


def _run_review(a: argparse.Namespace) -> int:
    cmd = a.review_cmd
    if cmd == "sync":
        repo = _remote_repo()
        if repo is None:
            die("review sync requires a GitHub corpus; select one with `paperstack corpus use` or set PAPERSTACK_REPO")
        cache = RemoteCache(repo)
        if not sync(repo, force=a.force):
            die(f"could not reach {repo}; check `gh auth status`")
        print(f"{cache.corpus}  {(local_sha(cache) or '?')[:7]}", file=sys.stderr)
        return 0

    if cmd == "init":
        return _review_init(_writable_review_root("review init"), a)
    if cmd == "citations":
        from . import citations

        root = _writable_review_root("review citations")
        entries = load(root)
        try:
            document, changed = citations.update(root, entries, live=a.fetch)
        except (OSError, TypeError, ValueError) as exc:
            die(f"citation update failed: {exc}")
        if a.json:
            print(json.dumps(document, indent=2))
        else:
            suffix = " (cached values only; pass --fetch for live counts)" if not a.fetch else ""
            print(
                f"{root / 'entries' / 'citations.json'}: "
                f"{len(document['papers'])}/{len(citations.collect(entries))} entries, "
                f"{changed} changed{suffix}"
            )
        return 0
    root = resolve(offline=getattr(a, "offline", False))
    if cmd == "check":
        from . import entry_types

        argv = ["--root", str(root)]
        if a.style:
            argv.append("--style")
        return entry_types.main(argv)
    entries = load(root)
    if not entries:
        die(f"no entries under {root}")
    if cmd == "audit":
        return _review_audit(entries, json_output=a.json)
    if cmd == "show":
        hits = find(entries, a.query)
        if not hits:
            die(f"nothing matches {a.query!r}", 1)
        if len(hits) > 1:
            warn(f"{a.query!r} matches {len(hits)} reviews:")
            for e in hits:
                print(f"  {one_line(e)}", file=sys.stderr)
            return 2
        if a.json:
            print(json.dumps(hits[0], ensure_ascii=False, indent=2, default=str))
        else:
            show(hits[0], a.brief)
        return 0

    if cmd == "search":
        ql = a.query.lower()
        hits = [e for e in entries if ql in searchable(e)]
    else:
        hits = [
            e
            for e in entries
            if (not a.quality or quality(e) == a.quality)
            and (not a.kind or kind(e) == a.kind)
            and (not a.tag or a.tag in as_list(e.get("tags")))
        ]
    if a.json:
        print(json.dumps([strip_body(e) for e in hits], ensure_ascii=False, indent=2, default=str))
    else:
        order = {"excellent": 0, "good": 1, "fair": 2, "poor": 3}
        for e in sorted(hits, key=lambda e: (order.get(quality(e), 9), e["key"])):
            print(one_line(e))
        print(f"{len(hits)} entries", file=sys.stderr)
    return 0 if hits else 1


def _paper_cache() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return Path(os.environ.get("PAPERSTACK_PAPERS_DIR", base / "paperstack" / "papers"))


def _run_paper(a: argparse.Namespace) -> int:
    from . import credentials, metadata

    offline = getattr(a, "offline", False)
    if a.paper_cmd == "verify-publication":
        if offline:
            die("publication verification requires network access")
        try:
            result = metadata.verify_publication(a.title)
        except (credentials.CredentialsError, RuntimeError, ValueError) as exc:
            die(f"publication verification failed: {exc}")
        if a.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(result["message"])
            if result["recommendation"]:
                print(result["recommendation"])
            print("Checked: " + ", ".join(item["source"] for item in result["checked"]))
            for item in result["checked"]:
                if item["error"]:
                    print(f"Warning: {item['source']}: {item['error']}", file=sys.stderr)
        return 1 if result["status"] == "error" else 0
    if a.paper_cmd in ("authors", "citations", "references"):
        if offline:
            die(f"paper {a.paper_cmd} requires network access")
        from . import semantic_scholar

        try:
            if a.paper_cmd == "authors":
                result = semantic_scholar.authors(a.paper_ref, limit=a.limit, offset=a.offset)
            else:
                result = semantic_scholar.graph(
                    a.paper_ref,
                    a.paper_cmd,
                    limit=a.limit,
                    offset=a.offset,
                )
        except credentials.CredentialsError as exc:
            die(f"configuration failed: {exc}")
        except ValueError as exc:
            die(f"Semantic Scholar lookup failed: {exc}")
        metadata.print_results(result, json_output=a.json)
        return 0 if result["status"] == "ok" else 1

    ref = None
    if a.paper_cmd == "metadata":
        try:
            ref = metadata.PaperRef.parse(a.paper_ref)
        except ValueError as exc:
            die(str(exc))
    if offline and a.paper_cmd in ("metadata", "search"):
        from . import dblp_index

        local_search = a.paper_cmd == "search" and a.source == "dblp"
        local_metadata = (
            a.paper_cmd == "metadata" and a.source == "dblp" and ref is not None and ref.kind in ("dblp", "doi")
        )
        if not dblp_index.installed() or not (local_search or local_metadata):
            die("offline paper lookup is available only through an installed DBLP index")
    if a.paper_cmd == "metadata":
        source = a.source.replace("-", "_")
        enabled = None if source == "all" else {source}
        try:
            results = metadata.fetch_all(ref, enabled, local_only=offline)
        except credentials.CredentialsError as exc:
            die(f"configuration failed: {exc}")
        except RuntimeError as exc:
            die(f"DBLP index lookup failed: {exc}")
        if a.normalized_json:
            metadata.print_results(metadata.normalize_results(results), json_output=True)
        else:
            metadata.print_results(results, json_output=a.json)
        return 0 if any(item["status"] == "ok" for item in results) else 1
    if a.paper_cmd == "search":
        semantic_filters = [
            flag
            for flag, selected in (
                ("--offset", a.offset),
                ("--year", a.year),
                ("--field-of-study", a.fields_of_study),
                ("--open-access", a.open_access),
            )
            if selected
        ]
        arxiv_filters = [
            flag
            for flag, selected in (
                ("--category", a.categories),
                ("--date-from", a.date_from),
                ("--date-to", a.date_to),
                ("--sort", a.sort != "relevance"),
            )
            if selected
        ]
        openreview_filters = [
            flag
            for flag, selected in (
                ("--exact-title", a.exact_title),
                ("--openreview-status", a.openreview_status),
            )
            if selected
        ]
        try:
            if a.source != "openreview" and openreview_filters:
                die(f"--source openreview is required for {', '.join(openreview_filters)}")
            if a.source == "arxiv":
                from . import arxiv

                if semantic_filters:
                    die(f"--source semantic-scholar is required for {', '.join(semantic_filters)}")
                result = arxiv.search(
                    a.query,
                    categories=a.categories,
                    date_from=a.date_from,
                    date_to=a.date_to,
                    limit=a.limit,
                    sort=a.sort,
                )
            elif a.source == "semantic-scholar":
                from . import semantic_scholar

                if arxiv_filters:
                    die(f"--source arxiv is required for {', '.join(arxiv_filters)}")
                result = semantic_scholar.search(
                    a.query,
                    limit=a.limit,
                    offset=a.offset,
                    year=a.year,
                    fields_of_study=a.fields_of_study,
                    open_access=a.open_access,
                )
            else:
                requirements = []
                if semantic_filters:
                    requirements.append(f"--source semantic-scholar is required for {', '.join(semantic_filters)}")
                if arxiv_filters:
                    requirements.append(f"--source arxiv is required for {', '.join(arxiv_filters)}")
                if requirements:
                    die("; ".join(requirements))
                result = metadata.search(
                    a.source,
                    a.query,
                    limit=a.limit,
                    local_only=offline,
                    exact_title=a.exact_title,
                    openreview_status=a.openreview_status,
                )
        except credentials.CredentialsError as exc:
            die(f"configuration failed: {exc}")
        except RuntimeError as exc:
            die(f"DBLP index lookup failed: {exc}")
        except ValueError as exc:
            die(f"paper search failed: {exc}")
        if a.normalized_json:
            metadata.print_results(metadata.normalize_results(result), json_output=True)
        else:
            metadata.print_results(result, json_output=a.json)
        return 0 if result["status"] == "ok" else 1

    try:
        ref = metadata.PaperRef.parse(a.paper_ref)
    except ValueError as exc:
        die(str(exc))
    if ref.kind != "arxiv":
        die(f"paper {a.paper_cmd} currently requires an arxiv: reference")
    if a.paper_cmd == "read":
        from .content import arxiv_source

        arxiv_source.CACHE_DIR = _paper_cache()
        cached_source = arxiv_source.CACHE_DIR / ref.value / "src"
        if offline and a.refresh:
            die("--offline and --refresh cannot be used together")
        if offline and (not cached_source.is_dir() or not arxiv_source._tex_candidates(cached_source)):
            die(f"no complete cached source for arxiv:{ref.value}")
        argv = ["read", ref.value]
        if a.outline:
            argv.append("--outline")
        elif a.section_id:
            argv.extend(["--section", a.section_id])
        if a.refresh:
            argv.append("--refresh")
        argv.extend(["--start", str(a.start), "--max-chars", str(a.max_chars)])
        arxiv_source.main(argv)
        return 0
    if a.paper_cmd == "pdf":
        from .content import arxiv_pdf

        arxiv_pdf.CACHE_DIR = _paper_cache()
        cached_pdf = arxiv_pdf._cached_conversion(
            arxiv_pdf.CACHE_DIR / ref.value,
            allow_native_fallback=offline,
        )
        if offline and cached_pdf is None:
            die(f"no usable cached PDF conversion for arxiv:{ref.value}")
        if not arxiv_pdf.convert(ref.value, allow_native_fallback=offline):
            return 1
        return 0
    raise AssertionError(a.paper_cmd)


def _run_index(a: argparse.Namespace) -> int:
    from . import dblp_index

    try:
        if a.dblp_cmd == "status":
            info = dblp_index.status()
        elif a.dblp_cmd == "install":
            warn("installing the selected-venue DBLP snapshot; this may take a minute")
            info = dblp_index.install()
        elif a.dblp_cmd == "update":
            warn("checking for a newer selected-venue DBLP snapshot")
            info = dblp_index.update()
        elif a.dblp_cmd == "build":
            from . import dblp_build

            info = dblp_build.build(
                a.output,
                snapshot=a.snapshot,
                base=a.base,
                selected_venues=a.venues,
                selected_years=a.years,
                base_url=a.base_url,
                minimum_records=a.minimum_records,
                delay=a.delay,
            )
        elif a.dblp_cmd == "install-file":
            info = dblp_index.install_file(a.path)
        elif a.dblp_cmd == "remove":
            if not a.yes:
                die("index removal requires --yes")
            dblp_index.remove()
            info = {"installed": False, "removed": True}
        else:
            raise AssertionError(a.dblp_cmd)
    except (OSError, RuntimeError, TypeError, ValueError, tarfile.TarError) as exc:
        die(f"DBLP index operation failed: {exc}")
    if a.json:
        print(json.dumps(info, indent=2))
    else:
        for key, value in info.items():
            print(f"{key}: {value}")
    return 0


def _run_viewer(a: argparse.Namespace) -> int:
    from . import viewer

    root = resolve(offline=a.offline)
    output = a.output.expanduser()
    if not output.is_absolute():
        output = Path.cwd() / output
    try:
        count = viewer.build(root, output)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        die(f"viewer build failed: {exc}")
    print(f"{output.resolve()}: {count} entries", file=sys.stderr)
    if a.viewer_cmd == "serve":
        print(f"serving Paperstack at http://{a.host}:{a.port}/index.html", file=sys.stderr)
        try:
            viewer.serve(output, a.host, a.port)
        except OSError as exc:
            die(f"viewer server failed: {exc}")
    return 0


def main() -> int:
    formatter = argparse.RawDescriptionHelpFormatter
    ap = argparse.ArgumentParser(
        prog="paperstack",
        description=__doc__.split("\n")[0],
        epilog="""typical workflow:
  paperstack corpus add work --path ~/reviews      register a local corpus
  paperstack corpus use work                       select its profile
  paperstack review search "flow matching"          find an existing critical read
  paperstack paper metadata arxiv:2403.19622       inspect source records
  paperstack paper read arxiv:2403.19622 --outline inspect the paper structure
  paperstack paper read arxiv:2403.19622 --section 3

Use `corpus` to select authored data, `review` to query it, and `paper` for external facts and contents.
Run `paperstack <group> --help` for group-specific examples.""",
        formatter_class=formatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    corpus = sub.add_parser(
        "corpus",
        help="named local or GitHub corpus profiles",
        description="Register and select corpus sources. Credentials remain managed by gh.",
    )
    corpus_sub = corpus.add_subparsers(dest="corpus_cmd", required=True)
    _corpus_commands(corpus_sub)

    config = sub.add_parser("config", help="manage local settings and provider credentials")
    config_sub = config.add_subparsers(dest="config_cmd", required=True)
    _config_commands(config_sub)

    viewer = sub.add_parser("viewer", help="build or serve the static corpus viewer")
    viewer_sub = viewer.add_subparsers(dest="viewer_cmd", required=True)
    s = viewer_sub.add_parser("build", help="build static files from the selected corpus")
    s.add_argument("--output", type=Path, default=Path("_site"))
    _offline(s)
    s = viewer_sub.add_parser("serve", help="build and serve the selected corpus")
    s.add_argument("--output", type=Path, default=Path("_site"))
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    _offline(s)

    review = sub.add_parser(
        "review",
        help="authored critical reads and judgments",
        description="Search, read, and maintain the authored entry corpus.",
        epilog="""examples:
  paperstack review search "flow matching"
  paperstack review show example2026paperstack --brief
  paperstack review show arxiv:2410.24164 --json
  paperstack review list --quality poor --tag vla

Use `paperstack paper ...` when you need external metadata or the paper body.""",
        formatter_class=formatter,
    )
    review_sub = review.add_subparsers(dest="review_cmd", required=True)
    _review_commands(review_sub)

    paper = sub.add_parser(
        "paper",
        help="source-backed paper facts and contents",
        description="Inspect external source records and read arXiv paper contents without choosing a citation.",
        epilog="""examples:
  paperstack paper search "Attention Is All You Need" --source dblp
  paperstack paper metadata arxiv:2403.19622
  paperstack paper read arxiv:2403.19622 --outline
  paperstack paper read arxiv:2403.19622 --section 3

Use `paperstack review ...` to find or read an authored critical judgment.""",
        formatter_class=formatter,
    )
    paper_sub = paper.add_subparsers(dest="paper_cmd", required=True)
    s = paper_sub.add_parser("metadata", help="fetch source records without choosing between them")
    s.add_argument("paper_ref", help="arxiv:, doi:, dblp:, or openreview: reference")
    s.add_argument(
        "--source",
        choices=("all", "semantic-scholar", "dblp", "crossref", "openreview", "acl-anthology", "arxiv"),
        default="all",
    )
    _output(s, normalized=True)
    _offline(s)
    s = paper_sub.add_parser("search", help="search one metadata source")
    s.add_argument("query", help="paper title or other source-specific search text")
    s.add_argument(
        "--source",
        choices=("semantic-scholar", "dblp", "crossref", "openreview", "arxiv"),
        default="semantic-scholar",
    )
    s.add_argument("--limit", type=int, default=10, help="maximum results")
    s.add_argument("--offset", type=int, default=0, help="Semantic Scholar result offset")
    s.add_argument("--year", help="Semantic Scholar year or year range")
    s.add_argument("--field-of-study", action="append", dest="fields_of_study")
    s.add_argument("--open-access", action="store_true", help="require an open-access PDF")
    s.add_argument("--category", action="append", dest="categories", help="arXiv category; repeat to combine")
    s.add_argument("--date-from", help="earliest arXiv submission date")
    s.add_argument("--date-to", help="latest arXiv submission date")
    s.add_argument("--sort", choices=("relevance", "date"), default="relevance")
    s.add_argument("--exact-title", action="store_true", help="require a normalized exact OpenReview title")
    s.add_argument(
        "--openreview-status",
        choices=("submission", "accepted", "withdrawn"),
        help="filter OpenReview forum records by inferred status",
    )
    _output(s, normalized=True)
    _offline(s)
    s = paper_sub.add_parser("verify-publication", help="resolve a title through publication sources")
    s.add_argument("title", help="exact paper title")
    _output(s)
    _offline(s)
    for command in ("authors", "citations", "references"):
        s = paper_sub.add_parser(command, help=f"inspect Semantic Scholar {command}")
        s.add_argument("paper_ref", help="arxiv:, doi:, s2:, corpus:, acl:, pmid:, or mag: reference")
        s.add_argument("--limit", type=int, default=100)
        s.add_argument("--offset", type=int, default=0)
        _output(s)
        _offline(s)
    s = paper_sub.add_parser("read", help="read the LaTeX body, outline, or one section")
    s.add_argument("paper_ref", help="arxiv: reference")
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--outline", action="store_true", help="print numbered section headings only")
    mode.add_argument("--section", dest="section_id", help="print one section by outline number")
    s.add_argument("--refresh", action="store_true", help="replace the cached arXiv source")
    s.add_argument("--start", type=int, default=0, help="start at this character offset")
    s.add_argument("--max-chars", type=int, default=0, help="truncate output after this many characters")
    _offline(s)
    s = paper_sub.add_parser("pdf", help="download and convert a native PDF submission")
    s.add_argument("paper_ref", help="arxiv: reference")
    _offline(s)

    index = sub.add_parser("index", help="optional local lookup indexes")
    index_sub = index.add_subparsers(dest="index_cmd", required=True)
    dblp = index_sub.add_parser("dblp", help="selected-venue DBLP index")
    dblp_sub = dblp.add_subparsers(dest="dblp_cmd", required=True)
    for command in ("status", "install", "update"):
        _output(dblp_sub.add_parser(command))
    s = dblp_sub.add_parser("build", help="build or refresh a snapshot directly from DBLP")
    s.add_argument("output", type=Path)
    s.add_argument("--snapshot", required=True, help="YYYY.MM.DD snapshot version")
    s.add_argument("--base", type=Path, help="existing DBLP Parquet to merge into")
    s.add_argument("--venue", action="append", dest="venues")
    s.add_argument("--year", action="append", type=int, dest="years")
    s.add_argument("--base-url", default="https://dblp.org")
    s.add_argument("--delay", type=float, default=0.25)
    s.add_argument("--minimum-records", type=int, default=250_000)
    _output(s)
    s = dblp_sub.add_parser("install-file", help="verify and install a locally built snapshot")
    s.add_argument("path", type=Path)
    _output(s)
    s = dblp_sub.add_parser("remove")
    s.add_argument("--yes", action="store_true")
    _output(s)

    a = ap.parse_args()

    if a.cmd == "corpus":
        return _run_corpus(a)
    if a.cmd == "config":
        return _run_config(a)
    if a.cmd == "viewer":
        return _run_viewer(a)
    if a.cmd == "paper":
        return _run_paper(a)
    if a.cmd == "index":
        return _run_index(a)
    return _run_review(a)


if __name__ == "__main__":
    sys.exit(main())
