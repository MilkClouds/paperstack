"""Entry contracts, scaffolds, and corpus validation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class EntryContract:
    directory: str
    required: tuple[str, ...]
    lead: str
    sections: tuple[str, ...]
    quality: bool = False


ENTRY_TYPES = {
    "paper": EntryContract(
        directory="papers",
        required=("quality",),
        lead="Why read it",
        sections=("What it is / What it shows", "Critical read and limits"),
        quality=True,
    ),
    "talk": EntryContract(
        directory="talks",
        required=("speaker", "channel", "published"),
        lead="Why watch it",
        sections=("What it covers", "Useful ideas and context", "Notes and caveats"),
    ),
    "post": EntryContract(
        directory="posts",
        required=("publisher", "published"),
        lead="Why read it",
        sections=("What it reports", "Useful details", "Provenance and caveats"),
    ),
}
DIRECTORY_KINDS = {contract.directory: kind for kind, contract in ENTRY_TYPES.items()}
QUALITIES = ("excellent", "good", "fair", "poor")


class FrontLoader(yaml.SafeLoader):
    """SafeLoader that preserves YAML 1.1 boolean-like words."""


FrontLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in values if tag != "tag:yaml.org,2002:bool"]
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def entry_paths(root: Path) -> list[Path]:
    """Return entries from the typed corpus layout."""
    entry_root = root / "entries"
    paths: list[Path] = []
    for contract in ENTRY_TYPES.values():
        paths.extend((entry_root / contract.directory).glob("*.md"))
    return sorted(paths)


def kind_for_path(path: Path) -> str:
    return DIRECTORY_KINDS[path.parent.name]


def split_front(text: str) -> tuple[str, str] | None:
    """Split frontmatter on delimiter lines."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    return None


def render_scaffold(
    kind: str,
    *,
    entry_id: str,
    title: str,
    editor: str,
    speaker: list[str] | None = None,
    channel: str | None = None,
    publisher: str | None = None,
    published: str | None = None,
) -> str:
    """Render the canonical scaffold for one entry type."""
    contract = ENTRY_TYPES[kind]
    metadata: list[tuple[str, object]] = [("id", entry_id), ("title", title)]
    if kind == "paper":
        metadata.append(("quality", "TODO"))
    elif kind == "talk":
        metadata.extend((("speaker", speaker or []), ("channel", channel), ("published", published)))
    else:
        metadata.extend((("publisher", publisher), ("published", published)))
    metadata.extend((("tags", []), ("editor", editor)))

    lines = ["---"]
    for key, value in metadata:
        if key == "quality":
            rendered = str(value)
        else:
            rendered = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.extend(("---", "", f"# {title}", "", "**One-liner.** TODO", "", f"**{contract.lead}.** TODO", ""))
    for section in contract.sections:
        placeholder = "**Verdict.** TODO" if kind == "paper" and section == "Critical read and limits" else "TODO"
        lines.extend((f"## {section}", "", placeholder, ""))
    return "\n".join(lines)


@dataclass(frozen=True)
class Diagnostic:
    level: str
    filename: str
    message: str


def _visible_characters(body: str) -> int:
    body = re.sub(r"\]\([^)]+\)", "]", body)
    body = re.sub(r"<https?://[^>]+>", "", body)
    body = re.sub(r"^\s*#{1,6}\s+", "", body, flags=re.MULTILINE)
    body = re.sub(r"^\s*[-*+]\s+", "", body, flags=re.MULTILINE)
    body = re.sub(r"^\s*\|?[-:| ]+\|?\s*$", "", body, flags=re.MULTILINE)
    body = re.sub(r"[*_`|>]", "", body)
    return len(re.sub(r"\s+", "", body))


def _sentence_count(text: str) -> int:
    return text.count(". ")


