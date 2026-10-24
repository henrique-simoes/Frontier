"""Novelty signals and the learned Frontier score. See docs/NOVELTY.md."""
from __future__ import annotations

import bisect
import itertools
import math
from datetime import timedelta
from typing import Optional

import numpy as np

from .config import config
from .store import Store, entity_id
from .util import age_days, clamp01, iso, now, parse_dt

FEATURES = ["semantic", "combinatorial", "emergence", "periphery", "traction", "recency", "attention",
            "llm_novelty", "llm_rigor", "llm_impact", "tournament", "class_prior"]
LLM_FEATURES = {"llm_novelty", "llm_rigor", "llm_impact", "tournament", "class_prior"}

# Expert priors. Calibration pulls toward these, not toward zero.
PRIOR_WEIGHTS = {"semantic": 1.2, "combinatorial": 0.75, "emergence": 0.7, "periphery": 0.45, "traction": 0.65,
                 "recency": 0.6, "attention": 0.5, "llm_novelty": 1.65, "llm_rigor": 0.45, "llm_impact": 1.05, "tournament": 0.6,
                 "class_prior": 0.9, "bias": -4.5}
IMPUTE = {"llm_novelty": 0.45, "llm_rigor": 0.5, "llm_impact": 0.4, "tournament": 0.3, "class_prior": 0.4}

CLASS_PRIOR = {"new_paradigm": 1.0, "new_capability": 0.85, "new_architecture": 0.8, "new_method": 0.65,
               "new_system_or_infra": 0.6, "efficiency_step_change": 0.65, "new_data_or_benchmark": 0.5,
               "empirical_finding": 0.45, "incremental": 0.15, "survey_or_position": 0.2, "hype_or_unclear": 0.05}

BROAD_PREFIXES = ("arxiv/", "modality/", "area/", "layer/")


def weights(store: Store) -> dict[str, float]:
    return {**PRIOR_WEIGHTS, **(store.get_meta("weights") or {})}


# ---------------------------------------------------------------- concepts

def concept_stats(store: Store, concepts: list[str]) -> tuple[int, dict[str, dict], dict[tuple[str, str], int]]:
    n_docs = store.db.execute("SELECT COUNT(DISTINCT item_id) FROM concept_items").fetchone()[0] or 0
    if not concepts:
        return n_docs, {}, {}
    marks = ",".join("?" * len(concepts))
    stats = {r["term"]: r for r in store.q(f"SELECT * FROM concepts WHERE term IN ({marks})", concepts)}
    pairs = {(r["a"], r["b"]): r["n"] for r in store.q(
        f"SELECT a, b, n FROM concept_pairs WHERE a IN ({marks}) AND b IN ({marks})", concepts + concepts)}
    return n_docs, stats, pairs


def record_concepts(store: Store, item_id: str, concepts: list[str], published: Optional[str]) -> None:
    concepts = sorted(set(concepts))[:14]
    ts = published or iso()
    for c in concepts:
        if store.db.execute("SELECT 1 FROM concept_items WHERE term=? AND item_id=?", (c, item_id)).fetchone():
            continue
        store.db.execute("INSERT INTO concept_items(term, item_id, published) VALUES (?,?,?)", (c, item_id, ts))
        store.db.execute("""INSERT INTO concepts(term, df, first_seen, last_seen) VALUES (?,1,?,?)
                            ON CONFLICT(term) DO UPDATE SET df=df+1, last_seen=MAX(last_seen, excluded.last_seen),
                            first_seen=MIN(first_seen, excluded.first_seen)""", (c, ts, ts))
    for a, b in itertools.combinations(concepts, 2):
        store.db.execute("""INSERT INTO concept_pairs(a, b, n) VALUES (?,?,1)
                            ON CONFLICT(a, b) DO UPDATE SET n=n+1""", (a, b))


def combinatorial(store: Store, concepts: list[str]) -> float:
    """Uzzi-style atypicality: how much rarer is this pairing than chance predicts?"""
    concepts = sorted(set(concepts))[:14]
    n_docs, stats, pairs = concept_stats(store, concepts)
    if n_docs < 30:
        return 0.5
    atyps = []
    for a, b in itertools.combinations(concepts, 2):
        sa, sb = stats.get(a), stats.get(b)
        if not sa or not sb or sa["df"] < 2 or sb["df"] < 2:
            continue
        expected = sa["df"] * sb["df"] / n_docs
        atyps.append(math.log((expected + 1) / (pairs.get((a, b), 0) + 1)))
    if not atyps:
        return 0.5
    p90 = float(np.percentile(atyps, 90))
    return clamp01(1 / (1 + math.exp(-1.6 * (p90 - 0.6))))


