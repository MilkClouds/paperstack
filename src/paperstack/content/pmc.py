"""Read PMC JATS full text with source identity and offline caching."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import metadata, pubmed

FLOATS = ("table-wrap", "table-wrap-group", "fig", "fig-group")


def license_text(node: ET.Element) -> str:
    # A license mixes a URL element, paragraphs and bare text; separate the block children, keep everything.
    parts = [node.text or ""]
    for child in node:
        content = "".join(child.itertext())
        parts.append(f" {content} " if child.tag.rsplit("}", 1)[-1] in ("license-p", "license_ref") else content)
        parts.append(child.tail or "")
    return " ".join("".join(parts).split())


def parse(raw: bytes, pmcid: str) -> dict:
    root = ET.fromstring(raw)
    article = root if root.tag == "article" else root.find("article")
    if article is None:
        raise ValueError("PMC response contains no article")
    ids = {node.get("pub-id-type"): pubmed.text(node) for node in article.findall("front/article-meta/article-id")}
    actual = ids.get("pmcid") or ids.get("pmc")
    if not actual or "PMC" + actual.upper().removeprefix("PMC") != pmcid:
        raise ValueError("PMC full-text identifier does not match the requested article")
    body = article.find("body")
    if body is None or not pubmed.text(body):
        raise ValueError("PMC full text is unavailable; an abstract is not a full article")
    sections = []

    def blocks(node):
        if node.tag == "table":
            return "\n".join(
                " | ".join(pubmed.text(cell) for cell in row if cell.tag in ("td", "th")) for row in node.iter("tr")
            )
        if node.tag == "p" and any(child.tag in FLOATS for child in node):
            # Publishers nest tables and figures inside paragraphs; keep them as separate blocks.
            parts, inline = [], [node.text or ""]
            for child in node:
                if child.tag in FLOATS:
                    parts += [" ".join("".join(inline).split()), blocks(child)]
                    inline = []
                else:
                    inline.append("".join(child.itertext()))
                inline.append(child.tail or "")
            parts.append(" ".join("".join(inline).split()))
            return "\n\n".join(part for part in parts if part)
        if node.tag in ("p", "title", "label", "ref", "disp-formula"):
            return pubmed.text(node)
        children = [blocks(child) for child in node]
        return "\n\n".join(part for part in children if part) if len(node) else pubmed.text(node)

    def visit(node, number, title):
        parts = []
        sections.append({"id": number, "title": title, "parts": parts})
        subsection = 0
        for child in node:
            if child.tag == "title":
                continue
            if child.tag == "sec":
                subsection += 1
                ident = f"{number}.{subsection}"
                parts.append({"section": ident})
                visit(child, ident, pubmed.text(child.find("title")) or "Untitled section")
            elif part := blocks(child):
                parts.append({"text": part})

    abstract = article.find("front/article-meta/abstract")
    if abstract is not None:
        visit(abstract, "0", "Abstract")
    # Preserve unsectioned paragraphs, figures and tables as well as nested sections.
    visit(body, "1", "Body")
    back = article.find("back")
    if back is not None:
        visit(back, "2", "Back matter")
    floats = article.find("floats-group")
    if floats is not None:
        visit(floats, "3", "Figures and tables")
    return {
        "pmcid": pmcid,
        "title": pubmed.text(article.find("front/article-meta/title-group/article-title")),
        "licenses": [license_text(node) for node in article.findall("front/article-meta/permissions/license")],
        "sections": sections,
    }


def _pmcid(ref):
    if ref.kind == "pmcid":
        return ref.value
    if ref.kind == "pmid":
        record = pubmed.fetch(ref)
        if record["status"] == "ok" and record["response"].get("pmcid"):
            return metadata.PaperRef.parse("pmcid:" + record["response"]["pmcid"]).value
    result = pubmed.europe_fetch(ref)
    if result["status"] == "ok":
        for row in result["response"]["matches"]:
            if row.get("pmcid"):
                return metadata.PaperRef.parse("pmcid:" + row["pmcid"]).value
    raise ValueError("No PMC full text found; inspect metadata or supply a verified --pdf-url")


def load(ref: metadata.PaperRef, cache: Path, *, offline=False, refresh=False) -> dict:
    if offline and refresh:
        raise ValueError("--offline and --refresh cannot be used together")
    identity = f"{ref.kind}:{ref.value}"
    path = cache / "pmc" / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
    if path.exists() and not refresh:
        try:
            saved = json.loads(path.read_text())
            if not isinstance(saved, dict) or not isinstance(saved.get("xml"), str):
                raise TypeError("invalid PMC cache format")
            raw = saved["xml"].encode()
            if saved["reference"] != identity or hashlib.sha256(raw).hexdigest() != saved["sha256"]:
                raise ValueError("invalid PMC cache identity or checksum")
            document = parse(raw, saved["pmcid"])
            return {**document, "source_url": saved["source_url"]}
        except (ValueError, KeyError, TypeError, ET.ParseError) as exc:
            if offline:
                raise ValueError("no usable cached PMC full text") from exc
    if offline:
        raise ValueError("no cached PMC full text; read this reference online first")
    pmcid = _pmcid(ref)
    candidates = [
        (f"{pubmed.EUTILS}/efetch.fcgi", {"db": "pmc", "id": pmcid[3:], "retmode": "xml", "tool": "paperstack"}),
        (f"{pubmed.EUROPE_PMC}/{pmcid}/fullTextXML", None),
    ]
    errors = []
    for url, params in candidates:
        try:
            raw = metadata.request(url, params)
            document = parse(raw, pmcid)
            # Canonical UTF-8 makes the cached XML checksum independent of its original encoding.
            raw = ET.tostring(ET.fromstring(raw), encoding="utf-8")
            source_url = url + "?" + urllib.parse.urlencode(params) if params else url
            saved = {
                "reference": identity,
                "pmcid": pmcid,
                "source_url": source_url,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "xml": raw.decode(),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as handle:
                    json.dump(saved, handle)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return {**document, "source_url": source_url}
        except (urllib.error.URLError, OSError, ValueError, ET.ParseError) as exc:
            errors.append(f"{url}: {exc}")
    raise ValueError("PMC full text unavailable (metadata/abstract may still exist): " + "; ".join(errors))


def render(document: dict, *, outline=False, section: str | None = None) -> str:
    sections = document["sections"]
    if outline:
        return "\n".join(f"{row['id']} {row['title']}" for row in sections)
    if section and not any(row["id"] == section for row in sections):
        raise ValueError(f"unknown PMC section: {section}; use --outline")

    heading = f"# {document['title']}\n\nSource: {document['source_url']}\nPMCID: {document['pmcid']}"
    if document["licenses"]:
        heading += "\nLicense: " + "\n".join(document["licenses"])
    by_id = {row["id"]: row for row in sections}

    def render_section(ident):
        row = by_id[ident]
        content = [f"## {ident} {row['title']}"]
        for part in row["parts"]:
            content.append(render_section(part["section"]) if "section" in part else part["text"])
        return "\n\n".join(content)

    roots = [section] if section else [row["id"] for row in sections if "." not in row["id"]]
    return heading + "\n\n" + "\n\n".join(render_section(ident) for ident in roots)
