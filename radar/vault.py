"""Obsidian vault writer. Notes are regenerated only between radar:auto markers; your text survives.
Ratings typed into a note's `rating:` property flow back into the scorer."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import yaml

from .calibrate import LABEL_VALUE
from .config import config
from .novelty import FEATURES
from .store import Store
from .util import iso, note_title, now

AUTO_START, AUTO_END = "<!-- radar:auto:start -->", "<!-- radar:auto:end -->"
USER_KEYS = ("rating", "tags", "aliases", "pinned")   # frontmatter keys owned by you, never overwritten
FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.S)
FOLDERS = {"person": "Entities/People", "org": "Entities/Orgs"}
SIGNAL_LABEL = {"semantic": "far from prior work", "combinatorial": "unusual combination",
                "emergence": "early in a new wave", "periphery": "outside the usual labs",
                "traction": "fast uptake", "recency": "fresh", "attention": "shared early by strong researchers", "llm_novelty": "reviewer: novel",
                "llm_rigor": "reviewer: rigorous", "llm_impact": "reviewer: high impact",
                "tournament": "won the daily tournament", "class_prior": "novelty class"}


def obsidian_uri(rel_path: str) -> str:
    return f"obsidian://open?vault={quote(config().vault_name)}&file={quote(rel_path.removesuffix('.md'))}"


class Vault:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.root: Path = config().path("vault")
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ io helpers
    def _read(self, path: Path) -> tuple[dict, str]:
        if not path.exists():
            return {}, ""
        text = path.read_text(encoding="utf-8")
        m = FM_RE.match(text)
        if not m:
            return {}, text
        try:
            return yaml.safe_load(m.group(1)) or {}, text[m.end():]
        except yaml.YAMLError:
            return {}, text[m.end():]

    def _write(self, rel: str, fm: dict, auto_body: str, default_user: str = "\n## My notes\n\n") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        old_fm, body = self._read(path)
        for k in USER_KEYS:
            if k in old_fm and old_fm[k] not in (None, ""):
                fm[k] = old_fm[k]
        block = f"{AUTO_START}\n{auto_body.strip()}\n{AUTO_END}"
        if AUTO_START in body and AUTO_END in body:
            body = re.sub(re.escape(AUTO_START) + ".*?" + re.escape(AUTO_END), lambda _: block, body, flags=re.S)
        else:
            body = f"{block}\n{default_user}"
        fm_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=1000).strip()
        path.write_text(f"---\n{fm_text}\n---\n{body}", encoding="utf-8")

    # ------------------------------------------------------------ naming
    def item_rel(self, item: dict) -> str:
        if item.get("note_path"):
            return item["note_path"]
        month = (item.get("published") or iso())[:7]
        if month < "2000":
            month = iso()[:7]
        base = note_title(item["title"], 110)
        rel = f"Items/{month}/{base}.md"
        clash = self.store.one("SELECT id FROM items WHERE note_path=? AND id!=?", (rel, item["id"]))
        if clash or (self.root / rel).exists() and not self._owned_by(rel, item["id"]):
            rel = f"Items/{month}/{base} ({item['id'].split(':')[-1][-8:]}).md"
        return rel

    def _owned_by(self, rel: str, item_id: str) -> bool:
        fm, _ = self._read(self.root / rel)
        return fm.get("id") == item_id

    def entity_rel(self, ent: dict) -> str:
        if ent.get("note_path"):
            return ent["note_path"]
        return f"{FOLDERS.get(ent['type'], 'Entities/Other')}/{note_title(ent['name'], 90)}.md"

    def link(self, rel: str, label: Optional[str] = None) -> str:
        target = rel.removesuffix(".md")
        name = target.rsplit("/", 1)[-1]
        return f"[[{target}|{label or name}]]"

    # ------------------------------------------------------------ ratings in
    def sync_ratings(self) -> int:
        n = 0
        for path in (self.root / "Items").rglob("*.md") if (self.root / "Items").exists() else []:
            fm, _ = self._read(path)
            rating = str(fm.get("rating") or "").strip().lower()
            if rating in LABEL_VALUE and fm.get("id"):
                cur = self.store.one("SELECT label FROM feedback WHERE item_id=? AND source='user'", (fm["id"],))
                if not cur or cur["label"] != rating:
                    self.store.feedback(fm["id"], rating, LABEL_VALUE[rating], "user")
                    n += 1
        if n:
            self.store.event("feedback", f"picked up {n} rating(s) from Obsidian")
            self.store.commit()
        return n

    # ------------------------------------------------------------ items
    def write_item(self, item: dict) -> str:
        rel = self.item_rel(item)
        qual = item.get("qual") or {}
        quant = item.get("quant") or {}
        explain = item.get("explain") or {}
        people, orgs = [], []
        for e in self.store.q("""SELECT e.* FROM edges x JOIN entities e ON e.id = x.src
                                  WHERE x.dst=? AND x.rel IN ('authored','released','affiliated','owns')""", (item["id"],)):
            (people if e["type"] == "person" else orgs).append(self.link(self.entity_rel(e), e["name"]))
        fm = {
            "id": item["id"], "kind": item["kind"], "title": item["title"], "url": item["url"],
            "published": (item.get("published") or "")[:10] or None, "discovered": (item.get("discovered") or "")[:10],
            "frontier": item.get("novelty"), "novelty_class": qual.get("novelty_class"),
            "modalities": qual.get("modalities") or item.get("modalities") or [],
            "areas": qual.get("areas") or item.get("areas") or [], "layer": qual.get("layer") or item.get("layer"),
            "sources": item.get("sources") or [item.get("source")], "people": people[:12], "orgs": orgs[:8],
            "signals": {k: round(float(quant.get(k, 0)), 2) for k in FEATURES if k in quant},
            "judged": bool(qual), "rating": None, "tags": ["radar/item", f"radar/{item['kind']}"],
        }
        drivers = sorted(((v, k) for k, v in explain.items()), reverse=True)[:3]
        lines = [f"# {item['title']}", "",
                 f"> **Frontier {item.get('novelty', 0):.0f}** · {qual.get('novelty_class', 'unjudged').replace('_', ' ')}"
                 f" · {item['kind']} · [source]({item['url']})",
                 f"> Driven by: {', '.join(SIGNAL_LABEL.get(k, k) for _, k in drivers)}", ""]
        if qual.get("what_is_new"):
            lines += ["## What's new", qual["what_is_new"], ""]
        if qual.get("claims"):
            lines += ["## Claims"] + [f"- {c}" for c in qual["claims"]] + [""]
        if qual.get("skeptic"):
            lines += ["## Skeptic's take", qual["skeptic"], ""]
        lines += ["## Summary", (item.get("summary") or "_no abstract_")[:2500], ""]
        if people or orgs:
            lines += ["## Who", " · ".join(orgs + people[:12]), ""]
        rel_items = []
        for e in self.store.q("""SELECT x.src, x.dst, x.rel, i.title, i.note_path, i.novelty FROM edges x
                                  JOIN items i ON i.id = CASE WHEN x.src=? THEN x.dst ELSE x.src END
                                  WHERE (x.src=? OR x.dst=?) AND x.rel IN ('cites','mentions','builds_on')
                                  ORDER BY i.novelty DESC LIMIT 15""", (item["id"],) * 3):
            target = self.link(e["note_path"], e["title"][:80]) if e["note_path"] else e["title"][:80]
            direction = "cites" if e["src"] == item["id"] else "cited by"
            rel_items.append(f"- {direction}: {target}")
        if rel_items:
            lines += ["## Connections"] + rel_items + [""]
        if qual.get("closest_prior"):
            lines += ["## Closest prior work"]
            for pid in qual["closest_prior"][:5]:
                p = self.store.item(pid)
                lines.append(f"- {self.link(p['note_path'], p['title'][:80]) if p and p.get('note_path') else pid}")
            lines.append("")
        lines += ["---", "Rate it: set `rating:` to frontier | solid | incremental | hype | irrelevant"]
        self._write(rel, fm, "\n".join(lines))
        if item.get("note_path") != rel:
            self.store.update_item(item["id"], note_path=rel)
        return rel

    # ------------------------------------------------------------ entities
    def write_entity(self, ent: dict) -> str:
        rel = self.entity_rel(ent)
        items = self.store.q("""SELECT i.id, i.title, i.note_path, i.novelty, i.published, x.rel FROM edges x
                                JOIN items i ON i.id = x.dst WHERE x.src=? ORDER BY i.published DESC LIMIT 60""",
                             (ent["id"],))
        disc = self.store.q("""SELECT e.name, e.type, e.note_path, e.id, x.rel, x.evidence FROM edges x
                               JOIN entities e ON e.id = x.src WHERE x.dst=? AND x.rel IN ('follows','suggested')
                               LIMIT 30""", (ent["id"],))
        nov = [i["novelty"] for i in items if i["novelty"] is not None]
        fm = {"id": ent["id"], "type": ent["type"], "name": ent["name"], "status": ent["status"],
              "handles": ent["handles"], "depth": ent["depth"],
              "yield": round(ent["alpha"] / (ent["alpha"] + ent["beta"]), 3), "hits": ent["hits"],
              "centrality": ent["centrality"], "best_frontier": max(nov) if nov else None,
              "discovered_via": ent.get("discovered_via"), "tags": [f"radar/{ent['type']}"]}
        lines = [f"# {ent['name']}", "",
                 f"> {ent['type']} · status **{ent['status']}** · depth {ent['depth']} · "
                 f"yield {fm['yield']:.0%} · {len(items)} linked items", ""]
        if ent["handles"]:
            lines += ["## Watching via"] + [f"- {k}: `{v}`" for k, v in ent["handles"].items()] + [""]
        if items:
            lines += ["## Items"]
            for i in items:
                label = i["title"][:90]
                link = self.link(i["note_path"], label) if i["note_path"] else label
                lines.append(f"- {(i['published'] or '')[:10]} · **{(i['novelty'] or 0):.0f}** · {link} ({i['rel']})")
            lines.append("")
        if disc:
            lines += ["## Led the radar to"]
            for d in disc:
                lines.append(f"- {d['name']} ({d['type']}): {d['evidence']}")
        self._write(rel, fm, "\n".join(lines))
        if ent.get("note_path") != rel:
            self.store.update_entity(ent["id"], note_path=rel)
        return rel

    # ------------------------------------------------------------ brief, topics, canvas
    def write_daily(self, stats: dict, top: list[dict], emerging: list[tuple[str, float]], discovered: list[dict]) -> str:
        day = now().strftime("%Y-%m-%d")
        rel = f"Daily/{day}.md"
        lines = [f"# Radar brief · {day}", "",
                 f"Scanned **{stats.get('seen', 0)}** items ({stats.get('new', 0)} new), judged {stats.get('judged', 0)}, "
                 f"checked {stats.get('entities_checked', 0)} sources, began watching {stats.get('new_entities', 0)} new "
                 f"entities. Graph: {stats.get('graph_entities', 0)} entities · {stats.get('graph_edges', 0)} edges.", "",
                 "## Frontier today"]
        for i, it in enumerate(top[:15], 1):
            q = it.get("qual") or {}
            lines.append(f"{i}. **{it['novelty']:.0f}** {self.link(it['note_path'], it['title'][:100])} "
                         f"· _{(q.get('novelty_class') or it['kind']).replace('_', ' ')}_")
            if q.get("what_is_new"):
                lines.append(f"    - {q['what_is_new']}")
        if emerging:
            lines += ["", "## Emerging concepts"]
            lines += [f"- [[Topics/{note_title(c)}|{c}]] ({s:.2f})" for c, s in emerging[:10]]
        if discovered:
            lines += ["", "## Now following"]
            for e in discovered[:20]:
                lines.append(f"- {self.link(self.entity_rel(e), e['name'])}: {e.get('evidence', '')}")
        lines += ["", "## What the radar did"]
        for ev in self.store.q("SELECT * FROM events WHERE run_id=? AND kind != 'error' ORDER BY id LIMIT 40",
                               (self.store.run_id,)):
            lines.append(f"- `{ev['kind']}` {ev['message']}")
        errs = self.store.one("SELECT COUNT(*) AS n FROM events WHERE run_id=? AND kind='error'", (self.store.run_id,))
        if errs and errs["n"]:
            lines.append(f"- `error` {errs['n']} source errors (see the Activity view)")
        self._write(rel, {"date": day, "tags": ["radar/daily"], **{k: v for k, v in stats.items()
                                                                    if isinstance(v, (int, float))}}, "\n".join(lines))
        return rel

    def write_topic(self, concept: str, score: float) -> str:
        rel = f"Topics/{note_title(concept)}.md"
        items = self.store.q("""SELECT i.title, i.note_path, i.novelty, i.published FROM concept_items c
                                JOIN items i ON i.id=c.item_id WHERE c.term=? ORDER BY i.published DESC LIMIT 40""",
                             (concept,))
        lines = [f"# {concept}", "", f"> Emergence {score:.2f} · {len(items)} items", ""]
        for i in items:
            link = self.link(i["note_path"], i["title"][:90]) if i["note_path"] else i["title"][:90]
            lines.append(f"- {(i['published'] or '')[:10]} · **{(i['novelty'] or 0):.0f}** · {link}")
        self._write(rel, {"concept": concept, "emergence": round(score, 3), "tags": ["radar/topic"]}, "\n".join(lines))
        return rel

    def write_canvas(self, top: list[dict]) -> str:
        """A JSON Canvas map of today's top items and the people/orgs behind them."""
        day = now().strftime("%Y-%m-%d")
        nodes, edges, placed = [], [], {}
        n = min(len(top), 12)
        for k, it in enumerate(top[:n]):
            ang = 2 * math.pi * k / max(n, 1)
            nid = f"i{k}"
            nodes.append({"id": nid, "type": "file", "file": it["note_path"], "x": int(900 * math.cos(ang)),
                          "y": int(650 * math.sin(ang)), "width": 380, "height": 220,
                          "color": "1" if it["novelty"] >= 80 else "3" if it["novelty"] >= 65 else "5"})
            ents = self.store.q("""SELECT e.* FROM edges x JOIN entities e ON e.id=x.src WHERE x.dst=?
                                   AND e.note_path IS NOT NULL ORDER BY e.centrality DESC LIMIT 3""", (it["id"],))
            for j, e in enumerate(ents):
                if e["id"] not in placed:
                    placed[e["id"]] = f"e{len(placed)}"
                    nodes.append({"id": placed[e["id"]], "type": "file", "file": e["note_path"],
                                  "x": int(1500 * math.cos(ang + (j - 1) * 0.12)),
                                  "y": int(1100 * math.sin(ang + (j - 1) * 0.12)), "width": 260, "height": 120,
                                  "color": "6"})
                edges.append({"id": f"x{len(edges)}", "fromNode": placed[e["id"]], "toNode": nid})
        rel = f"Maps/{day}.canvas"
        (self.root / "Maps").mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=1), encoding="utf-8")
        return rel
