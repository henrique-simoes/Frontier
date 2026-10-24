"""Concept extraction against the controlled vocabulary plus mined n-grams."""
from __future__ import annotations

import re
from functools import lru_cache

from .config import config

STOP = set("""a an the of for and or in on with to from by via at as is are be we our its it this that these those
towards toward using use based new novel learning model models approach method methods framework study
large small efficient improving improved via beyond through into over under without what when how why can
data task tasks system systems paper results analysis toward scalable simple general robust fast
day support open source first one two release released launch launches introducing show ask hn tell
your you my i how why not just more less vs versus""".split())


@lru_cache(maxsize=1)
def _vocab() -> list[tuple[str, str, list]]:
    out = []
    for facet, groups in (config().taxonomy or {}).items():
        for name, patterns in (groups or {}).items():
            # word-start match: "vit" must not fire inside "activity"; prefixes like "quantiz" still work
            compiled = [re.compile(p[3:] if p.startswith("re:") else r"\b" + re.escape(p.lower()), re.I)
                        for p in patterns]
            out.append((facet, name, compiled))
    return out


def facets(text: str) -> dict[str, list[str]]:
    """Return {"modality": [...], "area": [...], "layer": [...]} matched in text."""
    t = text.lower()
    res: dict[str, list[str]] = {}
    for facet, name, pats in _vocab():
        for p in pats:
            if p.search(t):
                res.setdefault(facet, []).append(name)
                break
    return res


def mine_terms(title: str, keywords: list[str] | None = None) -> list[str]:
    """Salient terms: provided keywords + capitalised/hyphenated tokens + title bigrams."""
    terms: list[str] = []
    for k in keywords or []:
        k = k.strip().lower()
        if 2 < len(k) < 48:
            terms.append(k)
    # Acronyms / CamelCase / hyphenated technical tokens from title, e.g. "GRPO", "FlashAttention", "test-time"
    for tok in re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b|\b[A-Z][a-z]+[A-Z]\w*\b|\b[A-Z]{2,}[0-9]*\b", title):
        if tok.lower() not in STOP and len(tok) > 2:
            terms.append(tok.lower())
    words = [w for w in re.findall(r"[a-z][a-z0-9]+", title.lower())]
    for a, b in zip(words, words[1:]):
        if a not in STOP and b not in STOP and len(a) > 2 and len(b) > 2 and not (a.isdigit() or b.isdigit()):
            terms.append(f"{a} {b}")
    return list(dict.fromkeys(terms))[:16]


def extract(title: str, summary: str = "", keywords: list[str] | None = None) -> tuple[list[str], dict[str, list[str]]]:
    f = facets(f"{title}. {summary}")
    concepts = [f"{facet}/{v}" for facet, vs in f.items() for v in vs] + mine_terms(title, keywords)
    return list(dict.fromkeys(concepts)), f
