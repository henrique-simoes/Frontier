"""Grounded Q&A: hybrid retrieval (FTS5 + embedding kNN + graph neighbours) -> Claude with citations."""
from __future__ import annotations

import re
from typing import Iterator, Optional

import numpy as np

from . import llm
from .embed import embed
from .store import Store
from .util import slugify

RECENT_HINT = re.compile(r"\b(today|this week|latest|new|recent|yesterday|now)\b", re.I)


def retrieve(store: Store, question: str, k: int = 18) -> tuple[list[dict], list[dict]]:
    scores: dict[str, float] = {}
    for rank, it in enumerate(store.search(question, limit=30)):
        scores[it["id"]] = scores.get(it["id"], 0) + 1 / (60 + rank)          # reciprocal rank fusion
    ids, _, mat = store.embeddings(limit=60000)
    if len(ids):
        sims = mat @ embed([question])[0]
        for rank, idx in enumerate(np.argsort(-sims)[:30]):
            scores[ids[idx]] = scores.get(ids[idx], 0) + 1 / (60 + rank)
    if RECENT_HINT.search(question):
        for rank, r in enumerate(store.q("""SELECT id FROM items WHERE discovered >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-3 days')
                                            AND status != 'baseline' ORDER BY novelty DESC LIMIT 20""")):
            scores[r["id"]] = scores.get(r["id"], 0) + 1 / (60 + rank)
    items = [store.item(i) for i, _ in sorted(scores.items(), key=lambda t: -t[1])[:k]]
    items = [i for i in items if i]
    # entities named in the question, plus the people/orgs behind the retrieved items
    ents: dict[str, dict] = {}
    for word in re.findall(r"[A-Z][\w.-]+(?:\s+[A-Z][\w.-]+)*", question):
        for typ in ("org", "person"):
            if e := store.entity(f"{typ}:{slugify(word)}"):
                ents[e["id"]] = e
    for it in items[:8]:
        for e in store.q("""SELECT e.* FROM edges x JOIN entities e ON e.id=x.src WHERE x.dst=?
                            ORDER BY e.centrality DESC LIMIT 3""", (it["id"],)):
            ents.setdefault(e["id"], e)
    return items, list(ents.values())[:15]


def context(items: list[dict], ents: list[dict]) -> str:
    out = []
    for it in items:
        q = it.get("qual") or {}
        out.append(f"[{it['id']}] {it['title']} (frontier {it.get('novelty') or 0:.0f}, {it['kind']}, "
                   f"{(it.get('published') or '')[:10]}, class {q.get('novelty_class', 'unjudged')})\n"
                   f"  new: {q.get('what_is_new', '')}\n  summary: {(it.get('summary') or '')[:600]}")
    for e in ents:
        out.append(f"[{e['id']}] {e['name']} ({e['type']}, status {e['status']}, hits {e['hits']}, "
                   f"centrality {e['centrality'] or 0:.2f})")
    return "\n".join(out)


def ask(store: Store, question: str, history: Optional[list[dict]] = None) -> tuple[Iterator[str], list[dict], list[dict]]:
    items, ents = retrieve(store, question)
    return llm.ask_stream(question, context(items, ents), history), items, ents
