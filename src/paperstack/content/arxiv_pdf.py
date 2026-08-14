"""Convert native-PDF arXiv submissions to Markdown.

Used by `paperstack paper pdf` after the source fetcher reports no TeX.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
CACHE_DIR = Path(os.environ.get("PAPERSTACK_PAPERS_DIR", _CACHE_ROOT / "paperstack" / "papers"))


def _fetch(url: str, timeout: int = 60) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": "paperstack/1.0 (+arxiv pdf fetch)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            if attempt == 2:
                return None
            time.sleep(5)
        except (urllib.error.URLError, OSError, TimeoutError):
            if attempt == 2:
                return None
            time.sleep(5)
    return None


def convert(arxiv_id: str) -> bool:
    d = CACHE_DIR / arxiv_id
    md_path = d / "paper.md"
    if md_path.exists() and md_path.stat().st_size > 1000:
        print(f"{arxiv_id}: cached at {md_path}")
        return True

    try:
        import pymupdf4llm
    except ImportError:
        print(
            "PDF conversion needs `paperstack-cli[pdf]`; reinstall with "
            "`uv tool install --force 'paperstack-cli[pdf]'`.",
            file=sys.stderr,
        )
        return False

    url = f"https://arxiv.org/pdf/{arxiv_id}"
    raw = _fetch(url)
    if raw is None:
        print(f"{arxiv_id}: could not fetch {url}", file=sys.stderr)
        return False
    if not raw.startswith(b"%PDF"):
        print(f"{arxiv_id}: {url} did not return a PDF", file=sys.stderr)
        return False

    d.mkdir(parents=True, exist_ok=True)
    pdf_path = d / "paper.pdf"
    pdf_path.write_bytes(raw)
    md = pymupdf4llm.to_markdown(str(pdf_path))
    if len(md) < 500:
        print(f"{arxiv_id}: converted to only {len(md)} chars, treat as a failure", file=sys.stderr)
        return False

    md_path.write_text(md, encoding="utf-8")
    (d / "meta.json").write_text(
        json.dumps(
            {
                "arxiv_id": arxiv_id,
                "bytes": len(md),
                "sha256": hashlib.sha256(md.encode("utf-8")).hexdigest(),
                "url": url,
                "converter": "pymupdf4llm",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"{arxiv_id}: {len(md)} chars -> {md_path}")
    return True


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit("pass one or more arXiv ids")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    failures = [a for a in argv if not convert(a)]
    if failures:
        sys.exit(f"failed: {' '.join(failures)}")


if __name__ == "__main__":
    main(sys.argv[1:])
