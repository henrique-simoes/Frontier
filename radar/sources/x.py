"""X (x.com) as a radar source, using sanctioned access only (see docs/build-stream/2026-10-24-x-source.md, DEC-2).

Providers (config x.provider):
  xapi - official X API v2, app bearer token (X_BEARER_TOKEN), pay-per-use
  xai  - xAI Responses API with the server-side x_search tool (XAI_API_KEY); posts are kept only when
         their status URL is among the response's citations (citation-grounded extraction)
  none - disabled
  auto - xapi if X_BEARER_TOKEN, else xai if XAI_API_KEY, else none

Every billable call is projected against per-run read caps and a daily USD cap before it is made.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx

from .. import http
from ..config import config
from ..util import iso

XAPI = "https://api.x.com/2"
XAI = "https://api.x.ai/v1/responses"
STATUS_RE = re.compile(r"(?:x|twitter)\.com/(?:[A-Za-z0-9_]+|i)/status(?:es)?/(\d+)", re.I)
X_LINK_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/", re.I)
TWEET_FIELDS = "created_at,public_metrics,entities,author_id,referenced_tweets,lang"
USER_FIELDS = "name,username,description,public_metrics,verified"
AI_HINT = re.compile(r"\b(model|paper|arxiv|weights|benchmark|agent|llm|training|inference|dataset|release|"
                     r"open[- ]source|reasoning|robot|diffusion|transformer|gpu|kernel|eval)", re.I)


class BudgetExceeded(Exception):
    pass


# ---------------------------------------------------------------- configuration

def xcfg(key: str, default: Any = None) -> Any:
    return config().get(f"x.{key}", default)


def provider() -> str:
    p = xcfg("provider", "auto")
    if p == "auto":
        if os.environ.get("X_BEARER_TOKEN"):
            return "xapi"
        if os.environ.get("XAI_API_KEY"):
            return "xai"
        return "none"
    if p == "xapi" and not os.environ.get("X_BEARER_TOKEN"):
        return "none"
    if p == "xai" and not os.environ.get("XAI_API_KEY"):
        return "none"
    return p if p in ("xapi", "xai") else "none"


def price(key: str) -> float:
    defaults = {"post_read": 0.005, "user_read": 0.010, "xai_post": 0.005, "xai_user": 0.010,
                "xai_usd_per_m_input": 3.0, "xai_usd_per_m_output": 15.0}
    return float((xcfg("pricing") or {}).get(key, defaults[key]))


class Budget:
    """Per-run read caps plus a daily USD cap persisted in meta.x_spend ({"YYYY-MM-DD": usd})."""

    def __init__(self, store) -> None:
        self.store = store
        self.posts = 0
        self.users = 0
        self.usd = 0.0

    def _today(self) -> float:
        return float((self.store.get_meta("x_spend") or {}).get(iso()[:10], 0.0))

    def check(self, posts: int = 0, users: int = 0, usd: float = 0.0) -> None:
        """Refuse BEFORE a call whose projected cost would cross any cap."""
        if self.posts + posts > int(xcfg("max_post_reads_per_run", 1000)):
            raise BudgetExceeded(f"post-read cap ({xcfg('max_post_reads_per_run', 1000)}/run)")
        if self.users + users > int(xcfg("max_user_reads_per_run", 300)):
            raise BudgetExceeded(f"user-read cap ({xcfg('max_user_reads_per_run', 300)}/run)")
        cap = float(xcfg("daily_usd_cap", 8.0))
        if self._today() + usd > cap:
            raise BudgetExceeded(f"daily spend cap (${cap:.2f}; spent ${self._today():.2f} today)")

    def charge(self, posts: int = 0, users: int = 0, usd: float = 0.0) -> None:
        self.posts += posts
        self.users += users
        self.usd += usd
        spend = self.store.get_meta("x_spend") or {}
        day = iso()[:10]
        spend[day] = round(float(spend.get(day, 0.0)) + usd, 4)
        self.store.set_meta("x_spend", dict(sorted(spend.items())[-60:]))


# ---------------------------------------------------------------- transport

def _request(method: str, url: str, *, params: Optional[dict] = None, body: Optional[dict] = None,
             headers: Optional[dict] = None, timeout: float = 120) -> dict:
    """Single choke point for every X/xAI request (tests replace it). No caching: calls are billed."""
    http._wait(httpx.URL(url).host)
    r = http._http().request(method, url, params=params, json=body, headers=headers, timeout=timeout,
                             follow_redirects=False)   # bearer tokens never travel through a redirect
    if r.status_code == 429:
        raise httpx.HTTPStatusError("429 rate limited by X", request=r.request, response=r)
    r.raise_for_status()
    return r.json()


def _xapi(path: str, params: dict) -> dict:
    return _request("GET", f"{XAPI}{path}", params=params,
                    headers={"Authorization": f"Bearer {os.environ.get('X_BEARER_TOKEN', '')}"})


# ---------------------------------------------------------------- normalisation

def _post(tid: str, handle: str, name: str, text: str, created_at: Optional[str], links: list[str],
          metrics: dict, mentions: list[str], is_repost: bool = False) -> dict:
    return {"id": str(tid), "url": f"https://x.com/{handle}/status/{tid}", "handle": handle, "name": name or handle,
            "created_at": created_at, "text": text or "", "links": [l for l in links if l and not X_LINK_RE.match(l)],
            "metrics": metrics, "mentions": [m.lstrip("@") for m in mentions if m], "is_repost": is_repost}


def _from_xapi(data: dict) -> list[dict]:
    users = {u["id"]: u for u in (data.get("includes") or {}).get("users", [])}
    out = []
    for t in data.get("data") or []:
        u = users.get(t.get("author_id"), {})
        ents = t.get("entities") or {}
        links = [x.get("unwound_url") or x.get("expanded_url") for x in ents.get("urls", [])]
        pm = t.get("public_metrics") or {}
        refs = {r.get("type") for r in t.get("referenced_tweets") or []}
        out.append(_post(t["id"], u.get("username", "i"), u.get("name", ""), t.get("text", ""), t.get("created_at"),
                         links, {"likes": pm.get("like_count", 0), "reposts": pm.get("retweet_count", 0),
                                 "quotes": pm.get("quote_count", 0), "bookmarks": pm.get("bookmark_count", 0),
                                 "replies": pm.get("reply_count", 0), "impressions": pm.get("impression_count", 0)},
                         [m.get("username", "") for m in ents.get("mentions", [])], "retweeted" in refs))
    return out


def _cited_status_ids(resp: dict) -> set[str]:
    urls = list(resp.get("citations") or [])
    for item in resp.get("output") or []:
        if not isinstance(item, dict):
            continue
        for c in item.get("content") or []:
            for a in c.get("annotations") or []:
                if a.get("url"):
                    urls.append(a["url"])
    return {m.group(1) for u in urls if isinstance(u, str) and (m := STATUS_RE.search(u))}


def _output_text(resp: dict) -> str:
    parts = []
    for item in resp.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text"):
                    parts.append(c.get("text", ""))
    return "\n".join(parts)


def _json_array(text: str) -> list:
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S) or re.search(r"(\[\s*\{.*\}\s*\])", text, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def _from_xai(resp: dict) -> tuple[list[dict], int]:
    """Parse Grok's JSON; keep only posts whose status id was actually cited. Returns (posts, dropped)."""
    cited = _cited_status_ids(resp)
    posts, dropped = [], 0
    for p in _json_array(_output_text(resp)):
        if not isinstance(p, dict):
            continue
        m = STATUS_RE.search(str(p.get("url", ""))) if p.get("url") else None
        tid = str(p.get("id") or (m.group(1) if m else ""))
        if not tid or tid not in cited:
            dropped += 1
            continue
        handle = str(p.get("author_handle") or p.get("handle") or "i").lstrip("@")
        metrics = {k: int(p.get(k) or 0) for k in ("likes", "reposts", "quotes", "replies") if str(p.get(k) or "0").isdigit()}
        posts.append(_post(tid, handle, str(p.get("author_name") or handle), str(p.get("text") or ""),
                           p.get("created_at"), [str(l) for l in p.get("links") or []], metrics,
                           [str(x) for x in p.get("mentions") or []], bool(p.get("is_repost"))))
    return posts, dropped


