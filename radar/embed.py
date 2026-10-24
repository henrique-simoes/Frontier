"""Embeddings. Default: dependency-free hashed TF-IDF (unigrams+bigrams). Optional: model2vec static embeddings."""
from __future__ import annotations

import math
import re
import zlib
from functools import lru_cache

import numpy as np

from .config import config

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]+")
STOP = set("the of and a an to in for on with by is are we our this that from as at be via using use based can".split())


def _tokens(text: str) -> list[str]:
    toks = [t for t in TOKEN_RE.findall(text.lower()) if t not in STOP and len(t) > 1]
    return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]


def _hash_embed(texts: list[str], dims: int) -> np.ndarray:
    out = np.zeros((len(texts), dims), dtype=np.float32)
    for i, t in enumerate(texts):
        counts: dict[str, int] = {}
        for tok in _tokens(t):
            counts[tok] = counts.get(tok, 0) + 1
        for tok, c in counts.items():
            h = zlib.crc32(tok.encode())
            sign = 1.0 if (h >> 31) & 1 else -1.0
            # bigrams carry more specificity than unigrams
            w = (1 + math.log(c)) * (1.6 if "_" in tok else 1.0)
            out[i, h % dims] += sign * w
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(norms, 1e-9)


@lru_cache(maxsize=1)
def _m2v():
    from model2vec import StaticModel  # type: ignore
    return StaticModel.from_pretrained(config().get("embedding.model2vec_model"))


def embed(texts: list[str]) -> np.ndarray:
    backend = config().get("embedding.backend", "hash")
    if backend == "model2vec":
        try:
            v = np.asarray(_m2v().encode(texts), dtype=np.float32)
            return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
        except ImportError:
            pass
    return _hash_embed(texts, int(config().get("embedding.dims", 1024)))


def item_text(item: dict) -> str:
    return f"{item.get('title', '')}. {item.get('title', '')}. {(item.get('summary') or '')[:2000]}"
