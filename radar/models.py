from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Author:
    name: str
    ids: dict[str, str] = field(default_factory=dict)        # {"s2": "...", "github": "...", "hf": "..."}
    affiliations: list[str] = field(default_factory=list)


@dataclass
class Item:
    """One thing the radar saw: a paper, model release, repo, post or announcement."""
    id: str
    kind: str                      # paper | model | repo | post | release | reference
    title: str
    url: str
    source: str                    # which connector found it
    summary: str = ""
    published: Optional[str] = None
    authors: list[Author] = field(default_factory=list)
    orgs: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)  # stars, upvotes, hn_points, likes, citations
    links: list[str] = field(default_factory=list)           # outbound URLs (repo, project page, refs)
    raw: dict[str, Any] = field(default_factory=dict)
    via_entity: Optional[str] = None                         # entity id that led us here


@dataclass
class Discovery:
    """A candidate entity proposed by expansion."""
    type: str                      # person | org | repo | feed | topic
    name: str
    handles: dict[str, str] = field(default_factory=dict)
    prior: float = 0.3
    via: str = ""                  # item/entity id that led here
    rel: str = "mentions"
    evidence: str = ""
