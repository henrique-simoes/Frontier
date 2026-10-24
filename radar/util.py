from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|arxiv:)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)
HF_PAPER_RE = re.compile(r"huggingface\.co/papers/(\d{4}\.\d{4,5})", re.I)
HF_MODEL_RE = re.compile(r"huggingface\.co/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?(?:$|[?#]|tree/|blob/)", re.I)
X_STATUS_RE = re.compile(r"(?:^|//|\.)(?:x|twitter)\.com/[A-Za-z0-9_]+/status(?:es)?/(\d+)", re.I)
HF_RESERVED = {"papers", "datasets", "spaces", "blog", "docs", "learn", "models", "collections", "organizations",
               "settings", "join", "login", "pricing", "posts", "api", "tasks", "chat"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    for fmt in (None, "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
        try:
            dt = datetime.fromisoformat(v) if fmt is None else datetime.strptime(value.strip(), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def age_days(published: str | None) -> float:
    dt = parse_dt(published)
    if not dt:
        return 30.0
    return max(0.0, (now() - dt).total_seconds() / 86400)


def slugify(text: str, maxlen: int = 80) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s_]+", "-", text)[:maxlen].strip("-") or "untitled"


def note_title(text: str, maxlen: int = 120) -> str:
    """A filename-safe title for Obsidian (keeps case and spaces)."""
    text = re.sub(r'[\\/:*?"<>|#^\[\]]', " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:maxlen].rstrip(" .") or "Untitled"


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def normalize_url(url: str) -> str:
    p = urlparse(url.strip())
    netloc = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/")
    return urlunparse((p.scheme or "https", netloc, path, "", "", ""))


def canonical_id(url: str) -> str:
    """Map any URL to the radar's canonical item id."""
    if m := ARXIV_RE.search(url) or HF_PAPER_RE.search(url):
        return f"arxiv:{m.group(1)}"
    if m := X_STATUS_RE.search(url):
        return f"x:{m.group(1)}"
    if (m := HF_MODEL_RE.search(url)) and m.group(1).lower() not in HF_RESERVED:
        return f"hf:{m.group(1)}/{m.group(2)}".lower()
    if m := GITHUB_RE.search(url):
        repo = m.group(2).removesuffix(".git")
        if m.group(1).lower() not in {"orgs", "topics", "features", "sponsors"}:
            return f"gh:{m.group(1)}/{repo}".lower()
    return f"url:{sha1(normalize_url(url))[:16]}"


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())[:64]


def strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return re.sub(r"\s+", " ", text).strip()


def clamp01(x: float) -> float:
    return 0.0 if x != x else max(0.0, min(1.0, x))
