"""The daily run: collect -> resolve -> embed -> score -> judge -> rank -> expand -> write -> learn."""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections import defaultdict
from typing import Callable, Optional

import numpy as np

from . import llm
from .calibrate import centrality, fit, retro_labels
from .concepts import extract
from .config import config
from .embed import embed, item_text
from .expand import Expander
from .models import Item
from .novelty import (DegreeIndex, maturity, attention, SemanticIndex, combinatorial, emergence, emerging_concepts, frontier_score,
                      item_entities, record_concepts, recency, traction, weights)
from .sources import SWEEPS, arxiv
from .sources import x as xsrc
from .sources import x_collect
from .store import Store
from .util import age_days, iso, parse_dt
from .vault import Vault

Log = Callable[[str], None]


class Pipeline:
    def __init__(self, store: Optional[Store] = None, log: Log = lambda m: print(m, file=sys.stderr)) -> None:
        self.store = store or Store()
        self.cfg = config()
        self.log = log
        self.exp = Expander(self.store)
        self.vault = Vault(self.store)
        self.stats: dict = defaultdict(int)
        self.touched: set[str] = set()
        self.from_entity: dict[str, list[str]] = defaultdict(list)
        self.started = iso()
        self.checked: list[dict] = []
        self.x_polled: list[str] = []
        self.top: list[dict] = []

    def _sync_touched(self) -> None:
        self.store.db.execute("CREATE TEMP TABLE IF NOT EXISTS touched (id TEXT PRIMARY KEY)")
        self.store.db.execute("DELETE FROM touched")
        self.store.db.executemany("INSERT OR IGNORE INTO touched(id) VALUES (?)", [(i,) for i in self.touched])

    # ------------------------------------------------------------ ingest
    def ingest(self, item: Item) -> Optional[str]:
        if not item.title or not item.id:
            return None
        dt = parse_dt(item.published)
        item.published = iso(dt) if dt else None
        concepts, facets = extract(item.title, item.summary, item.concepts)
        item.concepts = concepts
        iid, is_new = self.store.upsert_item(item)
        self.stats["seen"] += 1
        if is_new:
            self.stats["new"] += 1
            self.store.update_item(iid, modalities=facets.get("modality", []), areas=facets.get("area", []),
                                   layer=(facets.get("layer") or [None])[0])
            if item.published and item.published.startswith("1970"):
                self.store.update_item(iid, status="baseline")
        self.exp.link_item(self.store.item(iid))
        self.touched.add(iid)
        if item.via_entity and (is_new or item.source == "x"):   # an X share of a known item is still a hit
            self.from_entity[item.via_entity].append(iid)
        return iid

    def collect(self, sweeps: Optional[list[str]] = None, entity_budget: Optional[int] = None) -> None:
        for name, fn in SWEEPS.items():
            if sweeps is not None and name not in sweeps:
                continue
            before = self.stats["seen"]
            try:
                for it in fn():
                    self.ingest(it)
                self.store.event("collect", f"{name}: {self.stats['seen'] - before} items")
            except Exception as exc:
                self.store.event("error", f"sweep {name} failed: {exc}"[:300])
            self.store.commit()
            self.log(f"  {name}: {self.stats['seen'] - before}")
        if sweeps is None or "x" in sweeps:
            before = self.stats["seen"]
            self.x_budget = xsrc.Budget(self.store)
            self.exp.x_budget = self.x_budget
            collector = x_collect.Collector(self.store, self.ingest, self.exp, self.x_budget)
            collector.run()
            self.x_polled = [eid for eid in collector.polled
                             if not ((self.store.entity(eid) or {}).get("meta") or {}).get("x_invalid")]
            self.store.commit()
            self.log(f"  x: {self.stats['seen'] - before}")
        pending = self.store.get_meta("pending_arxiv") or []
        if pending:
            try:
                for it in arxiv.fetch_ids(pending[:100]):
                    self.ingest(it)
                self.store.set_meta("pending_arxiv", pending[100:])
                self.store.event("collect", f"resolved {min(len(pending), 100)} arXiv papers mentioned elsewhere")
            except Exception as exc:
                self.store.event("error", f"pending arXiv fetch failed: {exc}"[:300])
        entities = self.exp.select(entity_budget)
        self.stats["entities_checked"] = len(entities)
        self.checked = entities
        for e in entities:
            for it in self.exp.check(e):
                self.ingest(it)
        self.store.event("collect", f"checked {len(entities)} watched sources (bandit-selected)")
        self.store.commit()

    # ------------------------------------------------------------ score
    def embed_new(self) -> None:
        rows = self.store.q("SELECT id, title, summary FROM items WHERE embedding IS NULL")
        for k in range(0, len(rows), 256):
            batch = rows[k:k + 256]
            for r, v in zip(batch, embed([item_text(r) for r in batch])):
                self.store.set_embedding(r["id"], v)
        self.store.commit()

    def score_quant(self) -> None:
        sem = SemanticIndex(self.store)
        deg = DegreeIndex(self.store)
        w = weights(self.store)
        k = self.cfg.get("score.knn_k", 5)
        rows = self.store.q("SELECT * FROM items WHERE scored_at IS NULL ORDER BY published")
        vecs = {}
        if rows:
            ids = [r["id"] for r in rows]
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks = ",".join("?" * len(chunk))
                for rid, blob in self.store.db.execute(f"SELECT id, embedding FROM items WHERE id IN ({marks})", chunk):
                    vecs[rid] = np.frombuffer(blob, dtype=np.float32)
        for r in rows:
            d, nbrs = sem.raw_distance(r["id"], vecs[r["id"]], r["published"], k) if r["id"] in vecs else (0.5, [])
            # short, title-only items look "far from everything"; shrink toward neutral by evidence
            evidence = min(1.0, len((r["title"] or "") + (r["summary"] or "")) / 400)
            semantic = 0.5 + (sem.percentile(d) - 0.5) * evidence
            quant = {
                "semantic": round(semantic, 4), "semantic_raw": round(d, 4), "evidence": round(evidence, 2),
                "combinatorial": round(combinatorial(self.store, r["concepts"]), 4),
                "emergence": round(emergence(self.store, r["concepts"]), 4),
                "periphery": round(deg.periphery(item_entities(r)), 4),
                "traction": round(traction(r["metrics"]), 4), "recency": round(recency(r["published"]), 4),
                "attention": round(attention(self.store, r["id"], r["published"]), 4), "neighbors": nbrs,
            }
            sem.remember(d)
            record_concepts(self.store, r["id"], r["concepts"], r["published"])
            score, explain = frontier_score(quant, r["qual"], w)
            self.store.update_item(r["id"], quant=quant, novelty=score, explain=explain, scored_at=iso())
        self.store.set_meta("semantic_history", sem.history)
        # refresh traction/recency for already-scored items that were seen again
        for iid in self.touched:
            r = self.store.item(iid)
            if r and r["quant"] and r["scored_at"] and r["scored_at"] < self.started:
                q = {**r["quant"], "traction": round(traction(r["metrics"]), 4), "recency": round(recency(r["published"]), 4),
                     "attention": round(attention(self.store, iid, r["published"]), 4)}
                score, explain = frontier_score(q, r["qual"], w)
                self.store.update_item(iid, quant=q, novelty=score, explain=explain)
        self.store.commit()
        self.stats["scored"] += len(rows)

    def judge(self) -> None:
        if llm.backend() == "none":
            self.store.event("judge", "no LLM backend; quantitative scores only")
            return
        top_k = self.cfg.get("llm.judge_top_k", 40)
        self._sync_touched()
        cands = self.store.q("""SELECT * FROM items WHERE id IN (SELECT id FROM touched) AND qual = '{}' AND status != 'baseline'
                                 AND kind != 'reference' ORDER BY novelty DESC LIMIT ?""", (top_k * 2,))
        cands = [c for c in cands if age_days(c["published"]) <= 14][:top_k]
        w = weights(self.store)
        failures = 0
        for c in cands:
            nbrs = [n for nid in (c["quant"].get("neighbors") or []) if (n := self.store.item(nid))]
            try:
                qual = llm.judge(c, nbrs)
                failures = 0
            except Exception as exc:
                self.store.event("error", f"judge failed for {c['id']}: {exc}"[:300], c["id"])
                failures += 1
                if failures >= 3:   # circuit breaker: a dead endpoint must not stall the run
                    self.store.event("judge", f"stopped reviewing after 3 consecutive failures ({llm.backend('judge')})")
                    break
                continue
            score, explain = frontier_score(c["quant"], qual, w)
            self.store.update_item(c["id"], qual=qual, novelty=score, explain=explain,
                                   modalities=qual.get("modalities") or c["modalities"],
                                   areas=qual.get("areas") or c["areas"], layer=qual.get("layer") or c["layer"])
            self.stats["judged"] += 1
            self.store.commit()
        self.store.event("judge", f"reviewed {self.stats['judged']} candidates against their nearest prior work")
        # listwise tournament over the day's best
        n = self.cfg.get("llm.tournament_size", 15)
        best = self.store.q("""SELECT * FROM items WHERE id IN (SELECT id FROM touched) AND qual != '{}'
                                ORDER BY novelty DESC LIMIT ?""", (n,))
        if len(best) >= 3:
            try:
                ranking = llm.tournament(best)
                for pos, iid in enumerate(ranking):
                    r = self.store.item(iid)
                    qual = {**r["qual"], "tournament": round(1 - pos / max(len(ranking) - 1, 1), 3)}
                    score, explain = frontier_score(r["quant"], qual, w)
                    self.store.update_item(iid, qual=qual, novelty=score, explain=explain)
                self.store.event("judge", f"tournament ranked the top {len(ranking)}")
            except Exception as exc:
                self.store.event("error", f"tournament failed: {exc}"[:300])
        self.store.commit()

    # ------------------------------------------------------------ expand
    def _run_quantile(self, pct: float) -> float:
        self._sync_touched()
        scores = sorted(r[0] for r in self.store.db.execute(
            "SELECT novelty FROM items WHERE id IN (SELECT id FROM touched) AND novelty IS NOT NULL AND status != 'baseline'"))
        return scores[min(len(scores) - 1, int(len(scores) * pct))] if scores else 100.0

    def expand(self) -> list[dict]:
        # absolute thresholds, relaxed to the run's own top tail so the graph grows from day one
        hit = min(self.cfg.get("score.frontier_threshold", 70), self._run_quantile(1 - self.cfg.get("score.hit_top_pct", 5) / 100))
        disc = self.cfg.get("score.discover_threshold", 62)
        top_n = self.cfg.get("score.discover_top_n", 20)
        sources = {e["id"]: e for e in self.checked}
        for eid in list(self.from_entity) + self.x_polled:   # X accounts are polled by the X collector
            if eid not in sources and (ent := self.store.entity(eid)):
                sources[eid] = ent
        for e in sources.values():
            ids = list(dict.fromkeys(self.from_entity.get(e["id"], [])))
            scores = [self.store.item(i)["novelty"] or 0 for i in ids]
            hits = sum(s >= hit for s in scores)
            # an empty X poll costs money, so silent X accounts lose priority faster (DEC-6)
            self.exp.record_yield(e["id"], len(ids), hits, 1.5 if e["id"] in self.x_polled else 0.5)
            if hits:
                self.exp.social(self.store.entity(e["id"]))
        self._sync_touched()
        ranked = self.store.q("""SELECT * FROM items WHERE id IN (SELECT id FROM touched) AND status != 'baseline'
                                 AND kind != 'reference' ORDER BY novelty DESC LIMIT 80""")
        strong = [r for k, r in enumerate(ranked) if (r["novelty"] or 0) >= disc or k < top_n]
        for it in strong:
            self.exp.discover_from(it)
        for it in [s for s in strong if s["id"].startswith("arxiv:")][:8]:
            for found in self.exp.snowball(it):
                self.ingest(found)
        self.store.commit()
        self.stats["new_entities"] = self.exp.new_this_run
        return self.store.q("""SELECT e.*, (SELECT evidence FROM edges WHERE src=e.id ORDER BY weight DESC LIMIT 1)
                               AS evidence FROM entities e WHERE e.created >= ? AND e.status IN ('candidate','active')
                               ORDER BY e.prior DESC LIMIT 40""", (self.started,))

    # ------------------------------------------------------------ write
    def write(self, discovered: list[dict]) -> None:
        vmin = self.cfg.get("score.vault_min", 55)
        self._sync_touched()
        vmin = min(vmin, self._run_quantile(0.97))   # always write at least the run's top ~3%
        self._sync_touched()
        rows = self.store.q("""SELECT * FROM items WHERE (id IN (SELECT id FROM touched) AND novelty >= ? AND status != 'baseline'
                                AND kind != 'reference') OR (qual != '{{}}' AND note_path IS NULL)
                                ORDER BY novelty DESC LIMIT 400""", (vmin,))
        for r in rows:
            r["note_path"] = self.vault.write_item(r)
        ents = {e["id"]: e for e in discovered}
        if rows:
            ids = [r["id"] for r in rows]
            m2 = ",".join("?" * len(ids))
            for e in self.store.q(f"""SELECT DISTINCT e.* FROM edges x JOIN entities e ON e.id=x.src
                                      WHERE x.dst IN ({m2}) AND x.rel IN ('authored','released','affiliated','owns')""", ids):
                ents[e["id"]] = e
        for e in ents.values():
            self.vault.write_entity(self.store.entity(e["id"]) or e)
        # re-render items so their people/org links resolve to the fresh entity notes
        for r in rows[:150]:
            self.vault.write_item(self.store.item(r["id"]))
        emerging = emerging_concepts(self.store) if maturity(self.store) >= 0.3 else []
        for c, s in emerging[:10]:
            self.vault.write_topic(c, s)
        top = self.store.q("""SELECT * FROM items WHERE id IN (SELECT id FROM touched) AND note_path IS NOT NULL
                               AND status != 'baseline' ORDER BY novelty DESC LIMIT 15""")
        self.stats["graph_entities"] = self.store.db.execute(
            "SELECT COUNT(*) FROM entities WHERE status IN ('active','candidate')").fetchone()[0]
        self.stats["graph_edges"] = self.store.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        self.vault.write_daily(dict(self.stats), top, emerging, discovered)
        if top:
            self.vault.write_canvas(top)
        self.store.commit()
        self.top = top

    # ------------------------------------------------------------ learn
    def learn(self) -> None:
        last = self.store.get_meta("last_weekly")
        if last and age_days(last) < 6:
            return
        retro_labels(self.store)
        rep = fit(self.store)
        centrality(self.store)
        self.store.set_meta("last_weekly", iso())
        self.log(f"  calibration: {rep.get('status')}")

    # ------------------------------------------------------------ main
    def run(self, sweeps: Optional[list[str]] = None, entity_budget: Optional[int] = None, judge: bool = True,
            write: bool = True) -> dict:
        self.store.run_id = uuid.uuid4().hex[:10]
        self.started = iso()
        self.store.db.execute("INSERT INTO runs(id, started) VALUES (?,?)", (self.store.run_id, self.started))
        self.store.event("run", "run started")
        n = self.exp.seed()   # idempotent: merges handles added to config/seeds.yaml since the last run
        if n:
            self.store.event("seed", f"loaded {n} new seed entities")
        self.vault.sync_ratings()
        steps = [("collect", lambda: self.collect(sweeps, entity_budget)), ("embed", self.embed_new),
                 ("score", self.score_quant)]
        if judge:
            steps.append(("judge", self.judge))
        for name, fn in steps:
            self.log(f"[{name}]")
            fn()
        self.log("[expand]")
        discovered = self.expand()
        self.embed_new()
        self.score_quant()   # score snowballed references/citations
        if write:
            self.log("[write]")
            self.write(discovered)
        self.learn()
        stats = dict(self.stats)
        self.store.db.execute("UPDATE runs SET finished=?, stats=? WHERE id=?",
                              (iso(), json.dumps(stats), self.store.run_id))
        self.store.event("run", f"run finished: {stats.get('new', 0)} new, {stats.get('judged', 0)} judged, "
                                f"{stats.get('new_entities', 0)} new entities")
        self.store.commit()
        self.notify(stats)
        return stats

    def notify(self, stats: dict) -> None:
        if not self.cfg.get("notify.macos", True) or sys.platform != "darwin":
            return
        top = getattr(self, "top", [])
        head = top[0]["title"][:80].replace('"', "'") if top else "no new frontier items"
        msg = f"{stats.get('new', 0)} new · top: {head}"
        try:
            subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Frontier"'],
                           timeout=5, capture_output=True)
        except Exception:
            pass
