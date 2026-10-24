from __future__ import annotations

import re
from typing import Iterator

import feedparser

from .. import http
from ..config import config
from ..models import Author, Item
from ..util import age_days

API = "https://export.arxiv.org/api/query"
AI_RE = re.compile(r"\b(neural|deep learning|machine learning|learned|transformer|language model|llm|"
                   r"reinforcement learning|diffusion model|foundation model|generative|artificial intelligence|"
                   r"\bai\b|agent|embedding|gpu|accelerat|inference|training)", re.I)


def _parse(xml: str, source: str, via: str | None = None) -> list[Item]:
    feed = feedparser.parse(xml)
    out = []
    for e in feed.entries:
        m = re.search(r"(\d{4}\.\d{4,5})", e.get("id", ""))
        if not m:
            continue
        aid = m.group(1)
        cats = [t.get("term") for t in e.get("tags", []) if t.get("term")]
        authors = []
        for a in e.get("authors", []):
            aff = a.get("arxiv_affiliation")
            authors.append(Author(name=a.get("name", "").strip(), affiliations=[aff] if aff else []))
        links = [l.get("href") for l in e.get("links", []) if l.get("href")]
        summary = re.sub(r"\s+", " ", e.get("summary", "")).strip()
        links += re.findall(r"https?://github\.com/[\w.-]+/[\w.-]+", summary)
        out.append(Item(
            id=f"arxiv:{aid}", kind="paper", title=re.sub(r"\s+", " ", e.get("title", "")).strip(),
            url=f"https://arxiv.org/abs/{aid}", source=source, summary=summary,
            published=e.get("published"), authors=authors, concepts=[f"arxiv/{c}" for c in cats[:4]],
            links=links, raw={"categories": cats, "comment": e.get("arxiv_comment", "")}, via_entity=via))
    return out


def sweep() -> Iterator[Item]:
    cfg = config()
    lookback = cfg.get("collect.lookback_days", 3)
    adjacent = set(cfg.get("collect.arxiv_adjacent_categories", []))
    for cat in list(cfg.get("collect.arxiv_categories", [])) + sorted(adjacent):
        xml = http.get(API, params={"search_query": f"cat:{cat}", "sortBy": "submittedDate",
                                    "sortOrder": "descending", "max_results": cfg.get("collect.arxiv_per_category", 100)},
                       as_json=False, ttl=3 * 3600)
        for it in _parse(xml, "arxiv"):
            if age_days(it.published) > lookback + 1:
                continue
            if cat in adjacent and not AI_RE.search(f"{it.title} {it.summary}"):
                continue
            yield it


def fetch_author(entity: dict, query: str) -> list[Item]:
    xml = http.get(API, params={"search_query": f'au:"{query}"', "sortBy": "submittedDate",
                                "sortOrder": "descending", "max_results": 15}, as_json=False, ttl=12 * 3600)
    return _parse(xml, "arxiv_author", via=entity["id"])


def fetch_ids(ids: list[str]) -> list[Item]:
    """Resolve arXiv ids mentioned elsewhere (blog posts, Bluesky, HN) into full paper items."""
    out: list[Item] = []
    for k in range(0, len(ids), 50):
        chunk = ids[k:k + 50]
        xml = http.get(API, params={"id_list": ",".join(chunk), "max_results": len(chunk)}, as_json=False, ttl=7 * 86400)
        out.extend(_parse(xml, "arxiv_mention"))
    return out
