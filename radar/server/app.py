"""Web app: feed, detail, graph, watchlist, activity, brief, and streaming chat."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..calibrate import LABEL_VALUE
from ..config import config, load_dotenv
from ..llm import backend as llm_backend
from ..sources.x import provider as x_provider
from ..store import Store
from ..vault import obsidian_uri

load_dotenv()   # the server sees keys saved by `radar setup`
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Frontier Radar")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_run_lock = threading.Lock()
_run_state: dict = {"running": False, "last": None, "error": None}


def S() -> Store:
    return Store()


def _card(it: dict, s: Store) -> dict:
    q = it.get("qual") or {}
    rating = s.one("SELECT label FROM feedback WHERE item_id=? AND source='user'", (it["id"],))
    return {
        "id": it["id"], "title": it["title"], "kind": it["kind"], "url": it["url"], "score": it.get("novelty") or 0,
        "published": it.get("published"), "discovered": it.get("discovered"), "sources": it.get("sources") or [],
        "orgs": (it.get("orgs") or [])[:4], "authors": [a["name"] for a in (it.get("authors") or [])[:6]],
        "n_authors": len(it.get("authors") or []), "summary": (it.get("summary") or "")[:600],
        "what_is_new": q.get("what_is_new"), "novelty_class": q.get("novelty_class"), "skeptic": q.get("skeptic"),
        "modalities": it.get("modalities") or [], "areas": it.get("areas") or [], "layer": it.get("layer"),
        "explain": it.get("explain") or {}, "quant": {k: v for k, v in (it.get("quant") or {}).items() if k != "neighbors"},
        "metrics": it.get("metrics") or {}, "rating": rating["label"] if rating else None, "judged": bool(q),
        "obsidian": obsidian_uri(it["note_path"]) if it.get("note_path") else None,
    }


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/feed")
def feed(days: float = 2, min: float = 0, kind: str = "", modality: str = "", small: bool = False,
         unrated: bool = False, limit: int = 80):
    s = S()
    sql = f"""SELECT * FROM items WHERE status != 'baseline' AND kind != 'reference' AND novelty >= ?
              AND discovered >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-{float(days)} days')"""
    args: list = [min]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if modality:
        sql += " AND modalities LIKE ?"
        args.append(f'%"{modality}"%')
    if small:
        sql += " AND json_extract(quant, '$.periphery') >= 0.6"
    if unrated:
        sql += " AND id NOT IN (SELECT item_id FROM feedback WHERE source='user')"
    sql += " ORDER BY novelty DESC LIMIT ?"
    args.append(limit)
    return [_card(it, s) for it in s.q(sql, args)]


@app.get("/api/item/{item_id:path}")
def item(item_id: str):
    s = S()
    it = s.item(item_id)
    if not it:
        raise HTTPException(404)
    card = _card(it, s)
    q = it.get("qual") or {}
    card.update(claims=q.get("claims") or [], confidence=q.get("confidence"), full_summary=it.get("summary"))
    rels = []
    for e in s.neighbors(item_id, 120):
        other = e["dst"] if e["src"] == item_id else e["src"]
        node = s.entity(other) or s.item(other)
        if not node:
            continue
        rels.append({"id": other, "label": node.get("name") or node.get("title"), "rel": e["rel"],
                     "dir": "out" if e["src"] == item_id else "in", "evidence": e["evidence"],
                     "type": node.get("type") or node.get("kind"), "score": node.get("novelty")})
    card["relations"] = rels
    card["neighbors"] = [{"id": n["id"], "title": n["title"], "score": n.get("novelty")}
                         for nid in (it.get("quant") or {}).get("neighbors", []) if (n := s.item(nid))]
    return card


