"""Read selected venue-years from DBLP's XML dump (https://dblp.org/xml/dblp.xml.gz).

DBLP asks bulk users to query the dump locally rather than the live API. Records are turned into the same BibTeX
field shape the API returns, so ``dblp_index._row`` stays the only row constructor.
"""

from __future__ import annotations

import gzip
import html.entities
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from xml.etree import ElementTree

from .dblp_catalog import VENUES, toc_query, years

RECORD_TAGS = {"article", "inproceedings", "proceedings"}
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_DISAMBIGUATION = re.compile(r"\s+\d{4}$")
_PART = re.compile(r"-\d+$")


def _resolve_entities(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _XML_ENTITIES or name not in html.entities.name2codepoint:
            return match.group(0)
        return f"&#{html.entities.name2codepoint[name]};"

    return _ENTITY.sub(replace, text)


def _chunks(path: Path, size: int = 1 << 20) -> Iterator[str]:
    # The dump declares its entities in an external DTD, which ElementTree does not load.
    opener = gzip.open if path.suffix == ".gz" else open
    carry = ""
    with opener(path, "rt", encoding="iso-8859-1") as handle:
        while chunk := handle.read(size):
            text = carry + chunk
            cut = text.rfind("&")
            if cut != -1 and ";" not in text[cut:]:
                text, carry = text[:cut], text[cut:]
            else:
                carry = ""
            yield _resolve_entities(text)
    if carry:
        yield _resolve_entities(carry)


def iter_records(path: Path) -> Iterator[ElementTree.Element]:
    parser = ElementTree.XMLPullParser(events=("start", "end"))
    depth = 0
    for text in _chunks(path):
        parser.feed(text)
        for event, element in parser.read_events():
            if event == "start":
                depth += 1
                continue
            depth -= 1
            if depth == 1:
                if element.tag in RECORD_TAGS:
                    yield element
                element.clear()


def _toc(element: ElementTree.Element) -> str | None:
    url = element.findtext("url")
    if not url or not url.startswith("db/"):
        return None
    return url.split("#", 1)[0].removesuffix(".html")


def targets(venues: Iterable[str], year_filter: set[int] | None = None) -> dict[str, tuple[str, int]]:
    """Map each catalog table-of-contents page to its (venue, year)."""
    found: dict[str, tuple[str, int]] = {}
    for venue in venues:
        definition = VENUES[venue]
        suffixes = ["", *definition.get("suffixes", []), *definition.get("extra_tocs", [])]
        for year in years(venue):
            if year_filter is not None and year not in year_filter:
                continue
            for suffix in suffixes:
                toc = toc_query(venue, year, suffix).removeprefix("toc:").removesuffix(".bht:")
                found[toc] = (venue, year)
    return found


def _text(element: ElementTree.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None:
        return None
    value = " ".join("".join(child.itertext()).split())
    return value or None


def _value(text: str) -> str:
    return text.replace("{", "").replace("}", "")


def _bibtex(record: dict[str, object], venue_title: str | None) -> str:
    fields = [("author", " and ".join(record["authors"]))] if record["authors"] else []
    fields.append(("title", record["title"]))
    if venue_title:
        fields.append(("journal" if record["tag"] == "article" else "booktitle", venue_title))
    fields.append(("year", record["year"]))
    if record["doi"]:
        fields.append(("doi", record["doi"]))
    body = ",\n".join(f"  {name} = {{{_value(str(value))}}}" for name, value in fields if value)
    return f"@{record['tag']}{{DBLP:{record['key']},\n{body}\n}}"


def load(
    path: Path,
    venues: Iterable[str],
    year_filter: set[int] | None = None,
) -> dict[tuple[str, int], list[str]]:
    """Return DBLP-style BibTeX entries per (venue, year) for every catalog record in the dump."""
    wanted = targets(venues, year_filter)
    proceedings_titles: dict[str, str] = {}
    records: list[tuple[tuple[str, int], dict[str, object]]] = []
    for element in iter_records(path):
        key = element.get("key", "")
        if element.tag == "proceedings" and (title := _text(element, "title")):
            proceedings_titles[key] = title
        toc = _toc(element)
        if toc is None:
            continue
        slot = wanted.get(toc) or wanted.get(_PART.sub("", toc))
        if slot is None:
            continue
        links = [ee.text or "" for ee in element.findall("ee")]
        doi = next(
            (link.removeprefix("https://doi.org/") for link in links if link.startswith("https://doi.org/")), None
        )
        people = element.findall("editor" if element.tag == "proceedings" else "author")
        records.append(
            (
                slot,
                {
                    "tag": element.tag,
                    "key": key,
                    "authors": [_DISAMBIGUATION.sub("", " ".join("".join(p.itertext()).split())) for p in people],
                    "title": _text(element, "title"),
                    "year": element.findtext("year"),
                    "doi": doi,
                    "crossref": element.findtext("crossref"),
                    "venue": _text(element, "journal") or _text(element, "booktitle"),
                },
            )
        )
    grouped: dict[tuple[str, int], list[str]] = {}
    for slot, record in records:
        if not record["title"]:
            continue
        # Proceedings titles end in a period that the API BibTeX drops; journal abbreviations keep theirs.
        proceedings = proceedings_titles.get(str(record["crossref"]))
        venue_title = proceedings.rstrip(".") if proceedings else record["venue"]
        if record["tag"] == "proceedings":
            venue_title = None
        grouped.setdefault(slot, []).append(_bibtex(record, venue_title))
    return grouped
