"""Bluesky public AppView: researchers' posts (link-sharing) and their follow graph. No auth needed."""
from __future__ import annotations

from .. import http
from ..models import Item
from ..util import canonical_id

API = "https://public.api.bsky.app/xrpc"


def fetch_author_feed(entity: dict, handle: str) -> list[Item]:
    data = http.get(f"{API}/app.bsky.feed.getAuthorFeed", params={"actor": handle, "limit": 40,
                                                                    "filter": "posts_no_replies"}, ttl=6 * 3600)
    items = []
    for f in data.get("feed", []):
        post = f.get("post", {})
        rec = post.get("record", {})
        embed = (post.get("embed") or {}).get("external") or {}
        url = embed.get("uri")
        if not url:
            for facet in rec.get("facets", []) or []:
                for feat in facet.get("features", []):
                    if feat.get("uri"):
                        url = feat["uri"]
                        break
        if not url or "bsky.app" in url:
            continue
        cid = canonical_id(url)
        items.append(Item(
            id=cid, kind="paper" if cid.startswith("arxiv:") else "repo" if cid.startswith("gh:") else "post",
            title=embed.get("title") or rec.get("text", "")[:140], url=url, source="bluesky",
            summary=(embed.get("description") or "") + f"\n\nShared by @{handle}: {rec.get('text', '')}",
            published=rec.get("createdAt"),
            metrics={"bsky_likes": post.get("likeCount", 0) or 0, "bsky_reposts": post.get("repostCount", 0) or 0},
            via_entity=entity["id"]))
    return items


def follows(handle: str, limit: int = 100) -> list[dict]:
    data = http.get(f"{API}/app.bsky.graph.getFollows", params={"actor": handle, "limit": limit}, ttl=7 * 86400)
    return [{"handle": f["handle"], "name": f.get("displayName") or f["handle"], "bio": f.get("description", "")}
            for f in data.get("follows", [])]
