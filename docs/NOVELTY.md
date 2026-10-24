# Novelty scoring

The radar keeps two kinds of novelty separate and only combines them at the last step:

- **Quantitative**: signals measured from the corpus itself. They are cheap, consistent and
  computed for every item.
- **Qualitative**: a typed judgement from an LLM reviewer that sees the closest prior work. It is
  expensive, so only the top-K candidates get it.

Each signal is normalised to `[0, 1]` and kept separately, so every score can be explained.

## Quantitative signals

| Signal | Definition | Intuition | Reference |
|---|---|---|---|
| `semantic` | `1 − mean(top-k cos(e_i, e_j))` over items *published before* `i` (k = 5), then converted to a percentile against the last 30 days | Far from everything seen before | kNN outlier scoring; "semantic distance" novelty (Shibayama et al. 2021) |
| `combinatorial` | For each concept pair (a, b): `atyp = log((E[n_ab]+1)/(n_ab+1))`, where `E[n_ab] = df_a·df_b/N`. Item score = sigmoid of the 90th percentile pair atypicality | Common ideas combined in an unusual way | Uzzi, Mukherjee, Stringer & Jones 2013 (*Science*); Wang, Veugelers & Stephan 2017 |
| `emergence` | For concepts with df ≥ 2: share of occurrences in the last 14 days, weighted by first-seen recency. Item score = max over its concepts | Early in a new wave | Kleinberg 2002 burst detection (lightweight variant) |
| `periphery` | `1 − percentile(max degree of the item's authors/orgs)` | Small labs and new voices (the *beyond the big labs* requirement) | Core–periphery structure in science-of-science |
| `traction` | `log1p(stars/day + upvotes + HN points/10 + likes/day)` → percentile | People are picking it up quickly | Altmetrics velocity |
| `recency` | `exp(−age_days / 4)` | The newer the better | — |
| `attention` | `1 − exp(−Σ_sharers yield·(0.5 + centrality))` over distinct X accounts with a `shared` edge, ×1.5 if any share came within 48h of publication; 0 when nobody the radar knows shared it | Strong researchers noticed it early | Expert-curation and early-adopter signals (cf. altmetrics; "who shared it" > "how many") |

Concepts come from a configurable taxonomy (`config/taxonomy.yaml`), HF `ai_keywords`, arXiv
categories and salient title n-grams.

## Qualitative judgement (LLM reviewer)

The prompt includes the item **and its 5 nearest prior items** from the store
(retrieval-augmented novelty assessment). The reviewer must return strict JSON:

```json
{
  "novelty_class": "new_method",
  "novelty": 7, "rigor": 6, "impact": 8,
  "what_is_new": "…one or two sentences, relative to the neighbours shown…",
  "closest_prior": ["arxiv:…"],
  "claims": ["…"],
  "modalities": ["text", "vision"], "areas": ["reasoning"], "layer": "model|harness|infra|data|hardware|science",
  "skeptic": "…the strongest reason this might not matter…",
  "follow": [{"name": "…", "type": "person|org|repo|feed", "why": "…"}],
  "confidence": 0.6
}
```

**Tournament**: the day's top 15 go to one listwise ranking call ("rank by how much each would
change what a frontier practitioner believes or can do"). The rank becomes the `tournament`
feature. Listwise and pairwise comparisons are more stable than absolute 1–10 scores.

## Combining signals: the Frontier score

```
x  = [semantic, combinatorial, emergence, periphery, traction, recency, attention,
      llm_novelty/10, llm_rigor/10, llm_impact/10, tournament, class_prior]
p  = σ(w·x + b)
score = round(100·p)
explain_k = w_k·x_k   (shown as the stacked bar in the UI)
```

Items with no LLM judgement use only the quantitative features: the LLM features are imputed
with their running means and flagged `judged: false`.

`class_prior` encodes expert priors per novelty class (for example `new_paradigm` 1.0,
`incremental` 0.15, `hype_or_unclear` 0.05).

## Learning what "frontier" means

Labels come from two sources:

1. **You**: `frontier=1.0`, `solid=0.6`, `incremental=0.2`, `hype=0.0`, `irrelevant=0.0`, set
   in the web app (keys 1–4) or in the note's `rating:` property.
2. **Reality (retrospective)**: 30 days after discovery, items in the top decile of growth in
   citations, stars or upvotes get a soft positive (0.8), and the bottom half get a soft
   negative (0.2). Retrospective labels carry weight 0.3; your labels carry weight 1.0.

`radar calibrate` fits the weights by gradient descent on weighted binary cross-entropy with an
L2 penalty **toward the prior weights** (not toward zero), so a handful of labels nudges the model
without wrecking it. Each fit is versioned in `meta.weights_history` and reports the
before/after log-loss on held-out labels. A fit that makes held-out loss worse is rejected.
