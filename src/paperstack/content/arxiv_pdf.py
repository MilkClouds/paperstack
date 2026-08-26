"""Convert arXiv PDF submissions to Markdown with selective OCR.

Used by `paperstack paper pdf` after the source fetcher reports no TeX.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
CACHE_DIR = Path(os.environ.get("PAPERSTACK_PAPERS_DIR", _CACHE_ROOT / "paperstack" / "papers"))
CONVERTER = "pdf-inspector"
MIN_MARKDOWN_CHARS = 100
OCR_RUNTIME_GUIDE = "https://github.com/firecrawl/pdf-inspector/blob/main/docs/ocr-runtime.md"


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


def _cached_conversion(d: Path) -> Path | None:
    md_path = d / "paper.md"
    meta_path = d / "meta.json"
    if not md_path.is_file() or md_path.stat().st_size < MIN_MARKDOWN_CHARS or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return md_path if meta.get("converter") == CONVERTER else None


def _converter_version() -> str:
    try:
        return importlib.metadata.version(CONVERTER)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _ocr_reasons(items) -> list[dict]:
    return [{"page": item.page, "reasons": list(item.reasons)} for item in items]


def _page_provenance(page) -> dict:
    provenance = page.provenance
    model = provenance.ocr_model
    return {
        "page": page.page_number,
        "source": provenance.source,
        "ocr_model": None if model is None else {"name": model.name, "revision": model.revision},
        "render_dpi": provenance.render_dpi,
        "ocr_confidence": provenance.ocr_confidence,
        "hosted_recommended": provenance.hosted_recommended,
        "warnings": list(provenance.warnings),
        "timings": {
            "render_ms": provenance.timings.render_ms,
            "ocr_ms": provenance.timings.ocr_ms,
            "assembly_ms": provenance.timings.assembly_ms,
        },
    }


def _base_meta(arxiv_id: str, url: str, markdown: str) -> dict:
    encoded = markdown.encode("utf-8")
    return {
        "schema_version": 1,
        "arxiv_id": arxiv_id,
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "url": url,
        "converter": CONVERTER,
        "converter_version": _converter_version(),
    }


def _ocr_meta(arxiv_id: str, url: str, markdown: str, result) -> dict:
    meta = _base_meta(arxiv_id, url, markdown)
    meta.update(
        {
            "conversion_mode": "selective_ocr",
            "quality": "partial" if result.pages_recommending_hosted else "complete",
            "page_count": result.page_count,
            "pages_recommended_for_ocr": list(result.pages_recommended_for_ocr),
            "pages_routed_to_ocr": list(result.pages_routed_to_ocr),
            "pages_recommending_hosted": list(result.pages_recommending_hosted),
            "ocr_reasons_by_page": _ocr_reasons(result.ocr_reasons_by_page),
            "pages_with_tables": list(result.pages_with_tables),
            "pages_with_columns": list(result.pages_with_columns),
            "pages": [_page_provenance(page) for page in result.pages],
        }
    )
    return meta


def _native_fallback_meta(arxiv_id: str, url: str, markdown: str, result, error: ValueError) -> dict:
    meta = _ocr_meta(arxiv_id, url, markdown, result)
    meta["conversion_mode"] = "native_fallback"
    meta["quality"] = "partial" if result.pages_recommended_for_ocr else "complete"
    meta["ocr_error"] = str(error)
    return meta


def _usable(markdown: str | None) -> bool:
    return markdown is not None and len(markdown.strip()) >= MIN_MARKDOWN_CHARS


def _is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) == b"%PDF"
    except OSError:
        return False


def convert(arxiv_id: str) -> bool:
    d = CACHE_DIR / arxiv_id
    cached = _cached_conversion(d)
    if cached is not None:
        print(f"{arxiv_id}: cached at {cached}")
        return True

    try:
        import pdf_inspector
    except ImportError:
        print(
            "PDF conversion needs `paperstack-cli[pdf]`; reinstall with "
            "`uv tool install --force 'paperstack-cli[pdf]'`.",
            file=sys.stderr,
        )
        return False

    url = f"https://arxiv.org/pdf/{arxiv_id}"
    d.mkdir(parents=True, exist_ok=True)
    pdf_path = d / "paper.pdf"
    if not _is_pdf(pdf_path):
        raw = _fetch(url)
        if raw is None:
            print(f"{arxiv_id}: could not fetch {url}", file=sys.stderr)
            return False
        if not raw.startswith(b"%PDF"):
            print(f"{arxiv_id}: {url} did not return a PDF", file=sys.stderr)
            return False
        pdf_path.write_bytes(raw)

    try:
        result = pdf_inspector.process_pdf_with_ocr(str(pdf_path), mode="auto")
        md = result.markdown
        meta = _ocr_meta(arxiv_id, url, md, result)
    except ValueError as error:
        try:
            native = pdf_inspector.process_pdf_with_ocr(str(pdf_path), mode="off")
        except ValueError:
            native = None
        if native is None or not _usable(native.markdown):
            print(f"{arxiv_id}: PDF conversion failed: {error}", file=sys.stderr)
            print(f"OCR runtime setup: {OCR_RUNTIME_GUIDE}", file=sys.stderr)
            return False
        md = native.markdown
        meta = _native_fallback_meta(arxiv_id, url, md, native, error)
        print(f"{arxiv_id}: OCR unavailable; cached {meta['quality']} native extraction", file=sys.stderr)

    if not _usable(md):
        chars = len(md.strip()) if md else 0
        print(f"{arxiv_id}: converted to only {chars} chars, treat as a failure", file=sys.stderr)
        return False

    md_path = d / "paper.md"
    md_path.write_text(md, encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
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