@app.get("/api/entity/{eid:path}")
def entity(eid: str):
    s = S()
    e = s.entity(eid)
    if not e:
        raise HTTPException(404)
    items = s.q("""SELECT i.* FROM edges x JOIN items i ON i.id=x.dst WHERE x.src=?
                   ORDER BY i.novelty DESC LIMIT 40""", (eid,))
    links = s.q("""SELECT x.*, e.name, e.type FROM edges x JOIN entities e ON e.id = CASE WHEN x.src=? THEN x.dst ELSE x.src END
                   WHERE (x.src=? OR x.dst=?) LIMIT 60""", (eid, eid, eid))
    via = s.entity(e["discovered_via"]) or s.item(e["discovered_via"] or "")
    return {**e, "yield": e["alpha"] / (e["alpha"] + e["beta"]), "items": [_card(i, s) for i in items],
            "links": links, "via_label": (via or {}).get("name") or (via or {}).get("title"),
            "obsidian": obsidian_uri(e["note_path"]) if e.get("note_path") else None}


@app.get("/api/graph")
def graph(focus: str = "", days: float = 3, limit: int = 70):
    s = S()
    nodes, edges, seen = {}, [], set()

    def add_item(it):
        nodes[it["id"]] = {"id": it["id"], "label": it["title"][:60], "kind": "item", "sub": it["kind"],
                           "score": it.get("novelty") or 0}

    def add_ent(e):
        nodes[e["id"]] = {"id": e["id"], "label": e["name"][:40], "kind": "entity", "sub": e["type"],
                          "score": 100 * (e["centrality"] or 0), "status": e["status"]}

    if focus:
        center = s.item(focus) or s.entity(focus)
        if not center:
            raise HTTPException(404)
        (add_item if "title" in center else add_ent)(center)
        for x in s.neighbors(focus, 80):
            other = x["dst"] if x["src"] == focus else x["src"]
            n = s.item(other) or s.entity(other)
            if n:
                (add_item if "title" in n else add_ent)(n)
                edges.append({"source": x["src"], "target": x["dst"], "rel": x["rel"]})
    else:
        top = s.q(f"""SELECT * FROM items WHERE status != 'baseline' AND kind != 'reference'
                      AND discovered >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-{float(days)} days')
                      ORDER BY novelty DESC LIMIT ?""", (limit,))
        for it in top:
            add_item(it)
        ids = [t["id"] for t in top]
        # topic hubs (modality/area facets) make clusters visible even when no authors are shared
        for it in top:
            for facet in (it.get("modalities") or [])[:2] + (it.get("areas") or [])[:2]:
                tid = f"topic:{facet}"
                nodes.setdefault(tid, {"id": tid, "label": facet, "kind": "topic", "sub": "topic", "score": 0})
                nodes[tid]["score"] += 4
                edges.append({"source": it["id"], "target": tid, "rel": "about"})
        if ids:
            marks = ",".join("?" * len(ids))
            # who is behind each item: orgs + the first two authors, plus anyone shared across items
            for x in s.q(f"""SELECT x.*, e.* FROM edges x JOIN entities e ON e.id=x.src WHERE x.dst IN ({marks})
                             AND x.rel IN ('released','authored','affiliated','owns','mentions')""", ids):
                shared = s.db.execute(f"SELECT COUNT(*) FROM edges WHERE src=? AND dst IN ({marks})",
                                      (x["src"], *ids)).fetchone()[0]
                lead = x["rel"] != "authored" or len([e for e in edges if e["target"] == x["dst"] and e["rel"] == "authored"]) < 2
                if shared > 1 or lead or x["status"] in ("active", "candidate"):
                    add_ent(x)
                    edges.append({"source": x["src"], "target": x["dst"], "rel": x["rel"]})
            for x in s.q(f"SELECT * FROM edges WHERE src IN ({marks}) AND dst IN ({marks})", ids + ids):
                edges.append({"source": x["src"], "target": x["dst"], "rel": x["rel"]})
    edges = [e for e in edges if e["source"] in nodes and e["target"] in nodes
             and (k := (e["source"], e["target"], e["rel"])) not in seen and not seen.add(k)]
    return {"nodes": list(nodes.values()), "edges": edges}


