from __future__ import annotations

from typing import Iterator

from .. import http
from ..config import config
from ..models import Author, Item
from ..util import age_days, iso, now

BASE = "https://huggingface.co"


def sweep_daily_papers() -> Iterator[Item]:
    if not config().get("collect.hf_daily_papers", True):
        return
    data = http.get(f"{BASE}/api/daily_papers", params={"limit": 100}, ttl=2 * 3600)
    for d in data:
        p = d.get("paper", {})
        pid = p.get("id")
        if not pid:
            continue
        links = [x for x in (p.get("githubRepo"), p.get("projectPage")) if x]
        org = (p.get("organization") or {}).get("name") if isinstance(p.get("organization"), dict) else None
        yield Item(
            id=f"arxiv:{pid}", kind="paper", title=p.get("title", "").strip(), url=f"https://arxiv.org/abs/{pid}",
            source="hf_papers", summary=p.get("summary", "").strip(), published=p.get("publishedAt"),
            authors=[Author(name=a.get("name", "")) for a in p.get("authors", []) if a.get("name")],
            orgs=[org] if org else [],
            concepts=[k.lower() for k in p.get("ai_keywords") or []][:10],
            metrics={"upvotes": p.get("upvotes", 0) or 0, "stars": p.get("githubStars", 0) or 0,
                     "comments": d.get("numComments", 0) or 0},
            links=links, raw={"ai_summary": p.get("ai_summary", "")})


def _model_item(m: dict, source: str, via: str | None = None) -> Item:
    mid = m.get("id") or m.get("modelId")
    author = m.get("author") or mid.split("/")[0]
    tags = m.get("tags", [])
    days = max(age_days(m.get("createdAt")), 0.5)
    return Item(
        id=f"hf:{mid}".lower(), kind="model", title=mid, url=f"{BASE}/{mid}", source=source,
        summary=f"New model `{mid}` by {author}. Pipeline: {m.get('pipeline_tag') or 'n/a'}. "
                f"Library: {m.get('library_name') or 'n/a'}. Tags: {', '.join(t for t in tags if ':' not in t)[:300]}",
        published=m.get("createdAt"), orgs=[author],
        concepts=[t.lower() for t in tags if ":" not in t][:8] + ([m["pipeline_tag"]] if m.get("pipeline_tag") else []),
        metrics={"likes": m.get("likes", 0) or 0, "downloads": m.get("downloads", 0) or 0,
                 "likes_per_day": (m.get("likes", 0) or 0) / days, "trending": m.get("trendingScore", 0) or 0},
        raw={"author": author}, via_entity=via)


def sweep_trending_models() -> Iterator[Item]:
    n = config().get("collect.hf_trending_models", 100)
    if not n:
        return
    for m in http.get(f"{BASE}/api/models", params={"sort": "trendingScore", "limit": n}, ttl=3 * 3600):
        if age_days(m.get("createdAt")) <= 45:   # trending *and* new
            yield _model_item(m, "hf_models")


def fetch_org(entity: dict, org: str) -> list[Item]:
    data = http.get(f"{BASE}/api/models", params={"author": org, "sort": "createdAt", "direction": -1, "limit": 10},
                    ttl=12 * 3600)
    return [_model_item(m, "hf_org", via=entity["id"]) for m in data if age_days(m.get("createdAt")) <= 30]
