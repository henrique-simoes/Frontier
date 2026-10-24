# Frontier — Architecture

Frontier is a self-expanding research intelligence system for the AI frontier.
It reads papers, releases, repos, model drops and posts every day, scores each one for
**novelty**, follows the people and organisations behind the best items, then follows whoever
*those* people follow and cite. It writes everything into an Obsidian vault and serves a
visual web app with a chat box. Claude Code can operate it on a schedule.

This document covers how the system is built and why. [NOVELTY.md](NOVELTY.md) covers the
scoring maths in detail.

---

## 1. How a specialist team would approach it

We designed the system by asking how a small team of specialists would run it by hand, then
automating each role. Each role maps to one module.

| Role | What they do by hand | Technique we use | Module |
|---|---|---|---|
| **Information specialist / librarian** | Builds the source list, removes duplicates, assigns canonical IDs, keeps a controlled vocabulary | Canonical ID resolution (arXiv ID ↔ HF paper ↔ HN link ↔ GitHub repo), title fingerprinting, authority records for people and orgs, a faceted taxonomy (modality × area × layer) | `radar/store.py`, `radar/sources/*`, `config/taxonomy.yaml` |
| **Scout / bleeding-edge researcher** | "Who should I be reading that I'm not?" Follows authors and their co-authors, reads reference lists, checks who a good lab follows | **Snowball sampling** (backward through references, forward through citations), **focused crawling** with a best-first frontier, **social-graph expansion** (GitHub `following`, Bluesky follows), and a **multi-armed bandit** (Thompson sampling) that spends the daily budget on the most productive sources | `radar/expand.py` |
| **AI scientist** | Decides whether something is actually new: compares it with the closest prior work and spots new *combinations* of ideas | **Semantic distance** to prior work (kNN in embedding space), **combinatorial atypicality** (Uzzi et al. 2013, *Science*), **concept emergence / burst detection** (after Kleinberg 2002), with the **CD / disruption index** (Funk & Owen-Smith 2017; Park et al. 2023, *Nature*) as a retrospective label | `radar/novelty.py` |
| **Reviewer panel** | Writes a qualitative judgement: what is claimed, how rigorous it is, what kind of novelty it is, what changes if it's true | **LLM-as-judge using retrieved prior work** (the judge sees the nearest neighbours), a typed rubric with structured output, a **listwise tournament** across the day's top candidates (more reliable than absolute scores), and a mandatory skeptic note | `radar/llm.py`, `radar/novelty.py` |
| **AI engineer / MLOps** | Keeps it running, cheap and observable. Calibrates the scorer against reality | A deterministic pipeline with idempotent stages; every action logged as an event; scoring weights learned online (L2-regularised logistic regression toward the default weights) from **your ratings** and from **retrospective outcomes** (traction 30 days later) | `radar/pipeline.py`, `radar/calibrate.py` |
| **Editor / analyst** | Writes the morning brief, maintains the topic pages, draws the map | Generated notes with stable wikilinks, auto-sections that never overwrite your own text, Obsidian Bases dashboards, JSON Canvas maps, and a RAG chat that cites its sources | `radar/vault.py`, `radar/ask.py`, `radar/server/` |

Two findings from 2026 benchmarks shaped the reviewer design. **NovBench** (ACL Findings 2026)
found that LLMs understand scientific novelty only to a limited degree. **RINoBench** found that
LLM rationales *read* like an expert's but don't reliably *land* on the right answer. So the
LLM is never the only judge. It is one feature among several quantitative signals, it always
sees the retrieved prior work, and its weight is learned from outcomes rather than assumed.

---

## 2. System overview

```
                ┌──────────────────────── daily (launchd / Claude Code schedule) ───────────────────────┐
                │                                                                                        │
 SOURCES        │  1 COLLECT      2 RESOLVE       3 EMBED        4 SCORE (quant)   5 JUDGE (LLM)   6 RANK │
 arXiv (20 cats)│  sweep + per-   canonical ids,  hash-TFIDF or  semantic, combi-  rubric + prior  learned│
 HF papers/models│ entity fetch → merge sources → model2vec   →   natorial, emer- → work context → weights│
 GitHub, HN     │                 dedupe                         gence, periphery,  + tournament        │
 RSS (labs/blogs)│                                               traction                               │
 S2 / OpenAlex  │                                                                                        │
 Bluesky, X     │  7 EXPAND  ← entities from top items, refs/citations, follows; bandit-prioritised       │
                │  8 WRITE   → Obsidian vault (items, entities, topics, daily brief, canvas)              │
                │  9 LEARN   → ratings from vault/UI + 30-day retro outcomes → reweight scorer           │
                └────────────────────────────────────────────────────────────────────────────────────────┘
                                     │                                   │
                              SQLite (radar.db)                    vault/ (Markdown)
                    items · entities · edges · events ·        source of truth for *you*;
                    feedback · concepts · weights · FTS5       ratings flow back from frontmatter
                                     │
                     ┌───────────────┴──────────────────┐
              Web app (FastAPI + SPA)          Any agent (AGENTS.md + Agent Skills)
              feed · graph · entity pages ·    skills: radar-daily, -deep-dive, -ask,
              activity · chat with citations   agentic web research for what APIs miss
```