@app.get("/api/watchlist")
def watchlist(status: str = "active,candidate", limit: int = 200):
    s = S()
    sts = [x for x in status.split(",") if x]
    marks = ",".join("?" * len(sts))
    rows = s.q(f"""SELECT * FROM entities WHERE status IN ({marks})
                   ORDER BY (alpha/(alpha+beta)) * (0.5+prior) + centrality DESC LIMIT ?""", (*sts, limit))
    return [{**r, "yield": r["alpha"] / (r["alpha"] + r["beta"])} for r in rows]


@app.get("/api/activity")
def activity(limit: int = 150, kind: str = ""):
    s = S()
    if kind:
        return s.q("SELECT * FROM events WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))
    return s.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))


@app.get("/api/stats")
def stats():
    s = S()
    one = lambda sql: s.db.execute(sql).fetchone()[0]  # noqa: E731
    last = s.one("SELECT * FROM runs ORDER BY started DESC LIMIT 1")
    return {"items": one("SELECT COUNT(*) FROM items"),
            "today": one("SELECT COUNT(*) FROM items WHERE discovered >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 days')"),
            "frontier": one("SELECT COUNT(*) FROM items WHERE novelty >= 70 AND discovered >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-2 days')"),
            "watched": one("SELECT COUNT(*) FROM entities WHERE status IN ('active','candidate')"),
            "edges": one("SELECT COUNT(*) FROM edges"), "ratings": one("SELECT COUNT(*) FROM feedback WHERE source='user'"),
            "last_run": last, "running": _run_state["running"], "llm": llm_backend(), "x": x_provider()}


@app.get("/api/brief")
def brief():
    root = config().path("vault") / "Daily"
    files = sorted(root.glob("*.md")) if root.exists() else []
    if not files:
        return {"date": None, "markdown": "_No brief yet. Run the radar._"}
    text = files[-1].read_text(encoding="utf-8")
    body = text.split("---", 2)[-1] if text.startswith("---") else text
    return {"date": files[-1].stem, "markdown": body.replace("<!-- radar:auto:start -->", "").replace("<!-- radar:auto:end -->", "")}


@app.get("/api/search")
def search(q: str):
    s = S()
    items = [{"id": i["id"], "label": i["title"], "type": i["kind"], "score": i.get("novelty")} for i in s.search(q, 12)]
    ents = s.q("SELECT id, name AS label, type, status FROM entities WHERE name LIKE ? AND status != 'known' LIMIT 8",
               (f"%{q}%",))
    return {"items": items, "entities": ents}


class Rate(BaseModel):
    id: str
    label: str


@app.post("/api/rate")
def rate(r: Rate):
    if r.label not in LABEL_VALUE and r.label != "clear":
        raise HTTPException(400, "bad label")
    s = S()
    if r.label == "clear":
        s.db.execute("DELETE FROM feedback WHERE item_id=? AND source='user'", (r.id,))
    else:
        s.feedback(r.id, r.label, LABEL_VALUE[r.label], "user")
        s.event("feedback", f"rated {r.id} as {r.label} (web)", r.id)
    s.commit()
    return {"ok": True}


class Ask(BaseModel):
    question: str
    history: Optional[list] = None


@app.post("/api/ask")
def ask_endpoint(a: Ask):
    from ..ask import ask

    def gen():
        stream, items, ents = ask(S(), a.question, a.history)
        sources = [{"id": i["id"], "label": i["title"], "score": i.get("novelty")} for i in items] + \
                  [{"id": e["id"], "label": e["name"], "score": None} for e in ents]
        yield f"event: sources\ndata: {json.dumps(sources)}\n\n"
        try:
            for chunk in stream:
                yield f"data: {json.dumps(chunk)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps(f'[error: {exc}]')}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/run")
def trigger_run(no_judge: bool = False):
    if not _run_lock.acquire(blocking=False):
        return {"started": False, "reason": "already running"}

    def work():
        from ..pipeline import Pipeline
        _run_state.update(running=True, error=None)
        try:
            _run_state["last"] = Pipeline(log=lambda m: None).run(judge=not no_judge)
        except Exception as exc:
            _run_state["error"] = str(exc)
        finally:
            _run_state["running"] = False
            _run_lock.release()

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}
