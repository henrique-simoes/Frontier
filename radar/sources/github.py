from __future__ import annotations

from datetime import timedelta
from typing import Iterator

from .. import http
from ..config import config
from ..models import Item
from ..util import age_days, now

API = "https://api.github.com"


def _repo_item(r: dict, source: str, via: str | None = None) -> Item:
    days = max(age_days(r.get("created_at")), 0.5)
    stars = r.get("stargazers_count", 0) or 0
    return Item(
        id=f"gh:{r['full_name']}".lower(), kind="repo", title=r["full_name"], url=r["html_url"], source=source,
        summary=(r.get("description") or "").strip(), published=r.get("created_at"),
        orgs=[r["owner"]["login"]], concepts=[t.lower() for t in r.get("topics", [])][:10],
        metrics={"stars": stars, "stars_per_day": stars / days, "forks": r.get("forks_count", 0) or 0},
        links=[r["homepage"]] if r.get("homepage") else [],
        raw={"owner": r["owner"]["login"], "owner_type": r["owner"].get("type"), "language": r.get("language")},
        via_entity=via)


def sweep() -> Iterator[Item]:
    cfg = config()
    since = (now() - timedelta(days=max(cfg.get("collect.lookback_days", 3) * 3, 7))).strftime("%Y-%m-%d")
    min_stars = cfg.get("collect.github_min_stars", 40)
    for q in cfg.get("collect.github_queries", []):
        data = http.get(f"{API}/search/repositories",
                        params={"q": f"{q} created:>{since} stars:>{min_stars}", "sort": "stars", "order": "desc",
                                "per_page": 30}, ttl=6 * 3600)
        for r in data.get("items", []):
            yield _repo_item(r, "github")


def fetch_owner(entity: dict, owner: str) -> list[Item]:
    kind = "orgs" if entity.get("type") == "org" else "users"
    try:
        data = http.get(f"{API}/{kind}/{owner}/repos", params={"sort": "created", "direction": "desc", "per_page": 10},
                        ttl=12 * 3600)
    except Exception:
        data = http.get(f"{API}/users/{owner}/repos", params={"sort": "created", "per_page": 10}, ttl=12 * 3600)
    return [_repo_item(r, "github_owner", via=entity["id"]) for r in data
            if not r.get("fork") and age_days(r.get("created_at")) <= 60]


def following(owner: str, limit: int = 50) -> list[str]:
    """Who this GitHub user follows: the literal 'who do they read' signal."""
    data = http.get(f"{API}/users/{owner}/following", params={"per_page": limit}, ttl=7 * 86400)
    return [u["login"] for u in data]
