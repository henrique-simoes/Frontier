"""SQLite store: items, entities, typed edges, concepts, feedback, events, learned weights."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

from .config import config
from .models import Item
from .util import iso, slugify, title_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY, kind TEXT, title TEXT, title_key TEXT, summary TEXT, url TEXT,
  published TEXT, discovered TEXT, source TEXT, sources TEXT DEFAULT '[]',
  authors TEXT DEFAULT '[]', orgs TEXT DEFAULT '[]', concepts TEXT DEFAULT '[]',
  modalities TEXT DEFAULT '[]', areas TEXT DEFAULT '[]', layer TEXT,
  metrics TEXT DEFAULT '{}', links TEXT DEFAULT '[]', raw TEXT DEFAULT '{}',
  embedding BLOB, quant TEXT DEFAULT '{}', qual TEXT DEFAULT '{}',
  novelty REAL, explain TEXT DEFAULT '{}', note_path TEXT, status TEXT DEFAULT 'new',
  via_entity TEXT, scored_at TEXT
);
CREATE INDEX IF NOT EXISTS items_published ON items(published);
CREATE INDEX IF NOT EXISTS items_novelty ON items(novelty);
CREATE INDEX IF NOT EXISTS items_title_key ON items(title_key);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(id UNINDEXED, title, summary, concepts);

CREATE TABLE IF NOT EXISTS entities (
  id TEXT PRIMARY KEY, type TEXT, name TEXT, aliases TEXT DEFAULT '[]', handles TEXT DEFAULT '{}',
  meta TEXT DEFAULT '{}', status TEXT DEFAULT 'candidate', prior REAL DEFAULT 0.3,
  alpha REAL DEFAULT 1, beta REAL DEFAULT 1, checks INTEGER DEFAULT 0, hits INTEGER DEFAULT 0,
  last_checked TEXT, depth INTEGER DEFAULT 0, discovered_via TEXT, created TEXT,
  centrality REAL DEFAULT 0, note_path TEXT
);
CREATE TABLE IF NOT EXISTS handles (kind TEXT, value TEXT, entity_id TEXT, PRIMARY KEY (kind, value));

CREATE TABLE IF NOT EXISTS edges (
  src TEXT, dst TEXT, rel TEXT, weight REAL DEFAULT 1, created TEXT, evidence TEXT,
  PRIMARY KEY (src, dst, rel)
);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);

CREATE TABLE IF NOT EXISTS concepts (term TEXT PRIMARY KEY, df INTEGER DEFAULT 0, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS concept_pairs (a TEXT, b TEXT, n INTEGER DEFAULT 0, PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS concept_items (term TEXT, item_id TEXT, published TEXT, PRIMARY KEY (term, item_id));

CREATE TABLE IF NOT EXISTS feedback (item_id TEXT, label TEXT, value REAL, source TEXT, created TEXT,
  PRIMARY KEY (item_id, source));
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, kind TEXT, message TEXT, ref TEXT);
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, started TEXT, finished TEXT, stats TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

JSON_COLS = {"sources", "authors", "orgs", "concepts", "modalities", "areas", "metrics", "links", "raw",
             "quant", "qual", "explain", "aliases", "handles", "meta", "stats"}


def _row(r: sqlite3.Row | None) -> Optional[dict]:
    if r is None:
        return None
    d = dict(r)
    for k in list(d):
        if k in JSON_COLS and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    d.pop("embedding", None)
    return d


def entity_id(type_: str, name: str) -> str:
    return f"{type_}:{slugify(name)}"


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config().path("db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.run_id: Optional[str] = None

    # ---------- generic ----------
    def q(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        return [_row(r) for r in self.db.execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: Iterable[Any] = ()) -> Optional[dict]:
        return _row(self.db.execute(sql, tuple(args)).fetchone())

    def commit(self) -> None:
        self.db.commit()

    def get_meta(self, key: str, default: Any = None) -> Any:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_meta(self, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # ---------- events ----------
    def event(self, kind: str, message: str, ref: str = "") -> None:
        self.db.execute("INSERT INTO events(ts, run_id, kind, message, ref) VALUES (?,?,?,?,?)",
                        (iso(), self.run_id or "", kind, message, ref))

    # ---------- items ----------
    def find_duplicate(self, item: Item) -> Optional[str]:
        if self.db.execute("SELECT 1 FROM items WHERE id=?", (item.id,)).fetchone():
            return item.id
        tk = title_key(item.title)
        if len(tk) >= 24:
            r = self.db.execute("SELECT id FROM items WHERE title_key=?", (tk,)).fetchone()
            if r:
                return r[0]
        return None

    def upsert_item(self, item: Item) -> tuple[str, bool]:
        """Insert or merge. Returns (id, is_new). Merging unions sources/links and maxes metrics."""
        existing_id = self.find_duplicate(item)
        authors = [{"name": a.name, "ids": a.ids, "affiliations": a.affiliations} for a in item.authors]
        if existing_id:
            cur = self.one("SELECT * FROM items WHERE id=?", (existing_id,))
            sources = sorted(set(cur["sources"]) | {item.source})
            metrics = dict(cur["metrics"])
            for k, v in item.metrics.items():
                metrics[k] = max(float(v or 0), float(metrics.get(k, 0) or 0))
            links = list(dict.fromkeys(cur["links"] + item.links))
            concepts = list(dict.fromkeys(cur["concepts"] + item.concepts))
            summary = cur["summary"] if len(cur["summary"] or "") >= len(item.summary or "") else item.summary
            cur_authors = cur["authors"] or authors
            if authors and not cur["authors"]:
                cur_authors = authors
            orgs = list(dict.fromkeys(cur["orgs"] + item.orgs))
            if (cur["raw"] or {}).get("provisional") and not item.raw.get("provisional"):
                # an X share created this item first; its own source now supplies the real record
                self.db.execute("""UPDATE items SET title=?, title_key=?, kind=?, url=?, published=?, raw=?, summary=?
                                   WHERE id=?""", (item.title, title_key(item.title), item.kind, item.url,
                                                   item.published or cur["published"], json.dumps(item.raw)[:20000],
                                                   item.summary or summary, existing_id))
                self.db.execute("UPDATE items_fts SET title=?, summary=? WHERE id=?",
                                (item.title, item.summary or summary, existing_id))
                summary = item.summary or summary
            self.db.execute(
                "UPDATE items SET sources=?, metrics=?, links=?, concepts=?, summary=?, authors=?, orgs=? WHERE id=?",
                (json.dumps(sources), json.dumps(metrics), json.dumps(links), json.dumps(concepts), summary,
                 json.dumps(cur_authors), json.dumps(orgs), existing_id))
            return existing_id, False
        self.db.execute(
            """INSERT INTO items(id, kind, title, title_key, summary, url, published, discovered, source, sources,
               authors, orgs, concepts, metrics, links, raw, via_entity)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item.id, item.kind, item.title, title_key(item.title), item.summary, item.url, item.published, iso(),
             item.source, json.dumps([item.source]), json.dumps(authors), json.dumps(item.orgs),
             json.dumps(item.concepts), json.dumps(item.metrics), json.dumps(item.links),
             json.dumps(item.raw)[:20000], item.via_entity))
        self.db.execute("INSERT INTO items_fts(id, title, summary, concepts) VALUES (?,?,?,?)",
                        (item.id, item.title, item.summary, " ".join(item.concepts)))
        return item.id, True

    def item(self, item_id: str) -> Optional[dict]:
        return self.one("SELECT * FROM items WHERE id=?", (item_id,))

    def update_item(self, item_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(json.dumps(v) if k in JSON_COLS else v)
        self.db.execute(f"UPDATE items SET {', '.join(cols)} WHERE id=?", (*vals, item_id))

    def set_embedding(self, item_id: str, vec: np.ndarray) -> None:
        self.db.execute("UPDATE items SET embedding=? WHERE id=?", (vec.astype(np.float32).tobytes(), item_id))

    def embeddings(self, before: Optional[str] = None, limit: int = 50000) -> tuple[list[str], list[str], np.ndarray]:
        sql = "SELECT id, published, embedding FROM items WHERE embedding IS NOT NULL"
        args: list[Any] = []
        if before:
            sql += " AND published < ?"
            args.append(before)
        sql += " ORDER BY published DESC LIMIT ?"
        args.append(limit)
        rows = self.db.execute(sql, args).fetchall()
        if not rows:
            return [], [], np.zeros((0, 1), dtype=np.float32)
        ids = [r[0] for r in rows]
        pubs = [r[1] or "" for r in rows]
        mat = np.vstack([np.frombuffer(r[2], dtype=np.float32) for r in rows])
        return ids, pubs, mat

    def search(self, query: str, limit: int = 20) -> list[dict]:
        terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self.db.execute(
            "SELECT id, bm25(items_fts) AS s FROM items_fts WHERE items_fts MATCH ? ORDER BY s LIMIT ?",
            (match, limit)).fetchall()
        return [it for r in rows if (it := self.item(r[0]))]

    # ---------- entities ----------
    def resolve_handle(self, kind: str, value: str) -> Optional[str]:
        r = self.db.execute("SELECT entity_id FROM handles WHERE kind=? AND value=?",
                            (kind, value.lower())).fetchone()
        return r[0] if r else None

    def upsert_entity(self, type_: str, name: str, handles: Optional[dict] = None, prior: float = 0.3,
                      depth: int = 0, via: str = "", status: str = "candidate", meta: Optional[dict] = None) -> tuple[str, bool]:
        handles = {k: str(v) for k, v in (handles or {}).items() if v}
        eid = None
        for k, v in handles.items():
            eid = self.resolve_handle(k, v)
            if eid:
                break
        eid = eid or entity_id(type_, name)
        cur = self.one("SELECT * FROM entities WHERE id=?", (eid,))
        if cur:
            merged = {**handles, **cur["handles"]}
            new_prior = max(cur["prior"] or 0, prior)
            rank = {"known": 0, "candidate": 1, "active": 2}
            new_status = cur["status"]
            if new_status in rank and status in rank and rank[status] > rank[new_status]:
                new_status = status
            self.db.execute("UPDATE entities SET handles=?, prior=?, depth=MIN(depth, ?), status=? WHERE id=?",
                            (json.dumps(merged), new_prior, depth, new_status, eid))
            is_new = False
        else:
            self.db.execute(
                """INSERT INTO entities(id, type, name, handles, meta, status, prior, depth, discovered_via, created)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (eid, type_, name, json.dumps(handles), json.dumps(meta or {}), status, prior, depth, via, iso()))
            is_new = True
        for k, v in handles.items():
            self.db.execute("INSERT OR IGNORE INTO handles(kind, value, entity_id) VALUES (?,?,?)", (k, v.lower(), eid))
        return eid, is_new

    def entity(self, eid: str) -> Optional[dict]:
        return self.one("SELECT * FROM entities WHERE id=?", (eid,))

    def update_entity(self, eid: str, **fields: Any) -> None:
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(json.dumps(v) if k in JSON_COLS else v)
        self.db.execute(f"UPDATE entities SET {', '.join(cols)} WHERE id=?", (*vals, eid))

    # ---------- edges ----------
    def edge(self, src: str, dst: str, rel: str, weight: float = 1.0, evidence: str = "") -> None:
        self.db.execute(
            """INSERT INTO edges(src, dst, rel, weight, created, evidence) VALUES (?,?,?,?,?,?)
               ON CONFLICT(src, dst, rel) DO UPDATE SET weight=MAX(weight, excluded.weight)""",
            (src, dst, rel, weight, iso(), evidence[:500]))

    def neighbors(self, node: str, limit: int = 200) -> list[dict]:
        return self.q(
            """SELECT src, dst, rel, weight, evidence FROM edges WHERE src=? OR dst=?
               ORDER BY weight DESC LIMIT ?""", (node, node, limit))

    def degree(self, node: str) -> int:
        r = self.db.execute("SELECT COUNT(*) FROM edges WHERE src=? OR dst=?", (node, node)).fetchone()
        return int(r[0])

    # ---------- feedback ----------
    def feedback(self, item_id: str, label: str, value: float, source: str = "user") -> None:
        self.db.execute("INSERT OR REPLACE INTO feedback(item_id, label, value, source, created) VALUES (?,?,?,?,?)",
                        (item_id, label, value, source, iso()))
