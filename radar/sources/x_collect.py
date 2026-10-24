"""X collection: turns provider posts into radar items, share edges and leads (providers live in x.py)."""
from __future__ import annotations

import math
import random
import re
from datetime import timedelta
from typing import Optional

from ..config import config
from ..models import Author, Item
from ..util import age_days, canonical_id, iso, now, parse_dt
from .x import AI_HINT, Budget, BudgetExceeded, XAi, XApi, provider, xcfg

# ---------------------------------------------------------------- posts -> items

def kind_for(cid: str) -> str:
    return {"arxiv": "paper", "gh": "repo", "hf": "model"}.get(cid.split(":", 1)[0], "post")


def post_items(post: dict, via: Optional[str], watched: bool) -> tuple[list[Item], list[str]]:
    """Linked research -> canonical items (provisional until their own source fills them in).
    Link-less announcements from watched accounts -> an x:<id> post item. Returns (items, arxiv ids to resolve)."""
    items, pending = [], []
    m = post["metrics"]
    xm = {"x_likes": m.get("likes", 0), "x_reposts": m.get("reposts", 0), "x_quotes": m.get("quotes", 0),
          "x_bookmarks": m.get("bookmarks", 0)}
    author = Author(name=post["name"], ids={"x": post["handle"].lower()})
    first_line = re.sub(r"https?://\S+", "", post["text"]).strip().split("\n")[0][:140] or post["url"]
    for link in dict.fromkeys(post["links"]):
        cid = canonical_id(link)
        research = cid.startswith(("arxiv:", "gh:", "hf:"))
        if not research and (not watched or not AI_HINT.search(post["text"])):
            continue   # strangers' generic links (news, video, shops) are noise; watched accounts' AI links are not
        if cid.startswith("arxiv:"):
            pending.append(cid.split(":", 1)[1])
        title = cid.split(":", 1)[1] if cid.startswith(("gh:", "hf:")) else first_line
        items.append(Item(id=cid, kind=kind_for(cid), title=title, url=link, source="x", summary=post["text"][:1500],
                          published=post["created_at"], metrics=xm, links=[post["url"]],
                          raw={"provisional": True, "x_post": post["id"]}, via_entity=via))
    if not items and watched and not post["is_repost"] and len(post["text"]) >= 120 and AI_HINT.search(post["text"]):
        items.append(Item(id=f"x:{post['id']}", kind="post", title=first_line, url=post["url"], source="x",
                          summary=post["text"][:2000], published=post["created_at"], authors=[author],
                          metrics=xm, raw={"x_post": post["id"]}, via_entity=via))
    return items, pending


def share_evidence(post: dict) -> str:
    return f"x:{post['id']}@{post.get('created_at') or iso()}"


def since_for(entity_meta: dict) -> str:
    lookback = now() - timedelta(days=float(config().get("collect.lookback_days", 3)))
    last = parse_dt(entity_meta.get("x_since"))
    return iso(max(last, lookback) if last else lookback)


# ---------------------------------------------------------------- collection

def watched_accounts(store, limit: int) -> list[dict]:
    """Watched entities with an X handle, Thompson-sampled on their yield like every other source."""
    rows = store.q("""SELECT * FROM entities WHERE status IN ('active','candidate') AND handles LIKE '%"x":%'""")
    rows = [r for r in rows if r["handles"].get("x") and not (r["meta"] or {}).get("x_invalid")]
    scored = sorted(rows, key=lambda e: -random.betavariate(max(e["alpha"], .1), max(e["beta"], .1)) * (0.4 + e["prior"]))
    return scored[:limit]