### Two stores, two jobs

- **SQLite (`data/radar.db`)** is the machine's memory. It holds the graph, the embeddings,
  the concept co-occurrence statistics, the event log and the learned weights. SQLite is a single
  file, needs no server, and includes FTS5 for full-text search. A vector index is not needed at
  this scale (about 10⁵ items): brute-force numpy kNN over 100k × 512 float32 takes about 30 ms.
  LanceDB or sqlite-vec are drop-in replacements if the corpus grows past about 10⁶.
- **The Obsidian vault (`vault/`)** is the human's memory. Every item, person, org and topic
  is a Markdown note with YAML properties and wikilinks, so Obsidian's graph view, backlinks,
  Bases and Canvas work with no plugins. The generator only rewrites content between
  `<!-- radar:auto -->` markers, so your annotations survive. When you edit `rating:` in an
  item's frontmatter, the next run turns that edit into training data.

### Why a deterministic pipeline plus an agent

The daily pipeline is plain Python. It is cheap, reproducible and testable. It covers everything an
API can reach. Claude Code covers what APIs can't reach: a lab with no RSS feed, a model card
announced only on X, a Discord changelog, or a paper's actual contribution once you read the PDF.
The `radar-daily` Agent Skill runs the pipeline and then has the agent do targeted web research on the
day's top items and gaps. Claude writes its findings back through the same CLI
(`radar add-entity`, `radar add-item`), so everything ends up in one graph.

---

## 2b. X (x.com)

X is where much of the frontier is announced first, so it gets its own collector (`radar/sources/x.py` providers,
`radar/sources/x_collect.py` collection). The design follows from three constraints:

1. **Sanctioned access only.** X's terms forbid crawling and scraping without consent. The radar uses the official
   X API v2 (app bearer token, pay-per-use) or xAI's server-side `x_search` tool, and never scrapes or touches
   account credentials.
2. **No hallucinated posts.** Grok returns structured JSON, but a post is kept only if its status URL appears in the
   response's citations (*citation-grounded extraction*).
3. **Bounded spend.** Every call is projected against per-run read caps and a daily USD cap persisted in the store.
   A call that would cross a cap is refused before it is made. With no key the collector is off.

Per run: watched X accounts (Thompson-sampled on yield like any source) → timelines (xapi) or batches of 20
handles (xai) → your configured X lists → discovery queries. Each post's arXiv/GitHub/HF links resolve to
canonical items (provisional until their own source fills in the record) with X engagement metrics and a
`shared` edge. Link-less announcements from watched accounts become `x:<id>` post items. Empty polls cost money,
so silent X accounts lose priority three times faster than free sources.

## 3. Data model

```
items(id PK, kind, title, summary, url, published, discovered, source,
      authors[], orgs[], concepts[], modalities[], areas[], metrics{}, raw{},
      embedding BLOB, quant{}, qual{}, novelty, novelty_explain{}, note_path, status)
entities(id PK, type, name, aliases[], handles{}, meta{}, status,
         prior, alpha, beta, checks, last_checked, depth, discovered_via)
edges(src, dst, rel, weight, created, evidence)      -- typed, directed multigraph
concepts(term PK, df, first_seen, last_seen)         -- for combinatorial + emergence
concept_pairs(a, b, n)                               -- co-occurrence counts
feedback(item_id, label, value, source, created)     -- 'user' | 'retro'
events(ts, run_id, kind, message, ref)               -- "what the radar did"
runs(id, started, finished, stats{})
meta(key PK, value)                                  -- learned weights, versions
```

**Canonical IDs**: `arxiv:2610.31667`, `gh:owner/repo`, `hf:org/model`, `url:<sha1>`,
`person:<slug>`, `org:<slug>`, `topic:<slug>`. Items arriving from several sources merge into one
row: an HN thread linking to an arXiv paper becomes extra traction on the paper, not a new item.

**Edge relations**: `authored`, `affiliated`, `released`, `cites`, `cited_by`, `mentions`,
`follows`, `shared` (account→item on X; evidence `x:<post id>@<time>`), `about` (item→topic), `builds_on`. Edges carry the evidence that created them.

---

## 4. The expansion engine (how the radar "grows its own references")

The aim is growth that compounds, but toward novelty rather than toward noise. This is a
**focused crawler** (Chakrabarti et al., 1999) over a heterogeneous graph:

1. **Seeds**: `config/seeds.yaml` lists frontier labs and many small and independent labs,
   researchers, infra projects and feeds.
2. **Discovery**: an item that scores above the `discover_threshold` spawns candidate entities
   at `depth = parent.depth + 1`:
   - its authors and their affiliations (Semantic Scholar or OpenAlex when available),
   - the owner of its GitHub repo or HF org,
   - **backward snowball**: its references (Semantic Scholar), stored as `reference` items whose
     authors become candidates,
   - **forward snowball**: newer papers that cite it,
   - **social graph**: who its authors follow on GitHub, Bluesky and X, and outlinks in lab blog posts
     (feed autodiscovery turns a linked homepage into a watchable feed). On X, **co-follow** accumulates:
     each productive follower raises an account's prior by `1 − (1 − prior)(1 − yield·0.45)`, so accounts many
     strong researchers follow rise fastest (a link-prediction heuristic in the spirit of Adamic–Adar),
   - **X sharers and mentions**: people who shared a frontier item early, and accounts tagged next to research
     links by watched accounts.