def maturity(store: Store) -> float:
    """0 at cold start -> 1 once the corpus spans 21 days. Emergence is meaningless without history."""
    first = store.db.execute("SELECT MIN(discovered) FROM items").fetchone()[0]
    return clamp01(age_days(first) / 21) if first else 0.0


def emergence(store: Store, concepts: list[str]) -> float:
    """Is this item early in a wave? High when its specific concepts are new and accelerating."""
    m = maturity(store)
    if m < 0.05:
        return 0.3
    specific = [c for c in set(concepts) if not c.startswith(BROAD_PREFIXES)]
    if not specific:
        return 0.2
    since = iso(now() - timedelta(days=14))
    best = 0.0
    for c in specific:
        st = store.one("SELECT * FROM concepts WHERE term=?", (c,))
        if not st or st["df"] < 2:
            best = max(best, 0.35)          # unseen term: possibly new, possibly noise
            continue
        recent = store.db.execute("SELECT COUNT(*) FROM concept_items WHERE term=? AND published>=?",
                                  (c, since)).fetchone()[0]
        share = recent / st["df"]
        fresh = math.exp(-age_days(st["first_seen"]) / 30)
        support = min(1.0, math.log1p(recent) / math.log(8))   # need a few occurrences to call it a wave
        best = max(best, share * (0.5 + 0.5 * fresh) * (0.4 + 0.6 * support))
    return clamp01(0.3 * (1 - m) + m * best)


# ---------------------------------------------------------------- other signals

def traction(metrics: dict) -> float:
    raw = (metrics.get("stars_per_day", 0) + metrics.get("upvotes", 0) + metrics.get("hn_points", 0) / 10
           + metrics.get("likes_per_day", 0) + metrics.get("bsky_likes", 0) / 20 + metrics.get("citations", 0) / 5
           + metrics.get("trending", 0) / 20 + metrics.get("x_likes", 0) / 50 + metrics.get("x_reposts", 0) / 10
           + metrics.get("x_quotes", 0) / 5 + metrics.get("x_bookmarks", 0) / 20)
    return clamp01(1 - math.exp(-math.log1p(raw) / 2.5))


def attention(store: Store, item_id: str, published: Optional[str]) -> float:
    """Researcher attention: 1 - exp(-sum over distinct sharers of yield * (0.5 + centrality)),
    x1.5 when any share came within 48h of publication. 0 when nobody the radar knows shared it."""
    rows = store.q("""SELECT x.src, x.evidence, e.alpha, e.beta, e.centrality FROM edges x
                      JOIN entities e ON e.id = x.src WHERE x.dst = ? AND x.rel = 'shared'""", (item_id,))
    total, early, seen = 0.0, False, set()
    pub = parse_dt(published)
    for s in rows:
        if s["src"] in seen:
            continue
        seen.add(s["src"])
        total += s["alpha"] / max(s["alpha"] + s["beta"], 1e-9) * (0.5 + float(s["centrality"] or 0))
        ev = s["evidence"] or ""
        at = parse_dt(ev.split("@", 1)[1]) if "@" in ev else None
        if pub and at and (at - pub).total_seconds() <= 48 * 3600:
            early = True
    return clamp01((1 - math.exp(-total)) * (1.5 if early else 1.0))


def recency(published: Optional[str]) -> float:
    if not parse_dt(published):
        return 0.5
    return clamp01(0.5 ** (age_days(published) / config().get("score.recency_half_life_days", 4)))


