"""Polite HTTP: per-host rate limits, on-disk cache, retries, identifying User-Agent."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import config
from .util import sha1

USER_AGENT = "FrontierRadar/0.1 (personal research radar; +https://github.com/)"

# Minimum seconds between requests per host.
HOST_INTERVAL = {
    "export.arxiv.org": 3.1,
    "api.semanticscholar.org": 1.1,
    "api.github.com": 1.0,
    "api.openalex.org": 0.2,
    "hn.algolia.com": 0.3,
}
DEFAULT_INTERVAL = 0.5

_last: dict[str, float] = {}
_lock = threading.Lock()
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    return _client


def _wait(host: str) -> None:
    interval = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
    with _lock:
        delta = time.monotonic() - _last.get(host, 0)
        if delta < interval:
            time.sleep(interval - delta)
        _last[host] = time.monotonic()


def _auth_headers(host: str) -> dict[str, str]:
    h: dict[str, str] = {}
    if host == "api.github.com" and os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    if host == "api.semanticscholar.org" and os.environ.get("S2_API_KEY"):
        h["x-api-key"] = os.environ["S2_API_KEY"]
    if host == "huggingface.co" and os.environ.get("HF_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    return h


def get(url: str, params: dict | None = None, ttl: float = 6 * 3600, as_json: bool = True,
        headers: dict | None = None, retries: int = 3) -> Any:
    """GET with cache. Returns parsed JSON (or text). Raises httpx.HTTPError on final failure."""
    host = urlparse(url).netloc
    if host == "api.openalex.org" and os.environ.get("OPENALEX_API_KEY"):
        params = {**(params or {}), "api_key": os.environ["OPENALEX_API_KEY"]}
    key = sha1(url + json.dumps(params or {}, sort_keys=True))
    cache_dir: Path = config().path("cache")
    cache_file = cache_dir / key[:2] / key
    if ttl > 0 and cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
        text = cache_file.read_text(encoding="utf-8")
        return json.loads(text) if as_json else text

    if host == "api.semanticscholar.org" and not os.environ.get("S2_API_KEY"):
        retries = min(retries, 2)   # the shared keyless pool 429s often; don't stall the run on it
    last_exc: Exception | None = None
    for attempt in range(retries):
        _wait(host)
        try:
            r = _http().get(url, params=params, headers={**_auth_headers(host), **(headers or {})})
            if r.status_code in (429, 500, 502, 503, 504):
                retry_after = float(r.headers.get("retry-after", 0) or 0)
                time.sleep(max(retry_after, 2 ** attempt * 2))
                last_exc = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                continue
            r.raise_for_status()
            text = r.text
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(text, encoding="utf-8")
            return r.json() if as_json else text
        except (httpx.TransportError, json.JSONDecodeError) as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
    assert last_exc is not None
    raise last_exc