3. **Prior**: `prior = parent_novelty × edge_weight × γ^depth` (γ = 0.7), so a co-author of a
   0.9-novelty paper outranks someone a blog mentioned in passing.
4. **Scheduling (bandit)**: each entity is an arm with a Beta(α, β) posterior over its *yield*,
   the fraction of its items that clear the frontier threshold. Each run samples
   `θ ~ Beta(α, β)` and checks the top-`budget` entities by
   `θ × prior_boost × staleness × centrality`. Productive sources get checked more often;
   new candidates get explored because their posterior is wide; dead sources go `dormant`
   on their own.
5. **Centrality**: a weekly **personalised PageRank** from high-novelty items over the graph
   surfaces hubs that many frontier items point to, even ones never seen directly.
6. **Bounds**: `max_new_entities_per_run`, `max_active_entities` and per-API rate limits keep
   cost linear while the *reach* keeps growing.

---

## 5. Novelty in one paragraph

Each item gets a feature vector of quantitative signals: **semantic** distance from prior work,
**combinatorial** atypicality of its concept pairs, **emergence** of its concepts, **periphery**
(how far it sits from well-known nodes, which is the small-lab bonus), **traction velocity** and
**recency**. On top of these come the LLM judge's **novelty / rigor / impact** scores and a typed
**novelty class** (`new_paradigm`, `new_capability`, `new_architecture`, `new_method`,
`new_system_or_infra`, `efficiency_step_change`, `new_data_or_benchmark`, `empirical_finding`,
`incremental`, `survey_or_position`, `hype_or_unclear`). A logistic model combines them into a
0–100 **Frontier score** and keeps a per-signal **explanation**. The weights start as expert
priors and are re-fitted from your ratings and from what actually took off 30 days later. Full
details are in [NOVELTY.md](NOVELTY.md).

---

## 6. Interaction design

The system should feel like an analyst who has already done the reading when you sit down.

- **Ambient, no request needed**: the web app opens on *Today*, cards ranked by Frontier score.
  Each card shows a signal breakdown bar, the novelty class, and a one-line summary of what's new.
  The *Activity* stream shows what the radar did and why ("followed `@lab` because 3 of its
  authors wrote 0.8+ items"). The daily brief also lands in `vault/Daily/` and as a macOS
  notification.
- **Conversation**: an "Ask the radar…" bar (⌘K) is on every screen. Answers are grounded in the
  store and cite items and entities as clickable chips that open the detail drawer. The same
  works in Claude Code (`/ask`) and in Obsidian, since everything is linked notes.
- **Relationships**: the *Graph* view (Cytoscape) centres on any item or entity, expands
  neighbours on click, and colours nodes by novelty. The detail drawer lists every relation with
  its evidence. Each item has an `obsidian://` deep link.
- **Training with no extra steps**: a single key (`1` frontier, `2` solid, `3` incremental,
  `4` hype) rates the focused card. The same field exists in Obsidian frontmatter. Ratings
  re-weight the scorer at the next calibration.

---

## 7. Operations

| Concern | Choice |
|---|---|
| Schedule | `launchd` job at 06:30 for `radar run`, and a `launchd` KeepAlive for `radar serve` (port 7777). Optionally a Claude Code scheduled task for `/radar-daily` |
| Cost | Quantitative scoring is free. The LLM judges only the top-K per run (default 40) at `effort: low`, and the tournament covers the top 15. That is roughly $1–2/day on Claude Opus 5.5. Models are configurable in `config/radar.yaml` |
| LLM providers | Per role (`llm.judge`, `llm.ask`): `anthropic` (native SDK), any OpenAI-compatible endpoint (OpenAI, Gemini, OpenRouter, local llama.cpp/vLLM/Ollama), `claude-cli`, `none`, with a fallback chain. No gateway library (supply-chain surface) |
| Personal settings | `config/local.yaml` and `config/*.local.yaml` deep-merge over the committed defaults; keys in `.env`; all gitignored |
| Politeness | Per-host rate limits (arXiv 1 request / 3 s), an on-disk HTTP cache, and an identifying User-Agent. OpenAlex needs a free key since Feb 2026; Semantic Scholar is much more reliable with a key |
| Failure isolation | Each source and stage is wrapped; a failure is logged as an event and the run continues |
| Reproducibility | `runs` table with stats; weights versioned in `meta`; vault is plain Markdown under git |

## 8. Roadmap

- OpenReview (ICLR/NeurIPS submissions) and bioRxiv connectors.
- Full-text PDF reading for the top 5 items per day (method/claim extraction).
- A retrospective disruption (CD) index once citation data matures, used as a slow label.
- An MCP server that exposes `search`, `item`, `graph` and `ask` to Claude Desktop.
- Multi-user ratings (team calibration).