# ---------------------------------------------------------------- providers

class XApi:
    """Official X API v2. Costs are charged per resource returned."""

    def __init__(self, budget: Budget) -> None:
        self.b = budget

    def _charged(self, path: str, params: dict, max_posts: int = 0, max_users: int = 0) -> dict:
        self.b.check(max_posts, max_users, max_posts * price("post_read") + max_users * price("user_read"))
        data = _xapi(path, params)
        n_posts = len(data.get("data") or []) if max_posts else 0
        n_users = len((data.get("includes") or {}).get("users", [])) if max_posts else len(data.get("data") or [])
        self.b.charge(n_posts, n_users, n_posts * price("post_read") + n_users * price("user_read"))
        return data

    def lookup(self, handles: list[str]) -> list[dict]:
        out = []
        for k in range(0, len(handles), 100):
            chunk = handles[k:k + 100]
            data = self._charged("/users/by", {"usernames": ",".join(chunk), "user.fields": USER_FIELDS},
                                 max_users=len(chunk))
            out += data.get("data") or []
        return out

    def user_posts(self, user_id: str, since: str, limit: int = 20) -> list[dict]:
        return _from_xapi(self._charged(f"/users/{user_id}/tweets", {
            "max_results": max(5, min(limit, 100)), "start_time": since, "exclude": "replies",
            "tweet.fields": TWEET_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS}, max_posts=limit, max_users=1))

    def list_posts(self, list_id: str, limit: int = 100) -> list[dict]:
        return _from_xapi(self._charged(f"/lists/{list_id}/tweets", {
            "max_results": max(1, min(limit, 100)), "tweet.fields": TWEET_FIELDS, "expansions": "author_id",
            "user.fields": USER_FIELDS}, max_posts=limit, max_users=limit))

    def search(self, query: str, since: str, limit: int = 100) -> list[dict]:
        return _from_xapi(self._charged("/tweets/search/recent", {
            "query": query, "max_results": max(10, min(limit, 100)), "start_time": since,
            "tweet.fields": TWEET_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS},
            max_posts=limit, max_users=limit))

    def following(self, user_id: str, limit: int = 200) -> list[dict]:
        data = self._charged(f"/users/{user_id}/following", {"max_results": max(1, min(limit, 1000)),
                                                             "user.fields": USER_FIELDS}, max_users=limit)
        return data.get("data") or []


