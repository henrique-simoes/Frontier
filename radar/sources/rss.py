"""RSS/Atom feeds, plus homepage feed autodiscovery and a link-diff fallback for labs without feeds."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import feedparser

from .. import http
from ..models import Item
from ..util import canonical_id, strip_html

FEED_LINK_RE = re.compile(
    r'<link[^>]+rel=["\']alternate["\'][^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>', re.I)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.I | re.S)
POSTY = re.compile(r"/(blog|research|news|posts?|articles?|papers?|publications?|updates?|index)/.+", re.I)
EPOCH = "1970-01-01T00:00:00Z"   # baseline marker: links seen on the first visit are not "new"


def _entry_item(e, entity: dict, source: str) -> Item | None:
    url = e.get("link")
    if not url:
        return None
    content = e.get("summary", "") or ""
    if e.get("content"):
        content = e["content"][0].get("value", content)
    text = strip_html(content)
    links = re.findall(r'href=["\'](https?://[^"\']+)["\']', content)
    cid = canonical_id(url)
    return Item(id=cid, kind="paper" if cid.startswith("arxiv:") else "post", title=strip_html(e.get("title", url)),
                url=url, source=source, summary=text[:1500], published=e.get("published") or e.get("updated"),
                orgs=[entity["name"]] if entity.get("type") == "org" else [], links=links[:40],
                via_entity=entity["id"])


def fetch_feed(entity: dict, url: str) -> list[Item]:
    text = http.get(url, as_json=False, ttl=6 * 3600)
    feed = feedparser.parse(text)
    return [it for e in feed.entries[:25] if (it := _entry_item(e, entity, "rss"))]


def discover_feed(html: str, base: str) -> str | None:
    for tag in FEED_LINK_RE.findall(html):
        if m := HREF_RE.search(tag):
            return urljoin(base, m.group(1))
    return None


def fetch_homepage(entity: dict, url: str) -> list[Item]:
    html = http.get(url, as_json=False, ttl=12 * 3600)
    feed_url = discover_feed(html, url)
    if feed_url:
        entity.setdefault("_new_handles", {})["feed"] = feed_url
        try:
            return fetch_feed(entity, feed_url)
        except Exception:
            pass
    # Fallback: treat new post-like links on the page as new items.
    host = urlparse(url).netloc
    items, seen = [], set()
    first_visit = not entity.get("checks")
    for href, label in ANCHOR_RE.findall(html):
        full = urljoin(url, href)
        p = urlparse(full)
        label = strip_html(label)
        if p.netloc != host or not POSTY.search(p.path) or len(label) < 18 or full in seen:
            continue
        seen.add(full)
        items.append(Item(id=canonical_id(full), kind="post", title=label[:200], url=full, source="homepage",
                          published=EPOCH if first_visit else None,
                          orgs=[entity["name"]] if entity.get("type") == "org" else [], via_entity=entity["id"]))
        if len(items) >= 30:
            break
    return items