def _validate_entry(path: Path, known: set[Path], *, style: bool) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    filename = path.name

    def report(level: str, message: str) -> None:
        diagnostics.append(Diagnostic(level, filename, message))

    text = path.read_text(encoding="utf-8")
    parts = split_front(text)
    if parts is None:
        report("ERROR", "no frontmatter")
        return diagnostics
    frontmatter, body = parts
    if not frontmatter.strip():
        report("ERROR", "empty frontmatter")
        return diagnostics
    try:
        metadata = yaml.load(frontmatter, Loader=FrontLoader)
    except yaml.YAMLError:
        report("ERROR", "frontmatter is not valid YAML")
        return diagnostics
    if not isinstance(metadata, dict):
        report("ERROR", "frontmatter is not a mapping")
        return diagnostics

    kind = kind_for_path(path)
    contract = ENTRY_TYPES[kind]
    for key in ("id", "title", "tags", "editor", *contract.required):
        if key not in metadata:
            report("ERROR", f"missing key: {key}")
    if not re.search(r"^# \S", body, flags=re.MULTILINE):
        report("ERROR", "missing '# Name' heading")
    if not re.search(r"^\*\*One-liner\.\*\*", body, flags=re.MULTILINE):
        report("ERROR", "missing One-liner")
    if not re.search(rf"^\*\*{re.escape(contract.lead)}\.\*\*", body, flags=re.MULTILINE):
        report("ERROR", f"missing {contract.lead}")
    for section in contract.sections:
        if not re.search(rf"^## {re.escape(section)}\s*$", body, flags=re.MULTILINE):
            report("ERROR", f"missing '{section}' section")

    quality = str(metadata.get("quality", ""))
    if contract.quality:
        if quality == "TODO":
            report("warn", "quality is still TODO")
        elif quality not in QUALITIES:
            report("ERROR", f"bad quality value: {quality}")
        if re.search(r"^\*\*Read it anyway\.\*\*", body, flags=re.MULTILINE) and quality not in ("fair", "poor"):
            report("ERROR", f"quality is {quality} so 'Read it anyway.' does not apply")
    elif "quality" in metadata:
        report("ERROR", f"{kind} must not carry paper quality")

    entry_id = str(metadata.get("id", ""))
    if not re.fullmatch(r"(?:[a-z][a-z0-9.]*:[^ ]+|https?://[^ ]+)", entry_id):
        report("ERROR", f"id is neither a CURIE nor a URL: {entry_id}")
    filename_year = re.search(r"(?:19|20)\d{2}", path.stem)
    arxiv_year = re.match(r"arxiv:(\d{2})", entry_id, flags=re.IGNORECASE)
    if arxiv_year and (not filename_year or filename_year.group() != f"20{arxiv_year.group(1)}"):
        report(
            "ERROR",
            f"filename year {filename_year.group() if filename_year else ''} but arXiv id says 20{arxiv_year.group(1)}",
        )

    tags = metadata.get("tags", [])
    if not isinstance(tags, list):
        tags = [tags]
    if any(not re.fullmatch(r"[a-z0-9-]+", str(tag)) for tag in tags):
        report("ERROR", "tags must be lowercase")
    for link in re.findall(r"\]\(([^)]+\.md(?:#[^)]*)?)\)", body):
        relative = link.split("#", 1)[0]
        if "://" not in relative and (path.parent / relative).resolve() not in known:
            report("ERROR", f"link to a nonexistent entry: {link}")
    if "TODO" in body and quality != "TODO":
        report("warn", "TODO left in the body")

    if style:
        multi = sum(
            bool(re.search(r"(?:[A-Za-z][A-Za-z]+|\))\.\s+[A-Z]|다\.\s+\S", line))
            for line in body.splitlines()
            if not re.match(r"\s*(?:[|#>]|```|\*\*(?:One-liner|Why read it|Why watch it|Read it anyway)\.)", line)
        )
        if multi:
            report("warn", f"{multi} lines carry more than one sentence")
        lead_lines = {
            label: next((line for line in body.splitlines() if line.startswith(f"**{label}.**")), "")
            for label in ("One-liner", contract.lead)
        }
        if _sentence_count(lead_lines["One-liner"]) > 1:
            report("warn", "One-liner runs past one sentence")
        if _sentence_count(lead_lines[contract.lead]) > 2:
            report("warn", "Why read/watch it runs past two sentences")
        characters = _visible_characters(body)
        if characters > 2500:
            report("warn", f"review body has {characters} visible characters; guide is 2500")
    return diagnostics


