"""Learning what 'frontier' means: re-fit scorer weights from ratings + retrospective outcomes,
and compute personalised-PageRank centrality from high-novelty items."""
from __future__ import annotations

import math
import random
from collections import defaultdict

import numpy as np

from . import http
from .novelty import FEATURES, PRIOR_WEIGHTS, feature_vector, frontier_score, weights
from .store import Store
from .util import iso

LABEL_VALUE = {"frontier": 1.0, "solid": 0.6, "incremental": 0.2, "hype": 0.0, "irrelevant": 0.0}
SOURCE_WEIGHT = {"user": 1.0, "retro": 0.3}


def _dataset(store: Store):
    rows = store.q("""SELECT f.item_id, f.value, f.source, i.quant, i.qual FROM feedback f
                      JOIN items i ON i.id = f.item_id WHERE i.quant != '{}'""")
    X, y, sw = [], [], []
    for r in rows:
        x = feature_vector(r["quant"], r["qual"])
        X.append([x[k] for k in FEATURES])
        y.append(float(r["value"]))
        sw.append(SOURCE_WEIGHT.get(r["source"], 0.3))
    return np.array(X, dtype=float), np.array(y), np.array(sw)


def _loss(w, b, X, y, sw):
    p = 1 / (1 + np.exp(-(X @ w + b)))
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-(sw * (y * np.log(p) + (1 - y) * np.log(1 - p))).sum() / sw.sum())


def fit(store: Store, l2: float = 2.0, steps: int = 600, lr: float = 0.15) -> dict:
    X, y, sw = _dataset(store)
    if len(y) < 12:
        return {"status": "skipped", "reason": f"need >= 12 labels, have {len(y)}"}
    w0 = np.array([PRIOR_WEIGHTS[k] for k in FEATURES])
    b0 = PRIOR_WEIGHTS["bias"]
    cur = weights(store)
    idx = list(range(len(y)))
    random.Random(7).shuffle(idx)
    cut = max(3, len(idx) // 5)
    te, tr = idx[:cut], idx[cut:]
    w, b = np.array([cur[k] for k in FEATURES]), cur["bias"]
    before = _loss(w, b, X[te], y[te], sw[te])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(X[tr] @ w + b)))
        g = (sw[tr] * (p - y[tr]))
        gw = X[tr].T @ g / sw[tr].sum() + l2 * (w - w0) / len(tr)
        gb = g.sum() / sw[tr].sum() + l2 * (b - b0) / len(tr)
        w -= lr * gw
        b -= lr * gb
    after = _loss(w, b, X[te], y[te], sw[te])
    report = {"labels": len(y), "heldout_before": round(before, 4), "heldout_after": round(after, 4)}
    if after > before + 1e-4:
        store.event("calibrate", f"rejected new weights (held-out loss {before:.3f} -> {after:.3f})")
        return {"status": "rejected", **report}
    new = {k: round(float(v), 4) for k, v in zip(FEATURES, w)}
    new["bias"] = round(float(b), 4)
    hist = store.get_meta("weights_history") or []
    hist.append({"at": iso(), "weights": new, **report})
    store.set_meta("weights", new)
    store.set_meta("weights_history", hist[-50:])
    store.event("calibrate", f"learned new weights from {len(y)} labels (held-out loss {before:.3f} -> {after:.3f})")
    rescore(store)
    return {"status": "updated", **report, "weights": new}


def rescore(store: Store) -> int:
    w = weights(store)
    n = 0
    for r in store.q("SELECT id, quant, qual FROM items WHERE quant != '{}'"):
        score, explain = frontier_score(r["quant"], r["qual"], w)
        store.update_item(r["id"], novelty=score, explain=explain)
        n += 1
    store.commit()
    return n


def _current_metric(item: dict) -> float | None:
    iid = item["id"]
    try:
        if iid.startswith("gh:"):
            return float(http.get(f"https://api.github.com/repos/{iid[3:]}", ttl=86400).get("stargazers_count", 0))
        if iid.startswith("hf:"):
            return float(http.get(f"https://huggingface.co/api/models/{item['title']}", ttl=86400).get("likes", 0))
        if iid.startswith("arxiv:"):
            r = http.get(f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{iid[6:]}",
                         params={"fields": "citationCount"}, ttl=86400)
            return float(r.get("citationCount", 0))
    except Exception:
        return None
    return None


def retro_labels(store: Store, limit: int = 60) -> int:
    """Label 30-60 day old items by what actually happened (growth in stars/likes/citations)."""
    rows = store.q("""SELECT * FROM items WHERE discovered < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-30 days')
                      AND discovered > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-60 days') AND novelty IS NOT NULL
                      AND id NOT IN (SELECT item_id FROM feedback WHERE source='retro')
                      ORDER BY novelty DESC LIMIT ?""", (limit,))
    growth = []
    for r in rows:
        cur = _current_metric(r)
        if cur is None:
            continue
        m = r["metrics"] or {}
        base = m.get("stars", 0) + m.get("likes", 0) + m.get("citations", 0)
        growth.append((r["id"], math.log1p(max(cur - base, 0))))
    if len(growth) < 10:
        return 0
    vals = sorted(g for _, g in growth)
    hi, mid = vals[int(0.9 * len(vals))], vals[len(vals) // 2]
    for iid, g in growth:
        if g >= hi:
            store.feedback(iid, "retro_top", 0.8, "retro")
        elif g <= mid:
            store.feedback(iid, "retro_low", 0.2, "retro")
    store.event("calibrate", f"retrospective labels from {len(growth)} items aged 30-60 days")
    store.commit()
    return len(growth)


def centrality(store: Store, alpha: float = 0.15, iters: int = 30) -> int:
    """Personalised PageRank seeded on high-novelty items; writes entities.centrality in [0,1]."""
    edges = store.db.execute("SELECT src, dst, weight FROM edges").fetchall()
    if not edges:
        return 0
    out = defaultdict(list)
    nodes = set()
    for s, d, w in edges:
        out[s].append((d, w))
        out[d].append((s, w))   # undirected walk: hubs on either side matter
        nodes.update((s, d))
    seeds = {r[0]: max(r[1] - 60, 0) for r in
             store.db.execute("SELECT id, novelty FROM items WHERE novelty >= 65").fetchall()}
    z = sum(seeds.values()) or 1
    pers = {k: v / z for k, v in seeds.items()}
    if not pers:
        return 0
    rank = dict(pers)
    for _ in range(iters):
        nxt = defaultdict(float)
        for n, r in rank.items():
            nbrs = out.get(n)
            if not nbrs:
                continue
            tot = sum(w for _, w in nbrs)
            for m, w in nbrs:
                nxt[m] += (1 - alpha) * r * w / tot
        for k, v in pers.items():
            nxt[k] += alpha * v
        rank = nxt
    ents = {k: v for k, v in rank.items() if k.startswith(("person:", "org:"))}
    if not ents:
        return 0
    top = max(ents.values())
    store.db.execute("UPDATE entities SET centrality=0")
    for k, v in ents.items():
        store.db.execute("UPDATE entities SET centrality=? WHERE id=?", (round(v / top, 4), k))
    store.event("calibrate", f"centrality recomputed over {len(nodes)} nodes")
    store.commit()
    return len(ents)