XAI_SYSTEM = """You are the X scout of a frontier-AI research radar. Use the x_search tool to find posts, then
reply with ONLY a JSON array in a ```json fenced block. One object per relevant post:
{"id": "<post id>", "url": "https://x.com/<handle>/status/<id>", "author_handle": "...", "author_name": "...",
 "created_at": "<ISO 8601>", "text": "<full post text>", "links": ["<every external URL in the post, expanded>"],
 "mentions": ["<handles mentioned>"], "likes": <int or null>, "reposts": <int or null>, "is_repost": <bool>}
Relevant = announces or discusses new AI research, papers, models, weights, datasets, benchmarks, results, tools,
infrastructure or hardware. Skip memes, politics, ads and generic opinion. Never invent posts: every post must
come from a search result you actually saw."""


class XAi:
    """xAI Responses API + x_search. Billed per post/profile fetched plus tokens."""

    def __init__(self, budget: Budget) -> None:
        self.b = budget
        self.dropped = 0   # uncited posts discarded across all calls

    def _ask(self, prompt: str, tool: dict) -> list[dict]:
        turns = int(xcfg("xai_max_turns", 3))
        projected = turns * 25 * price("xai_post") + 0.03   # ~25 posts per search turn + a token allowance
        self.b.check(turns * 25, 0, projected)
        resp = _request("POST", XAI, body={
            "model": xcfg("xai_model", "grok-4.7"), "max_turns": turns, "tools": [tool],
            "input": [{"role": "system", "content": XAI_SYSTEM}, {"role": "user", "content": prompt}]},
            headers={"Authorization": f"Bearer {os.environ.get('XAI_API_KEY', '')}"}, timeout=300)
        usage = resp.get("usage") or {}
        det = usage.get("server_side_tool_usage_details") or {}
        n_posts, n_users = int(det.get("x_posts_fetched", 0) or 0), int(det.get("x_users_fetched", 0) or 0)
        usd = (n_posts * price("xai_post") + n_users * price("xai_user")
               + usage.get("input_tokens", 0) / 1e6 * price("xai_usd_per_m_input")
               + usage.get("output_tokens", 0) / 1e6 * price("xai_usd_per_m_output"))
        self.b.charge(n_posts, n_users, usd)
        posts, dropped = _from_xai(resp)
        self.dropped += dropped
        return posts

    def accounts(self, handles: list[str], since: str) -> list[dict]:
        out: list[dict] = []
        for k in range(0, len(handles), 20):   # x_search allows at most 20 handles per call
            batch = handles[k:k + 20]
            out += self._ask(f"List every relevant post by these accounts since {since[:10]}: "
                             f"{', '.join('@' + h for h in batch)}.",
                             {"type": "x_search", "allowed_x_handles": batch, "from_date": since[:10]})
        return out

    def search(self, query: str, since: str) -> list[dict]:
        return self._ask(f"{query} Only posts since {since[:10]}.", {"type": "x_search", "from_date": since[:10]})
