"""Connectors. Each sweep function yields Items; each entity fetcher takes an entity dict.

Sweeps cover the whole field every run (arXiv categories, HF daily papers, trending models,
GitHub search, HN). Entity fetchers are what the expansion engine schedules.
"""
from . import arxiv, bluesky, github, hf, hn, rss, s2  # noqa: F401

SWEEPS = {
    "arxiv": arxiv.sweep,
    "hf_papers": hf.sweep_daily_papers,
    "hf_models": hf.sweep_trending_models,
    "github": github.sweep,
    "hn": hn.sweep,
}

# handle kind -> fetcher(entity, handle_value) -> list[Item]
ENTITY_FETCHERS = {
    "feed": rss.fetch_feed,
    "homepage": rss.fetch_homepage,
    "github": github.fetch_owner,
    "hf": hf.fetch_org,
    "arxiv_author": arxiv.fetch_author,
    "s2": s2.fetch_author,
    "bluesky": bluesky.fetch_author_feed,
}
