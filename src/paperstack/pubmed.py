"""PubMed records and Europe PMC discovery, without publisher access assumptions."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from . import metadata

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def text(node: ET.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def parse_articles(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    if error := text(root.find(".//ERROR")):
        raise ValueError(error)
    records = []
    for item in root.findall("PubmedArticle"):
        citation = item.find("MedlineCitation")
        article = item.find("MedlineCitation/Article")
        if citation is None or article is None:
            continue
        pmid = text(citation.find("PMID"))
        identifiers = {node.get("IdType"): text(node) for node in item.findall("PubmedData/ArticleIdList/ArticleId")}
        abstract = [
            {"label": node.get("Label") or node.get("NlmCategory"), "text": text(node)}
            for node in article.findall("Abstract/AbstractText")
        ]
        authors = [
            text(author.find("CollectiveName"))
            or " ".join(
                filter(
                    None,
                    [text(author.find("ForeName")) or text(author.find("Initials")), text(author.find("LastName"))],
                )
            )
            for author in article.findall("AuthorList/Author")
        ]
        date = article.find("Journal/JournalIssue/PubDate")
        records.append(
            {
                "id": f"pmid:{pmid}",
                "pmid": pmid,
                "pmcid": identifiers.get("pmc"),
                "doi": identifiers.get("doi") or text(article.find('ELocationID[@EIdType="doi"]')) or None,
                "title": text(article.find("ArticleTitle")),
                "authors": authors,
                "abstract": "\n".join(
                    f"{part['label']}: {part['text']}" if part["label"] else part["text"] for part in abstract
                ),
                "abstract_sections": abstract,
                "journal": text(article.find("Journal/Title")),
                "published": " ".join(text(child) for child in date) if date is not None else "",
                "publication_types": [text(node) for node in article.findall("PublicationTypeList/PublicationType")],
                "mesh_terms": [
                    {"id": node.get("UI"), "name": text(node), "major_topic": node.get("MajorTopicYN") == "Y"}
                    for node in citation.findall("MeshHeadingList/MeshHeading/DescriptorName")
                ],
                "notices": [
                    {
                        "type": node.get("RefType"),
                        "pmid": text(node.find("PMID")),
                        "citation": text(node.find("RefSource")),
                    }
                    for node in citation.findall("CommentsCorrectionsList/CommentsCorrections")
                ],
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            }
        )
    return records


def _articles(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    return parse_articles(
        metadata.request(
            f"{EUTILS}/efetch.fcgi",
            {
                "db": "pubmed",
                "id": ",".join(ids),
                "retmode": "xml",
                "tool": "paperstack",
            },
        )
    )


def search(query: str, *, limit: int = 10, offset: int = 0, sort: str = "relevance") -> dict:
    if not query.strip() or not 1 <= limit <= 100 or offset < 0 or offset + limit > 9999:
        raise ValueError("PubMed requires a query, limit 1..100, and offset + limit <= 9999")
    if sort not in ("relevance", "date"):
        raise ValueError("sort must be relevance or date")
    url = f"{EUTILS}/esearch.fcgi"

    def run():
        data = metadata._get_json(
            url,
            {
                "db": "pubmed",
                "term": query,
                "retmax": limit,
                "retstart": offset,
                "retmode": "json",
                "sort": "pub date" if sort == "date" else "relevance",
                "tool": "paperstack",
            },
        )
        if data.get("error"):
            raise ValueError(str(data["error"]))
        result = data.get("esearchresult")
        if not isinstance(result, dict):
            return metadata._result("pubmed", url, error="PubMed returned no search result")
        if result.get("ERROR"):
            raise ValueError(str(result["ERROR"]))
        ids = result.get("idlist", [])
        records = {row["pmid"]: row for row in _articles(ids)}
        return metadata._result(
            "pubmed",
            url,
            {
                "query": query,
                "effective_query": result.get("querytranslation"),
                "total": int(result.get("count", 0)),
                "offset": offset,
                "matches": [records[ident] for ident in ids if ident in records],
                "unavailable_pmids": [ident for ident in ids if ident not in records],
                "warnings": result.get("warninglist", {}),
            },
        )

    return metadata._safe(run, "pubmed", url)


def europe_search(query: str, *, limit: int = 10) -> dict:
    if not query.strip() or not 1 <= limit <= 100:
        raise ValueError("Europe PMC requires a query and limit 1..100")
    url = f"{EUROPE_PMC}/search"

    def run():
        data = metadata._get_json(url, {"query": query, "format": "json", "resultType": "core", "pageSize": limit})
        if data.get("errMsg"):
            raise ValueError(str(data["errMsg"]))
        return metadata._result(
            "europe_pmc",
            url,
            {
                "query": query,
                "total": data.get("hitCount", 0),
                "matches": data.get("resultList", {}).get("result", []),
            },
        )

    return metadata._safe(run, "europe_pmc", url)


def europe_fetch(ref: metadata.PaperRef) -> dict:
    if ref.kind == "pmid":
        query = f"EXT_ID:{ref.value} AND SRC:MED"
    elif ref.kind == "pmcid":
        query = f"PMCID:{ref.value}"
    elif ref.kind == "doi":
        query = 'DOI:"' + ref.value.replace('"', '\\"') + '"'
    else:
        return {"source": "europe_pmc", "status": "unavailable", "reason": "requires PMID, PMCID, or DOI"}
    result = europe_search(query, limit=10)
    if result["status"] == "ok":
        matches = result["response"]["matches"]
        # Search engines may tokenize identifiers; only exact identifier matches resolve a paper.
        field = {"pmid": "id", "pmcid": "pmcid", "doi": "doi"}[ref.kind]
        matches = [
            row
            for row in matches
            if str(row.get(field, "")).casefold() == ref.value.casefold()
            and (ref.kind != "pmid" or row.get("source") == "MED")
        ]
        result["response"]["matches"] = matches
        if not matches:
            result.update(status="unavailable", reason="no exact Europe PMC record for this identifier")
    return result


def fetch(ref: metadata.PaperRef) -> dict:
    url = f"{EUTILS}/efetch.fcgi"

    def run():
        pmid = ref.value if ref.kind == "pmid" else None
        if not pmid:
            resolved = europe_fetch(ref)
            if resolved["status"] != "ok":
                return {**resolved, "source": "pubmed"}
            pmid = next(
                (
                    row.get("pmid") or (row.get("id") if row.get("source") == "MED" else None)
                    for row in resolved["response"]["matches"]
                    if row.get("pmid") or (row.get("source") == "MED" and row.get("id"))
                ),
                None,
            )
        if not pmid:
            return {"source": "pubmed", "status": "unavailable", "reason": "no PMID for this record"}
        records = _articles([pmid])
        record = next((row for row in records if row["pmid"] == pmid), None)
        if record is None:
            return {"source": "pubmed", "status": "unavailable", "reason": "PMID not found or unsupported record type"}
        return metadata._result("pubmed", record["url"], record)

    return metadata._safe(run, "pubmed", url)