class DegreeIndex:
    """Snapshot of entity degrees for the periphery signal."""

    def __init__(self, store: Store) -> None:
        rows = store.db.execute("""SELECT node, COUNT(*) FROM (SELECT src AS node FROM edges UNION ALL
                                   SELECT dst FROM edges) WHERE node LIKE 'person:%' OR node LIKE 'org:%'
                                   GROUP BY node""").fetchall()
        self.deg = {r[0]: r[1] for r in rows}
        self.sorted = sorted(self.deg.values())

    def periphery(self, entity_ids: list[str]) -> float:
        if not self.sorted or len(self.sorted) < 20:
            return 0.5
        known = [self.deg.get(e, 0) for e in entity_ids]
        if not known:
            return 0.5
        d = max(known)   # mid-rank percentile: ties (most authors have degree 1) land in the middle
        pct = (bisect.bisect_left(self.sorted, d) + bisect.bisect_right(self.sorted, d)) / (2 * len(self.sorted))
        return clamp01(1 - pct)


def item_entities(item: dict) -> list[str]:
    ids = [entity_id("person", a["name"]) for a in (item.get("authors") or [])[:12] if a.get("name")]
    ids += [entity_id("org", o) for o in item.get("orgs") or []]
    return ids


# ---------------------------------------------------------------- semantic

class SemanticIndex:
    """Brute-force kNN over the corpus. ~30 ms per 100k x 1024 batch on a laptop."""

    def __init__(self, store: Store) -> None:
        self.ids, pubs, self.mat = store.embeddings()
        self.pubs = np.array(pubs, dtype=object)
        self.pos = {i: k for k, i in enumerate(self.ids)}
        self.history: list[float] = store.get_meta("semantic_history") or []

    def raw_distance(self, item_id: str, vec: np.ndarray, published: Optional[str], k: int) -> tuple[float, list[str]]:
        if len(self.ids) < k + 5:
            return 0.5, []
        sims = self.mat @ vec
        mask = np.ones(len(self.ids), dtype=bool)
        if item_id in self.pos:
            mask[self.pos[item_id]] = False
        if published:
            mask &= self.pubs < published   # only prior work counts
        if mask.sum() < k:
            return 0.5, []
        cand = np.where(mask)[0]
        top = cand[np.argpartition(-sims[cand], min(k, len(cand) - 1))[:k]]
        top = top[np.argsort(-sims[top])]
        return float(1 - sims[top].mean()), [self.ids[t] for t in top]

    def percentile(self, d: float) -> float:
        if len(self.history) < 50:
            return clamp01(d)
        s = sorted(self.history)
        return bisect.bisect_left(s, d) / len(s)

    def remember(self, d: float) -> None:
        self.history = (self.history + [d])[-3000:]


# ---------------------------------------------------------------- composite

def feature_vector(quant: dict, qual: dict) -> dict[str, float]:
    # attention defaults to 0: no researcher shared it (or X is off), which must not look like evidence
    x = {k: float(quant.get(k, 0.0 if k == "attention" else 0.5)) for k in FEATURES if k not in LLM_FEATURES}
    if qual and qual.get("novelty") is not None:
        x["llm_novelty"] = clamp01(float(qual.get("novelty", 0)) / 10)
        x["llm_rigor"] = clamp01(float(qual.get("rigor", 0)) / 10)
        x["llm_impact"] = clamp01(float(qual.get("impact", 0)) / 10)
        x["class_prior"] = CLASS_PRIOR.get(qual.get("novelty_class", ""), 0.4)
        x["tournament"] = float(qual.get("tournament", IMPUTE["tournament"]))
    else:
        x.update(IMPUTE)
    return x


def frontier_score(quant: dict, qual: dict, w: dict[str, float]) -> tuple[float, dict[str, float]]:
    x = feature_vector(quant, qual)
    contrib = {k: w.get(k, 0) * x[k] for k in FEATURES}
    z = w.get("bias", 0) + sum(contrib.values())
    p = 1 / (1 + math.exp(-z))
    judged = bool(qual and qual.get("novelty") is not None)
    # imputed reviewer features move the score but are not evidence, so they never appear as drivers
    shown = {k: round(v, 3) for k, v in contrib.items() if judged or k not in LLM_FEATURES}
    return round(100 * p, 1), shown


def emerging_concepts(store: Store, n: int = 12) -> list[tuple[str, float]]:
    since = iso(now() - timedelta(days=3))
    rows = store.q("SELECT term FROM concepts WHERE df >= 3 AND last_seen >= ? ORDER BY df DESC LIMIT 400", (since,))
    scored = [(r["term"], emergence(store, [r["term"]])) for r in rows if not r["term"].startswith(BROAD_PREFIXES)]
    return sorted(scored, key=lambda t: -t[1])[:n]
