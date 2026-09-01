"""Read-only BibTeX validation driven by a small TOML style contract."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Entry:
    kind: str
    key: str
    raw: str
    line: int
    leading: str


def field(raw: str, name: str) -> str | None:
    match = re.search(rf"(?im)(?:^|,)\s*{re.escape(name)}\s*=\s*", raw)
    if not match:
        return None
    start = match.end()
    if start >= len(raw):
        return None
    opening = raw[start]
    if opening == "{":
        depth = 1
        position = start + 1
        while position < len(raw):
            if raw[position] == "\\":
                position += 2
                continue
            if raw[position] == "{":
                depth += 1
            elif raw[position] == "}":
                depth -= 1
                if depth == 0:
                    return raw[start + 1 : position].strip()
            position += 1
        return None
    if opening == '"':
        position = start + 1
        while position < len(raw):
            if raw[position] == "\\":
                position += 2
                continue
            if raw[position] == '"':
                return raw[start + 1 : position].strip()
            position += 1
        return None
    match = re.match(r"[^,\n})]+", raw[start:])
    return match.group().strip() if match else None


def _skip_value(raw: str, position: int, end: int) -> int | None:
    if position >= end:
        return None
    if raw[position] == "{":
        depth = 1
        position += 1
        while position < end and depth:
            if raw[position] == "\\":
                position += 2
                continue
            depth += raw[position] == "{"
            depth -= raw[position] == "}"
            position += 1
        return position if depth == 0 else None
    if raw[position] == '"':
        position += 1
        while position < end:
            if raw[position] == "\\":
                position += 2
                continue
            if raw[position] == '"':
                return position + 1
            position += 1
        return None
    match = re.match(r"[A-Za-z0-9_.:+/-]+", raw[position:end])
    return position + match.end() if match else None


def _valid_fields(raw: str) -> bool:
    first_comma = raw.find(",")
    if first_comma < 0:
        return False
    position = first_comma + 1
    end = len(raw) - 1
    while position < end:
        while position < end and (raw[position].isspace() or raw[position] == ","):
            position += 1
        if position >= end:
            return True
        name = re.match(r"[A-Za-z][A-Za-z0-9_-]*", raw[position:end])
        if not name:
            return False
        position += name.end()
        while position < end and raw[position].isspace():
            position += 1
        if position >= end or raw[position] != "=":
            return False
        position += 1
        while position < end and raw[position].isspace():
            position += 1
        while True:
            skipped = _skip_value(raw, position, end)
            if skipped is None:
                return False
            position = skipped
            while position < end and raw[position].isspace():
                position += 1
            if position >= end or raw[position] != "#":
                break
            position += 1
            while position < end and raw[position].isspace():
                position += 1
        if position < end and raw[position] != ",":
            return False
    return True


def parse(raw: str) -> tuple[list[Entry], list[dict]]:
    entries = []
    errors = []
    previous_end = 0
    for match in re.finditer(r"(?i)@(\w+)\s*([({])", raw):
        if match.start() < previous_end:
            continue
        line_start = raw.rfind("\n", 0, match.start()) + 1
        prefix = raw[line_start : match.start()]
        comment = prefix.find("%")
        if comment >= 0 and (comment == 0 or prefix[comment - 1] != "\\"):
            continue
        kind = match.group(1).lower()
        opening = match.group(2)
        closing = "}" if opening == "{" else ")"
        depth = 1
        brace_depth = 0
        quoted = False
        position = match.end()
        while position < len(raw) and depth:
            char = raw[position]
            if char == "\\":
                position += 2
                continue
            if char == '"':
                quoted = not quoted
            elif not quoted:
                if opening == "(" and char == "{":
                    brace_depth += 1
                elif opening == "(" and char == "}" and brace_depth:
                    brace_depth -= 1
                elif char == opening:
                    depth += 1
                elif char == closing and not brace_depth:
                    depth -= 1
            position += 1
        line = raw.count("\n", 0, match.start()) + 1
        if depth:
            errors.append({"line": line, "code": "syntax", "message": "unterminated BibTeX entry"})
            break
        block = raw[match.start() : position]
        previous = raw[previous_end : match.start()]
        previous_end = position
        if kind in ("comment", "preamble", "string"):
            continue
        key_match = re.match(r"(?is)@\w+\s*[({]\s*([^,]+?)\s*,", block)
        if not key_match:
            errors.append({"line": line, "code": "syntax", "message": "entry has no citation key"})
            continue
        if not _valid_fields(block):
            errors.append({"line": line, "code": "syntax", "message": "malformed BibTeX field list"})
        entries.append(Entry(kind, key_match.group(1).strip(), block, line, previous))
    return entries, errors


def _style(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read style {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError("BibTeX style must be a TOML table")
    return value


def _table(style: dict, name: str) -> dict:
    value = style.get(name, {})
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a TOML table")
    return value


def lint(path: Path, style_path: Path | None = None) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read bibliography {path}: {exc}") from exc
    style = _style(style_path)
    entries, issues = parse(raw)
    max_authors = _table(style, "authors").get("max")
    key_pattern = _table(style, "citation_keys").get("pattern")
    provenance = _table(style, "provenance")
    venue_style = _table(style, "venues")
    allowed = venue_style.get("allowed", [])
    required = _table(style, "fields").get("required", {})
    if max_authors is not None and (not isinstance(max_authors, int) or max_authors < 1):
        raise ValueError("authors.max must be a positive integer")
    if key_pattern is not None and not isinstance(key_pattern, str):
        raise ValueError("citation_keys.pattern must be a string")
    if key_pattern:
        try:
            re.compile(key_pattern)
        except re.error as exc:
            raise ValueError(f"citation_keys.pattern is invalid: {exc}") from exc
    if not isinstance(required, dict) or any(not isinstance(value, list) for value in required.values()):
        raise ValueError("fields.required must map entry types to field lists")
    if not isinstance(allowed, list) or any(not isinstance(value, str) for value in allowed):
        raise ValueError("venues.allowed must be a list")
    allowed_venues = set(allowed)
    provenance_pattern = provenance.get("pattern", r"(?im)^\s*%\s*(?:source|provenance)\s*:")
    try:
        re.compile(provenance_pattern)
    except (re.error, TypeError) as exc:
        raise ValueError(f"provenance.pattern is invalid: {exc}") from exc
    seen = {}
    for entry in entries:
        if entry.key in seen:
            issues.append(
                {
                    "line": entry.line,
                    "key": entry.key,
                    "code": "citation_key.duplicate",
                    "message": f"duplicate citation key; first defined on line {seen[entry.key]}",
                }
            )
        else:
            seen[entry.key] = entry.line
        if max_authors is not None and (authors := field(entry.raw, "author")):
            names = [name.strip() for name in re.split(r"\s+and\s+", authors) if name.strip()]
            explicit = [name for name in names if name.casefold() != "others"]
            if len(explicit) > max_authors:
                issues.append(
                    {
                        "line": entry.line,
                        "key": entry.key,
                        "code": "authors.max",
                        "message": f"{len(explicit)} authors exceeds maximum {max_authors}",
                    }
                )
        required_fields = required.get("all", []) + required.get(entry.kind, []) if isinstance(required, dict) else []
        for name in required_fields:
            if not field(entry.raw, str(name)):
                issues.append(
                    {
                        "line": entry.line,
                        "key": entry.key,
                        "code": "field.required",
                        "message": f"missing required field {name}",
                    }
                )
        if key_pattern and not re.fullmatch(key_pattern, entry.key):
            issues.append(
                {
                    "line": entry.line,
                    "key": entry.key,
                    "code": "citation_key.pattern",
                    "message": "citation key does not match style pattern",
                }
            )
        if provenance.get("required") and not re.search(provenance_pattern, entry.leading):
            issues.append(
                {
                    "line": entry.line,
                    "key": entry.key,
                    "code": "provenance.required",
                    "message": "missing provenance comment before entry",
                }
            )
        if allowed_venues:
            venue = field(entry.raw, "booktitle") or field(entry.raw, "journal")
            if venue and venue not in allowed_venues:
                issues.append(
                    {
                        "line": entry.line,
                        "key": entry.key,
                        "code": "venue.allowed",
                        "message": f"venue is not allowed by style: {venue}",
                    }
                )
    return {"status": "ok" if not issues else "invalid", "path": str(path), "entries": len(entries), "issues": issues}