def _validate_readme(root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    path = root / "README.md"
    if not path.is_file():
        return diagnostics
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    fenced = False
    for number, line in enumerate(lines, 1):
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        for relative in re.findall(r"\]\((entries/[^)#\s]+\.md)(?:#[^)]*)?\)", line):
            if not re.fullmatch(r"entries/(?:papers|talks|posts)/[a-z0-9]+\.md", relative):
                diagnostics.append(Diagnostic("ERROR", "README.md", f"unsupported entry path: {relative}"))
                continue
            target = root / relative
            if not target.is_file():
                diagnostics.append(Diagnostic("ERROR", "README.md", f"dead link: {relative}"))
                continue
            if line.startswith("|"):
                continue
            metadata, _ = split_front(target.read_text(encoding="utf-8")) or ("", "")
            try:
                parsed = yaml.load(metadata, Loader=FrontLoader) or {}
            except yaml.YAMLError:
                diagnostics.append(Diagnostic("ERROR", "README.md", f"invalid frontmatter: {relative}"))
                continue
            wanted = re.search(r"arxiv:\d{4}\.\d{4,5}", line)
            current_id = str(parsed.get("id", "")) if isinstance(parsed, dict) else ""
            if wanted and wanted.group() != current_id:
                diagnostics.append(
                    Diagnostic("ERROR", "README.md", f"{relative} is listed as {wanted.group()} but its id differs")
                )
            venue = str(parsed.get("venue", "")) if isinstance(parsed, dict) else ""
            normalized_line = " ".join(line.translate(str.maketrans({",": " ", "·": " "})).split())
            normalized_venue = " ".join(venue.translate(str.maketrans({",": " ", "·": " "})).split())
            if venue and normalized_venue not in normalized_line:
                diagnostics.append(
                    Diagnostic("ERROR", "README.md", f'{relative} has venue "{venue}" but the linked line does not')
                )
        if re.search(r"(?:^|[^\[(<`])https?://", line):
            diagnostics.append(Diagnostic("ERROR", "README.md", f"line {number} has an unlinked URL"))
    if fenced:
        diagnostics.append(Diagnostic("ERROR", "README.md", "unclosed code fence"))
    return diagnostics


def validate(root: Path, *, style: bool = False) -> tuple[list[Diagnostic], int]:
    paths = entry_paths(root)
    names = [path.name for path in paths]
    known = {path.resolve() for path in paths}
    diagnostics = [item for path in paths for item in _validate_entry(path, known, style=style)]
    for name in sorted({name for name in names if names.count(name) > 1}):
        diagnostics.append(Diagnostic("ERROR", "entries/", f"duplicate entry key: {name}"))
    diagnostics.extend(_validate_readme(root))
    return sorted(diagnostics, key=lambda item: (item.filename, item.level, item.message)), len(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the authored entry corpus")
    parser.add_argument("--style", action="store_true", help="include prose-length warnings")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    diagnostics, count = validate(args.root, style=args.style)
    for item in diagnostics:
        print(f"  {item.level:<5} {item.filename}: {item.message}", file=sys.stderr)
    errors = sum(item.level == "ERROR" for item in diagnostics)
    warnings = sum(item.level == "warn" for item in diagnostics)
    print(f"{count} entries, {errors} errors, {warnings} warnings", file=sys.stderr)
    return int(errors > 0)


if __name__ == "__main__":
    raise SystemExit(main())
