"""Read an arXiv LaTeX source or inspect its section structure.

Uses latexpand from PATH, or the vendored copy via Perl. Does not execute TeX.
Section ids follow source heading order. Used by `paperstack paper read`.
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

_CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
CACHE_DIR = Path(os.environ.get("PAPERSTACK_PAPERS_DIR", _CACHE_ROOT / "paperstack" / "papers"))

LEVELS = {"section": 1, "subsection": 2, "subsubsection": 3}

HEAD_RE = re.compile(r"\\(section|subsection|subsubsection)\s*\*?\s*(?=[\[{])")
TEX_SUFFIXES = {".tex", ".ltx", ".latex"}
VENDORED_LATEXPAND = Path(__file__).parent / "vendor" / "latexpand"
LATEXPAND_ARGS = ("--keep-comments", "--empty-comments", "--fatal", "--define", r"subfile=\input")


def _fetch_bytes(url: str, timeout: int = 60) -> bytes | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "paperstack/1.0 (+arxiv e-print fetch)",
            "Accept": "*/*",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            if e.code == 404 or attempt == 2:
                return None
            time.sleep(5)
        except (urllib.error.URLError, OSError, TimeoutError):
            if attempt < 2:
                time.sleep(5)
                continue
            return None
    return None


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> int:
    """Extract regular files without path traversal."""
    n = 0
    root = dest.resolve()
    for m in tar.getmembers():
        if not (m.isreg() or m.isdir()):
            continue
        target = (root / m.name).resolve()
        if target != root and root not in target.parents:
            continue
        if m.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        src = tar.extractfile(m)
        if src is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read())
        n += 1
    return n


def _unpack(raw: bytes, dest: Path) -> int:
    """Unpack a tar or bare TeX response; return zero for PDFs."""
    body = raw
    if raw[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(raw)
        except (gzip.BadGzipFile, EOFError, zlib.error):
            return 0
    if body[:5] == b"%PDF-":
        return 0
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:*") as tar:
            return _safe_extract(tar, dest)
    except tarfile.TarError:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "main.tex").write_bytes(body)
        return 1


def _ensure_source(arxiv_id: str, refresh: bool = False) -> Path:
    paper_dir = CACHE_DIR / arxiv_id
    src = paper_dir / "src"
    if not refresh and src.is_dir() and _tex_candidates(src):
        return src
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    raw = _fetch_bytes(url)
    if raw is None:
        sys.exit(f"{arxiv_id}: could not fetch {url}")
    paper_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".src-", dir=paper_dir) as tmp:
        staged = Path(tmp) / "new"
        staged.mkdir()
        unpacked = _unpack(raw, staged)
        if not unpacked or not _tex_candidates(staged):
            reason = "no LaTeX source (PDF-only submission?)" if not unpacked else "no .tex files in archive"
            sys.exit(f"{arxiv_id}: {reason} at {url}")

        previous = Path(tmp) / "previous"
        if src.exists():
            src.rename(previous)
        try:
            staged.rename(src)
        except OSError:
            if previous.exists():
                previous.rename(src)
            raise
    return src


def _tex_candidates(src: Path) -> list[Path]:
    out = []
    for p in sorted(src.rglob("*")):
        if not p.is_file() or "__MACOSX" in p.parts:
            continue
        if p.suffix.lower() in TEX_SUFFIXES or (not p.suffix and p.stat().st_size < 2_000_000):
            out.append(p)
    return out


def _read(p: Path) -> str:
    return p.read_bytes().decode("utf-8", errors="replace")


def _mask_comments(text: str) -> str:
    """Mask comments without changing offsets."""
    out = []
    for line in text.splitlines(keepends=True):
        i, n = 0, len(line)
        while i < n:
            c = line[i]
            if c == "\\":
                i += 2
                continue
            if c == "%":
                nl = len(line) - len(line.rstrip("\r\n"))
                out.append(line[:i] + " " * (n - i - nl) + line[n - nl :])
                break
            i += 1
        else:
            out.append(line)
    return "".join(out)


def _balanced(text: str, start: int, open_ch: str, close_ch: str) -> tuple[str, int] | None:
    """Return balanced content and the position after its closing delimiter."""
    if start >= len(text) or text[start] != open_ch:
        return None
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return None


def _flatten(path: Path, src: Path) -> str | None:
    """Flatten one root with latexpand, or return None when it is unusable."""
    command = [shutil.which("latexpand") or "perl"]
    if command[0] == "perl":
        command.append(str(VENDORED_LATEXPAND))
    try:
        proc = subprocess.run(
            [*command, *LATEXPAND_ARGS, str(path.relative_to(src))],
            cwd=src,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError:
        sys.exit("latexpand not found and Perl is unavailable for the vendored copy")
    except subprocess.TimeoutExpired:
        return None
    return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else None


def _body_span(masked: str) -> tuple[int, int]:
    """Return the document body without preamble headings."""
    lo = masked.find(r"\begin{document}")
    start = lo + len(r"\begin{document}") if lo >= 0 else 0
    hi = masked.rfind(r"\end{document}")
    return start, (hi if hi > start else len(masked))


_MACRO_RE = re.compile(r"\\(?:(?:new|renew|provide)command\*?\s*\{?|def\s*)\\([A-Za-z@]+)\}?")


def _collect_macros(masked: str) -> dict[str, str]:
    """Collect zero-argument macro bodies used in headings."""
    macros: dict[str, str] = {}
    for m in _MACRO_RE.finditer(masked):
        i = m.end()
        while i < len(masked) and masked[i] in " \t":
            i += 1
        if i >= len(masked) or masked[i] != "{":
            continue
        bal = _balanced(masked, i, "{", "}")
        if bal is not None:
            macros[m.group(1)] = bal[0]
    return macros


def _find_sections(text: str) -> list[dict]:
    """Parse sections with dotted ids, levels, titles, and text."""
    masked_full = _mask_comments(text)
    macros = _collect_macros(masked_full)
    lo, hi = _body_span(masked_full)
    body, masked = text[lo:hi], masked_full[lo:hi]
    found = []
    for m in HEAD_RE.finditer(masked):
        i = m.end()
        if masked[i] == "[":
            opt = _balanced(masked, i, "[", "]")
            if opt is None:
                continue
            i = opt[1]
            while i < len(masked) and masked[i].isspace():
                i += 1
            if i >= len(masked) or masked[i] != "{":
                continue
        arg = _balanced(masked, i, "{", "}")
        if arg is None:
            continue
        # Keep empty headings so subsection nesting remains intact.
        title = _clean_title(arg[0], macros) or "(untitled)"
        found.append({"level": LEVELS[m.group(1)], "title": title, "start": m.start()})

    counters = [0, 0, 0]
    for k, s in enumerate(found):
        lvl = s["level"]
        counters[lvl - 1] += 1
        for j in range(lvl, 3):
            counters[j] = 0
        for j in range(lvl - 1):
            counters[j] = max(counters[j], 1)
        s["id"] = ".".join(str(c) for c in counters[:lvl])
        s["end"] = len(body)
        for nxt in found[k + 1 :]:
            if nxt["level"] <= lvl:
                s["end"] = nxt["start"]
                break
        s["text"] = body[s["start"] : s["end"]]
    return found


_DROP_CMDS = ("label", "footnote", "thanks", "vspace", "hspace", "protect", "index")
_SPACE_CMDS = re.compile(r"\\(?:quad|qquad|,|;|:|!|\s)")


def _clean_title(s: str, macros: dict[str, str] | None = None) -> str:
    for _ in range(4):
        if not macros:
            break
        new = re.sub(
            r"\\([A-Za-z@]+)\s*(?:\{\})?",
            lambda m: macros.get(m.group(1), m.group(0)),
            s,
        )
        if new == s:
            break
        s = new
    for cmd in _DROP_CMDS:
        while True:
            m = re.search(r"\\" + cmd + r"\s*\{", s)
            if not m:
                break
            bal = _balanced(s, m.end() - 1, "{", "}")
            if bal is None:
                break
            s = s[: m.start()] + s[bal[1] :]
    while True:
        m = re.search(r"\\texorpdfstring\s*\{", s)
        if not m:
            break
        first = _balanced(s, m.end() - 1, "{", "}")
        if first is None:
            break
        second = _balanced(s, first[1], "{", "}")
        if second is None:
            break
        s = s[: m.start()] + second[0] + s[second[1] :]
    for _ in range(6):
        new = re.sub(r"\\[a-zA-Z]+\*?\s*\{([^{}]*)\}", r"\1", s)
        if new == s:
            break
        s = new
    s = s.replace("\\\\", " ")
    s = _SPACE_CMDS.sub(" ", s)
    s = re.sub(r"\\([a-zA-Z]+)\*?", r"\1", s)
    s = re.sub(r"\\(.)", r"\1", s)
    s = re.sub(r"[${}$~]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _load_document(arxiv_id: str, refresh: bool) -> tuple[str, list[dict]]:
    src = _ensure_source(arxiv_id, refresh)
    cands = _tex_candidates(src)
    scored = []
    for p in cands:
        head = _mask_comments(_read(p)[:200_000])
        if r"\documentclass" not in head:
            continue
        flat = _flatten(p, src)
        if flat is None:
            continue
        sections = _find_sections(flat)
        scored.append((r"\begin{document}" in head, len(sections), -len(p.parts), flat, sections))
    if not scored:  # Fall back when no file declares a document class.
        for p in cands:
            flat = _flatten(p, src)
            if flat is None:
                continue
            sections = _find_sections(flat)
            scored.append((False, len(sections), -len(p.parts), flat, sections))
    if not scored:
        sys.exit(f"{arxiv_id}: no usable .tex file in {src}")
    best = max(scored, key=lambda t: (t[0], t[1], t[2]))
    if not best[4]:
        sys.exit(f"{arxiv_id}: LaTeX source found but no \\section commands in it")
    masked = _mask_comments(best[3])
    lo, hi = _body_span(masked)
    return best[3][lo:hi], best[4]


def _load(arxiv_id: str, refresh: bool) -> list[dict]:
    return _load_document(arxiv_id, refresh)[1]


def _print_chunk(text: str, start: int, max_chars: int) -> None:
    start = max(0, start)
    chunk = text[start : start + max_chars] if max_chars else text[start:]
    print(chunk)
    shown = start + len(chunk)
    if shown < len(text):
        print(
            f"[truncated: {start}..{shown} of {len(text)} chars; --start {shown} for more]",
            file=sys.stderr,
        )


def cmd_list(args: argparse.Namespace) -> None:
    for s in _load(args.arxiv_id, args.refresh):
        print(f"{s['id']}\t{s['level']}\t{s['title']}")


def cmd_section(args: argparse.Namespace) -> None:
    sections = _load(args.arxiv_id, args.refresh)
    want = args.section_id.strip()
    hit = next((s for s in sections if s["id"] == want or s["title"] == want), None)
    if hit is None:
        hit = next((s for s in sections if s["title"].lower() == want.lower()), None)
    if hit is None:
        avail = ", ".join(f"{s['id']} {s['title']}" for s in sections[:20])
        sys.exit(f"{args.arxiv_id}: no section {want!r}. Available: {avail}")
    _print_chunk(hit["text"], args.start, args.max_chars)


def cmd_read(args: argparse.Namespace) -> None:
    body, sections = _load_document(args.arxiv_id, args.refresh)
    if args.outline:
        for section in sections:
            print(f"{section['id']}\t{section['level']}\t{section['title']}")
        return
    if args.section_id:
        want = args.section_id.strip()
        hit = next((s for s in sections if s["id"] == want or s["title"] == want), None)
        if hit is None:
            hit = next((s for s in sections if s["title"].lower() == want.lower()), None)
        if hit is None:
            avail = ", ".join(f"{s['id']} {s['title']}" for s in sections[:20])
            sys.exit(f"{args.arxiv_id}: no section {want!r}. Available: {avail}")
        body = hit["text"]
    _print_chunk(body, args.start, args.max_chars)


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="print <id>\\t<level>\\t<title> per section")
    p_list.add_argument("arxiv_id")
    p_list.set_defaults(func=cmd_list)

    p_sec = sub.add_parser("section", help="print raw LaTeX of one section")
    p_sec.add_argument("arxiv_id")
    p_sec.add_argument("section_id", help="dotted id (3.2) or exact title")
    p_sec.add_argument("--max-chars", type=int, default=0, help="0 = whole section")
    p_sec.add_argument("--start", type=int, default=0)
    p_sec.set_defaults(func=cmd_section)

    p_read = sub.add_parser("read", help="print the document body, outline, or one section")
    p_read.add_argument("arxiv_id")
    mode = p_read.add_mutually_exclusive_group()
    mode.add_argument("--outline", action="store_true")
    mode.add_argument("--section", dest="section_id", help="dotted id (3.2) or exact title")
    p_read.add_argument("--max-chars", type=int, default=0, help="0 = all remaining text")
    p_read.add_argument("--start", type=int, default=0)
    p_read.add_argument("--refresh", action="store_true", help="refetch even if cached")
    p_read.set_defaults(func=cmd_read)

    for p in (p_list, p_sec):
        p.add_argument("--refresh", action="store_true", help="refetch even if cached")

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
