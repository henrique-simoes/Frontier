"""Semantic Scholar: author resolution, author papers, references (backward) and citations (forward)."""
from __future__ import annotations

from .. import http
from ..models import Author, Item

API = "https://api.semanticscholar.org/graph/v1"
PAPER_FIELDS = "title,abstract,externalIds,publicationDate,authors,url,citationCount,venue"


def _paper_item(p: dict, source: str, via: str | None = None, kind: str = "paper") -> Item | None:
    if not p or not p.get("title"):
        return None
    ext = p.get("externalIds") or {}
    pid = f"arxiv:{ext['ArXiv']}" if ext.get("ArXiv") else f"s2:{p.get('paperId')}"
    url = f"https://arxiv.org/abs/{ext['ArXiv']}" if ext.get("ArXiv") else (p.get("url") or "")
    return Item(id=pid, kind=kind, title=p["title"], url=url, source=source, summary=p.get("abstract") or "",
                published=p.get("publicationDate"),
                authors=[Author(name=a.get("name", ""), ids={"s2": a["authorId"]} if a.get("authorId") else {})
                         for a in p.get("authors") or [] if a.get("name")],
                metrics={"citations": p.get("citationCount") or 0}, raw={"venue": p.get("venue")}, via_entity=via)


def paper(paper_id: str) -> dict | None:
    """paper_id like 'arXiv:2610.31667'. Returns S2 record with authors (incl. ids + affiliations)."""
    try:
        return http.get(f"{API}/paper/{paper_id}",
                        params={"fields": PAPER_FIELDS + ",authors.affiliations,authors.name"}, ttl=7 * 86400)
    except Exception:
        return None


def references(paper_id: str, limit: int = 40) -> list[Item]:
    data = http.get(f"{API}/paper/{paper_id}/references", params={"fields": PAPER_FIELDS, "limit": limit},
                    ttl=14 * 86400)
    return [it for d in data.get("data", []) if (it := _paper_item(d.get("citedPaper"), "s2_refs", kind="reference"))]


def citations(paper_id: str, limit: int = 40) -> list[Item]:
    data = http.get(f"{API}/paper/{paper_id}/citations", params={"fields": PAPER_FIELDS, "limit": limit},
                    ttl=3 * 86400)
    return [it for d in data.get("data", []) if (it := _paper_item(d.get("citingPaper"), "s2_cites"))]


def fetch_author(entity: dict, author_id: str) -> list[Item]:
    data = http.get(f"{API}/author/{author_id}/papers",
                    params={"fields": PAPER_FIELDS, "limit": 20, "sort": "publicationDate:desc"}, ttl=12 * 86400)
    rows = sorted(data.get("data", []), key=lambda p: p.get("publicationDate") or "", reverse=True)[:12]
    return [it for p in rows if (it := _paper_item(p, "s2_author", via=entity["id"]))]
