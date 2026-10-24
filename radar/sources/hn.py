from __future__ import annotations

import time
from typing import Iterator

from .. import http
from ..config import config
from ..models import Item
from ..util import canonical_id

API = "https://hn.algolia.com/api/v1/search_by_date"
AI_TERMS = ("ai", "llm", "gpt", "claude", "gemini", "model", "neural", "transformer", "agent", "inference",
            "diffusion", "embedding", "vector", "rag", "robot", "gpu", "cuda", "arxiv", "dataset", "training",
            "openai", "anthropic", "deepmind", "mistral", "qwen", "deepseek", "llama", "mcp")


def _is_ai(title: str, url: str) -> bool:
    t = f" {title.lower()} "
    return "arxiv.org" in url or "huggingface.co" in url or any(f" {w}" in t or f"{w} " in t for w in AI_TERMS)


def sweep() -> Iterator[Item]:
    cfg = config()
    since = int(time.time()) - 86400 * cfg.get("collect.lookback_days", 3)
    data = http.get(API, params={"tags": "story", "hitsPerPage": 200,
                                 "numericFilters": f"points>{cfg.get('collect.hn_min_points', 60)},created_at_i>{since}"},
                    ttl=2 * 3600)
    for h in data.get("hits", []):
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        title = h.get("title") or ""
        if title.startswith(("Ask HN", "Tell HN")) or not _is_ai(title, url):
            continue
        cid = canonical_id(url)
        kind = "paper" if cid.startswith("arxiv:") else "repo" if cid.startswith("gh:") else "post"
        yield Item(id=cid, kind=kind, title=title, url=url, source="hn", published=h.get("created_at"),
                   metrics={"hn_points": h.get("points", 0) or 0, "hn_comments": h.get("num_comments", 0) or 0},
                   links=[f"https://news.ycombinator.com/item?id={h['objectID']}"])
