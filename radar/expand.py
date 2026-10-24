"""The expansion engine: a focused crawler over people, orgs, repos and feeds.

- discovery: high-scoring items spawn candidate entities (authors, orgs, repo owners, referenced
  and citing papers, social follows), each with prior = parent_novelty * edge_weight * decay^depth
- scheduling: Thompson sampling on each entity's Beta(alpha, beta) yield posterior
- bounded: per-run budgets and caps keep cost linear while reach compounds
"""
from __future__ import annotations

import random
import re
from typing import Optional

from .config import config
from .models import Item
from . import http
from .sources import ENTITY_FETCHERS, bluesky, github, s2
from .sources import x as xsrc
from .store import Store, entity_id
from .util import GITHUB_RE, age_days, canonical_id, iso

REL_WEIGHT = {"shared": 0.7, "authored": 1.0, "released": 0.9, "affiliated": 0.7, "owns": 0.9, "cites": 0.5, "cited_by": 0.8,
              "follows": 0.45, "mentions": 0.4, "suggested": 0.6}


class Expander:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.cfg = config()
        self.new_this_run = 0
        self.decay = self.cfg.get("expand.depth_decay", 0.7)
        self.x_budget = None          # set by the pipeline when X is enabled
        self.x_follow_done = 0

    # ------------------------------------------------------------ seeds
    def seed(self) -> int:
        n = 0
        seeds = self.cfg.seeds or {}
        for type_, key in (("org", "orgs"), ("person", "people")):
            for s in seeds.get(key) or []:
                handles = {k: v for k, v in s.items() if k != "name" and v}
                _, is_new = self.store.upsert_entity(type_, s["name"], handles, prior=0.6, depth=0,
                                                     via="seed", status="active")
                n += is_new
        self.store.commit()
        return n

    # ------------------------------------------------------------ scheduling
    def select(self, budget: Optional[int] = None) -> list[dict]:
        budget = budget or self.cfg.get("expand.budget_per_run", 60)
        rows = self.store.q("SELECT * FROM entities WHERE status IN ('active','candidate')")
        scored = []
        for e in rows:
            if not any(k in ENTITY_FETCHERS for k in e["handles"]):
                continue
            theta = random.betavariate(max(e["alpha"], 0.1), max(e["beta"], 0.1))
            hours = (age_days(e["last_checked"]) * 24) if e["last_checked"] else 999
            staleness = min(1.0, hours / 20)
            if staleness < 0.25:
                continue
            score = theta * (0.4 + e["prior"]) * staleness * (1 + 2 * (e["centrality"] or 0))
            scored.append((score, e))
        scored.sort(key=lambda t: -t[0])
        return [e for _, e in scored[:budget]]

    def check(self, entity: dict) -> list[Item]:
        items: list[Item] = []
        for kind, value in entity["handles"].items():
            fetch = ENTITY_FETCHERS.get(kind)
            if not fetch:
                continue
            if kind == "homepage" and "feed" in entity["handles"]:
                continue   # the feed supersedes the homepage
            try:
                items.extend(fetch(entity, value))
            except Exception as exc:  # one dead source never stops the run
                self.store.event("error", f"{kind} fetch failed for {entity['name']}: {exc}"[:300], entity["id"])
        if entity.get("_new_handles"):
            handles = {**entity["handles"], **entity["_new_handles"]}
            self.store.update_entity(entity["id"], handles=handles)
            self.store.event("discover", f"found feed for {entity['name']}", entity["id"])
        self.store.update_entity(entity["id"], last_checked=iso())
        return items

    def record_yield(self, eid: str, n_items: int, n_hits: int, empty_penalty: float = 0.5) -> None:
        e = self.store.entity(eid)
        if not e:
            return
        alpha = e["alpha"] + n_hits
        beta = e["beta"] + 0.5 * max(n_items - n_hits, 0) + (empty_penalty if n_items == 0 else 0)
        status = e["status"]
        if status == "candidate" and n_hits > 0:
            status = "active"
            self.store.event("promote", f"{e['name']} produced a frontier hit; now active", eid)
        checks = e["checks"] + 1
        if checks >= 4 and alpha / (alpha + beta) < 0.06 and e["discovered_via"] != "seed":
            status = "dormant"
            self.store.event("demote", f"{e['name']} went dormant (low yield after {checks} checks)", eid)
        self.store.update_entity(eid, alpha=alpha, beta=beta, checks=checks, hits=e["hits"] + n_hits, status=status)

    # ------------------------------------------------------------ discovery
    def _room(self) -> bool:
        if self.new_this_run >= self.cfg.get("expand.max_new_entities_per_run", 150):
            return False
        active = self.store.db.execute("SELECT COUNT(*) FROM entities WHERE status IN ('active','candidate')").fetchone()[0]
        return active < self.cfg.get("expand.max_active_entities", 5000)

    def _add(self, type_: str, name: str, handles: dict, parent: dict, novelty: float, rel: str,
             target: str, evidence: str) -> Optional[str]:
        if not name or len(name) < 2:
            return None
        depth = int(parent.get("depth", 0)) + 1
        prior = (novelty / 100) * REL_WEIGHT.get(rel, 0.4) * (self.decay ** max(depth - 1, 0))
        existing = next((e for k, v in handles.items() if v and (e := self.store.resolve_handle(k, v))), None)
        existing = existing or (self.store.entity(entity_id(type_, name)) or {}).get("id")
        before = self.store.entity(existing) if existing else None
        if (not before or before["status"] == "known") and not self._room():
            return None
        eid, is_new = self.store.upsert_entity(type_, name, handles, prior=prior, depth=depth, via=target)
        if is_new or (before and before["status"] == "known"):
            self.new_this_run += 1
            if before:   # was only known as a co-author/owner; now worth watching
                self.store.update_entity(eid, discovered_via=target, depth=depth)
            self.store.event("discover", f"now watching {type_} '{name}' ({evidence})", eid)
        self.store.edge(eid, target, rel, REL_WEIGHT.get(rel, 0.4), evidence)
        return eid

    def link_item(self, item: dict) -> None:
        """Always-on structural edges (cheap): authors/orgs -> item, via-entity -> item."""
        for a in (item.get("authors") or [])[:15]:
            if a.get("name"):
                eid, _ = self.store.upsert_entity("person", a["name"], a.get("ids") or {}, prior=0.1, status="known")
                self.store.edge(eid, item["id"], "authored")
        for o in item.get("orgs") or []:
            handles = {}
            if item["id"].startswith("hf:"):
                handles["hf"] = o
            if item["id"].startswith("gh:"):
                handles["github"] = o
            eid, _ = self.store.upsert_entity("org", o, handles, prior=0.1, status="known")
            self.store.edge(eid, item["id"], "released")
        if item.get("via_entity"):
            self.store.edge(item["via_entity"], item["id"], "released" if item["kind"] in ("model", "repo") else "mentions", 0.8)

    def discover_from(self, item: dict) -> None:
        """Called for items above discover_threshold: turn their neighbourhood into watchable entities."""
        parent = {"depth": 0}
        if item.get("via_entity"):
            parent = self.store.entity(item["via_entity"]) or parent
        ctx = (item, parent, item.get("novelty") or 0)
        self._discover_people(*ctx)
        self._discover_links(*ctx)
        self._discover_sharers(*ctx)
        self._discover_suggested(*ctx)

    def _discover_people(self, item: dict, parent: dict, nov: float) -> None:
        iid = item["id"]
        for a in (item.get("authors") or [])[:10]:
            handles = dict(a.get("ids") or {}) or {"arxiv_author": a["name"]}
            self._add("person", a["name"], handles, parent, nov, "authored", iid, f"author of {item['title'][:60]}")
        prefix = {"hf:": "hf", "gh:": "github"}.get(iid[:3])
        for o in (item.get("orgs") or []) if prefix else []:
            self._add("org", o, {prefix: o}, parent, nov, "released", iid, f"released {item['title'][:60]}")

    def _discover_links(self, item: dict, parent: dict, nov: float) -> None:
        iid = item["id"]
        for url in item.get("links") or []:
            m = GITHUB_RE.search(url)
            if m and m.group(1).lower() not in {"orgs", "topics", "features", "sponsors", "user-attachments"}:
                self._add("org", m.group(1), {"github": m.group(1)}, parent, nov, "owns", iid,
                          f"owns repo linked from {item['title'][:50]}")
            cid = canonical_id(url)
            if cid.startswith("arxiv:") and cid != iid:
                pending = set(self.store.get_meta("pending_arxiv") or [])
                pending.add(cid.split(":", 1)[1])
                self.store.set_meta("pending_arxiv", sorted(pending)[-300:])
                self.store.edge(iid, cid, "mentions", 0.5)

    def _discover_sharers(self, item: dict, parent: dict, nov: float) -> None:
        """People who shared a frontier item on X early are worth watching themselves."""
        for s in self.store.q("""SELECT e.* FROM edges x JOIN entities e ON e.id = x.src
                                 WHERE x.dst = ? AND x.rel = 'shared' AND e.status = 'known' LIMIT 10""", (item["id"],)):
            self._add(s["type"], s["name"], s["handles"], parent, nov, "shared", item["id"],
                      f"shared {item['title'][:50]} on X")

    def _discover_suggested(self, item: dict, parent: dict, nov: float) -> None:
        for f in (item.get("qual") or {}).get("follow") or []:
            name, typ = f.get("name", ""), f.get("type", "person")
            handles = {}
            if typ == "repo" and re.match(r"^[\w.-]+/[\w.-]+$", name):
                handles = {"github": name.split("/")[0]}
                typ = "org"
            elif typ == "person":
                handles = {"arxiv_author": name}
            eid = self._add("person" if typ == "person" else "org", name, handles, parent, nov, "suggested",
                            item["id"], f"reviewer: {f.get('why', '')[:80]}")
            if eid and not handles:
                self.store.update_entity(eid, meta={"needs_resolution": True, "why": f.get("why", "")})

    def snowball(self, item: dict) -> list[Item]:
        """Backward (references) and forward (citations) snowballing via Semantic Scholar."""
        if not item["id"].startswith("arxiv:"):
            return []
        sid = "arXiv:" + item["id"].split(":", 1)[1]
        found: list[Item] = []
        rec = s2.paper(sid)
        if rec:
            for a in rec.get("authors") or []:
                if a.get("authorId"):
                    self.store.upsert_entity("person", a["name"], {"s2": a["authorId"]}, prior=0.1, status="known")
                for aff in a.get("affiliations") or []:
                    eid, _ = self.store.upsert_entity("org", aff, {}, prior=0.1, status="known")
                    self.store.edge(eid, item["id"], "affiliated", 0.7, f"affiliation of {a.get('name')}")
        try:
            if self.cfg.get("expand.follow_references", True):
                for ref in s2.references(sid, limit=30):
                    found.append(ref)
                    self.store.edge(item["id"], ref.id, "cites", 0.5)
            if self.cfg.get("expand.follow_citations", True):
                for cit in s2.citations(sid, limit=30):
                    cit.via_entity = None
                    found.append(cit)
                    self.store.edge(cit.id, item["id"], "cites", 0.8)
        except Exception as exc:
            if "404" not in str(exc):   # 404 = too new for Semantic Scholar yet; retried on a later run
                self.store.event("error", f"snowball failed for {item['id']}: {exc}"[:300], item["id"])
        if found:
            self.store.event("snowball", f"{len(found)} references/citations around '{item['title'][:70]}'", item["id"])
        return found

    def cofollow(self, follower: dict, followed: list[dict]) -> int:
        """X follow graph with co-follow accumulation: each productive account that follows X raises X's prior
        by 1 - (1 - prior) * (1 - w), w = follower yield * follows-weight. Many strong followers -> high prior."""
        y = follower["alpha"] / max(follower["alpha"] + follower["beta"], 1e-9)
        w = y * REL_WEIGHT["follows"]
        added = 0
        for u in followed:
            handle = (u.get("username") or "").lower()
            if not handle:
                continue
            eid = self.store.resolve_handle("x", handle)
            before = self.store.entity(eid) if eid else None
            if not before:
                if not self._room():
                    break
                eid, _ = self.store.upsert_entity("person", u.get("name") or handle, {"x": handle}, prior=0.0,
                                                  depth=int(follower.get("depth", 0)) + 1, via=follower["id"],
                                                  meta={"x_id": u.get("id"), "x_bio": u.get("description", "")})
                self.new_this_run += 1
                added += 1
                self.store.event("discover", f"now watching @{handle} (followed by {follower['name']} on X)", eid)
                before = self.store.entity(eid)
            elif before["status"] == "known":
                self.store.update_entity(eid, status="candidate")
            self.store.edge(follower["id"], eid, "follows", REL_WEIGHT["follows"], f"{follower['name']} follows on X")
            self.store.update_entity(eid, prior=round(1 - (1 - (before["prior"] or 0)) * (1 - w), 4))
        return added

    def _guarded(self, entity: dict, label: str, fn) -> None:
        """One network failing (or hitting the X budget) never blocks the others."""
        try:
            fn()
        except xsrc.BudgetExceeded as exc:
            self.store.event("budget", f"{label} skipped for {entity['name']}: {exc}", entity["id"])
        except Exception as exc:
            self.store.event("error", f"{label} failed for {entity['name']}: {exc}"[:300], entity["id"])

    def _x_from_github(self, entity: dict) -> None:
        gh = entity["handles"]["github"]
        prof = http.get(f"https://api.github.com/users/{gh}", ttl=30 * 86400)
        if tw := (prof or {}).get("twitter_username"):
            self.store.upsert_entity(entity["type"], entity["name"], {"github": gh, "x": tw.lower()})
            self.store.event("discover", f"{entity['name']} is @{tw} on X (from GitHub profile)", entity["id"])

    def _x_follows(self, entity: dict, x_id: str) -> None:
        self.x_follow_done += 1
        followed = xsrc.XApi(self.x_budget).following(x_id, int(self.cfg.get("x.following_limit", 200)))
        n = self.cofollow(entity, followed)
        self.store.event("expand", f"read who @{entity['handles']['x']} follows on X: {len(followed)} accounts, "
                                   f"{n} new", entity["id"])

    def social(self, entity: dict) -> None:
        """Follow who productive entities follow (GitHub, Bluesky, X) and pick up X handles from GitHub."""
        if not self.cfg.get("expand.follow_social", True):
            return
        meta = entity.get("meta") or {}
        if meta.get("social_at") and age_days(meta["social_at"]) < 14:
            return
        nov = 100 * entity["alpha"] / (entity["alpha"] + entity["beta"])
        h = entity["handles"]
        if h.get("github"):
            self._guarded(entity, "GitHub follows", lambda: [
                self._add("person", login, {"github": login}, entity, nov, "follows", entity["id"],
                          f"followed by {entity['name']} on GitHub") for login in github.following(h["github"], limit=40)])
        if h.get("github") and not h.get("x"):
            self._guarded(entity, "GitHub profile", lambda: self._x_from_github(entity))
        if (h.get("x") and meta.get("x_id") and self.x_budget is not None and xsrc.provider() == "xapi"
                and self.x_follow_done < int(self.cfg.get("x.follow_accounts_per_run", 2))):
            self._guarded(entity, "X follows", lambda: self._x_follows(entity, meta["x_id"]))
        if h.get("bluesky"):
            self._guarded(entity, "Bluesky follows", lambda: [
                self._add("person", f["name"], {"bluesky": f["handle"]}, entity, nov, "follows", entity["id"],
                          f"followed by {entity['name']} on Bluesky") for f in bluesky.follows(h["bluesky"], limit=80)])
        self.store.update_entity(entity["id"], meta={**meta, "social_at": iso()})