class Collector:
    """One X pass per run: watched accounts, configured lists, discovery queries, mention leads."""

    def __init__(self, store, ingest, expander, budget: Budget) -> None:
        self.store, self.ingest, self.exp, self.b = store, ingest, expander, budget
        self.p = provider()
        self.client = XApi(budget) if self.p == "xapi" else XAi(budget) if self.p == "xai" else None
        self.stats = {"posts": 0, "items": 0, "dropped_uncited": 0, "accounts": 0, "mention_leads": 0}
        self.pending: list[str] = []
        self.polled: list[str] = []     # entity ids polled this run, productive or not (bandit yield)

    def run(self) -> dict:
        if not self.client:
            self.store.event("collect", "x: skipped (no X_BEARER_TOKEN or XAI_API_KEY; see README)")
            return self.stats
        steps = [("accounts", self._accounts), ("lists", self._lists), ("queries", self._queries)]
        for name, fn in steps:
            try:
                fn()
            except BudgetExceeded as exc:
                self.store.event("budget", f"x: stopped at {name}: {exc}")
                break
            except Exception as exc:   # an X outage never stops the run
                self.store.event("error", f"x {name} failed: {exc}"[:300])
        self.stats["dropped_uncited"] = getattr(self.client, "dropped", 0)
        if self.pending:
            queue = set(self.store.get_meta("pending_arxiv") or []) | set(self.pending)
            self.store.set_meta("pending_arxiv", sorted(queue)[-300:])
        self.store.event("collect", f"x ({self.p}): {self.stats['posts']} posts from {self.stats['accounts']} "
                                    f"accounts -> {self.stats['items']} items, ${self.b.usd:.2f} spent"
                                    + (f", {self.stats['dropped_uncited']} uncited posts dropped" if self.stats["dropped_uncited"] else ""))
        return self.stats

    # -- sources
    def _accounts(self) -> None:
        accts = watched_accounts(self.store, int(xcfg("accounts_per_run", 40)))
        self.stats["accounts"] = len(accts)
        self.polled = [a["id"] for a in accts]
        if not accts:
            return
        if self.p == "xapi":
            missing = [a for a in accts if not (a["meta"] or {}).get("x_id")]
            if missing:
                found = {u["username"].lower(): u for u in self.client.lookup([a["handles"]["x"] for a in missing])}
                for a in missing:
                    u = found.get(a["handles"]["x"].lower())
                    meta = {**(a["meta"] or {}), **({"x_id": u["id"], "x_bio": u.get("description", "")} if u else {"x_invalid": True})}
                    self.store.update_entity(a["id"], meta=meta)
                    a["meta"] = meta
                    if not u:
                        self.store.event("x", f"@{a['handles']['x']} not found on X; marked invalid", a["id"])
            for a in accts:
                if (a["meta"] or {}).get("x_id"):
                    for post in self.client.user_posts(a["meta"]["x_id"], since_for(a["meta"] or {}),
                                                       int(xcfg("posts_per_account", 20))):
                        self._process(post, a["id"], watched=True)
                    self.store.update_entity(a["id"], meta={**(a["meta"] or {}), "x_since": iso()})
        else:
            by_handle = {a["handles"]["x"].lower(): a for a in accts}
            since = min(since_for(a["meta"] or {}) for a in accts)
            for post in self.client.accounts(list(by_handle), since):
                a = by_handle.get(post["handle"].lower())
                self._process(post, a["id"] if a else None, watched=True)
            for a in accts:
                self.store.update_entity(a["id"], meta={**(a["meta"] or {}), "x_since": iso()})

    def _lists(self) -> None:
        if self.p != "xapi":
            return
        for list_id in xcfg("lists") or []:
            for post in self.client.list_posts(str(list_id), int(xcfg("posts_per_list", 100))):
                if age_days(post["created_at"]) <= float(config().get("collect.lookback_days", 3)):
                    self._process(post, None, watched=True)

    def _queries(self) -> None:
        since = iso(now() - timedelta(days=float(config().get("collect.lookback_days", 3))))
        min_likes = int(xcfg("min_likes_for_search", 25))
        for q in (xcfg("queries") or {}).get(self.p, []):
            for post in self.client.search(q, since):
                if self.p == "xapi" and post["metrics"].get("likes", 0) < min_likes:
                    continue
                self._process(post, None, watched=False)

    # -- one post
    def _process(self, post: dict, via: Optional[str], watched: bool) -> None:
        self.stats["posts"] += 1
        handle = post["handle"].lower()
        if not via and handle != "i":
            via, _ = self.store.upsert_entity("person", post["name"], {"x": handle}, prior=0.1,
                                              status="known", via="x")
        items, pending = post_items(post, via, watched)
        self.pending += pending
        for it in items:
            iid = self.ingest(it)
            if iid and via:
                self.store.edge(via, iid, "shared", 1 + math.log1p(post["metrics"].get("likes", 0)) / 5,
                                share_evidence(post))
                self.stats["items"] += 1
        # people a watched account tags next to research links are leads worth a look
        cap = int(xcfg("max_mention_leads_per_run", 25))
        if watched and items and via and self.exp is not None:
            parent = self.store.entity(via) or {"depth": 0}
            for m in post["mentions"][:5]:
                if self.stats["mention_leads"] >= cap:
                    break
                if self.exp._add("person", m, {"x": m.lower()}, parent, 40, "mentions", via,
                                 f"tagged by @{handle} alongside {items[0].id}"):
                    self.stats["mention_leads"] += 1
