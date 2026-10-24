# Frontier Radar

A research radar for the AI frontier that grows on its own. Every day it reads arXiv (17 AI categories plus 12
adjacent science/tech ones), Hugging Face daily papers and trending new models, new GitHub repos, Hacker News,
lab and researcher blogs, Bluesky, and **X** (researchers' and labs' posts, via the official X API or xAI's
`x_search`). It scores everything for **novelty**, quantitatively and qualitatively.
Then it **follows the people, labs, references and citations behind the best items**, and whoever *they* follow.
Everything lands in an **Obsidian vault** and a **visual web app with chat**. Any AI coding agent can operate it on top (AGENTS.md + Agent Skills).

```
sources ─► resolve/dedupe ─► embed ─► quantitative novelty ─► Claude review (sees prior work) ─► tournament
                                                                                                    │
     Obsidian vault ◄── write ◄── learned Frontier score ◄── your ratings + 30-day outcomes ◄───────┘
          ▲                                  │
     web app + chat                 expansion engine (snowball refs/citations, authors, orgs,
                                    GitHub/Bluesky follows; Thompson-sampling scheduler)
```

- **Architecture and the specialist-team design:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Novelty maths:** [docs/NOVELTY.md](docs/NOVELTY.md)
- **How AI agents operate it:** [AGENTS.md](AGENTS.md) and `.agents/skills/`

## Quick start

```bash
scripts/setup.sh                       # venv + deps, then the key wizard (radar setup) and an optional first run
.venv/bin/python -m radar serve        # http://127.0.0.1:7777
```

`radar setup` walks through each key: what it unlocks, where to get it, and what it costs. Input is hidden, and
each key is checked against the provider's free endpoint. The X API check costs about $0.01, so it runs only if
you say yes. Keys are saved to `.env` (gitignored, permissions 0600), which every `radar` command and the web app
load automatically; real environment variables always win. All keys are optional.

| Command | What it does |
|---|---|
| `radar setup` | Full wizard, then offers the first run and the launchd schedule |
| `radar setup --check` | Status and validation of every key, no prompts (`--paid` also tests the X API key) |
| `radar setup --only XAI_API_KEY` | Set or replace one key |

Open `vault/` as a vault in Obsidian. Start at `Home.md`.

**Run it autonomously** (macOS): `scripts/install_launchd.sh` installs a daily 06:30 run and an always-on web app.
For the agentic layer as well, schedule the `radar-daily` skill in your agent harness (e.g. a Claude Code scheduled task). It runs the pipeline,
reads the top items' primary sources, hunts the web for what APIs miss, and grows the watchlist.

## Using it

| Where | What |
|---|---|
| **Web app** | *Today* feed ranked by Frontier score, with a signal breakdown bar and "what's new" per card · **⌘K** chat with clickable citations · *Graph* (click a node to open it, double-click to expand its neighbourhood) · *Watchlist* (who the radar follows, with yield and centrality) · *Activity* (everything it did, and why) · *Brief* |
| **Keys** | `j`/`k` move · `↵` open · `1` frontier · `2` solid · `3` incremental · `4` hype |
| **Obsidian** | `Daily/` briefs · `Items/` · `Entities/People`, `Entities/Orgs` · `Topics/` (emerging concepts) · `Maps/*.canvas` · `Dashboards/*.base` · set `rating:` on any item to train the scorer |
| **Your agent** (Claude Code, Codex, Cursor, …) | skills `radar-daily`, `radar-deep-dive`, `radar-ask`, `radar-expand`, `radar-calibrate` |
| **CLI** | `radar x-status`, `radar top`, `radar ask "…"`, `radar item <id>`, `radar rate <id> frontier`, `radar add-entity …`, `radar activity`, `radar calibrate`, `radar stats` |

## Any model: local, Claude, GPT, Gemini, OpenRouter
Two roles use an LLM: **judge** (per-item review and the daily tournament; structured JSON, high volume) and **ask**
(chat). Each can use a different provider. `radar setup --llm` lists what's available (including the models each
endpoint actually serves) and saves your choice. `radar llm-test --role judge` makes one real call.

| Provider | How | Notes |
|---|---|---|
| `anthropic` (default) | Anthropic SDK, `ANTHROPIC_API_KEY` or `ant auth login` | Claude Opus 5.5 with effort control and server-side refusal fallbacks. Judging 40 items/day ≈ $1–2 |
| `openai`, `gemini`, `openrouter` | OpenAI-compatible API + `OPENAI_API_KEY` / `GEMINI_API_KEY` / `OPENROUTER_API_KEY` | Set a `model`; OpenRouter reaches hundreds of hosted models with one key |
| `ollama`, `llamacpp`, or your own | Any OpenAI-compatible server (llama.cpp, vLLM, Ollama, LM Studio, a gateway you run) | Free and private. For Qwen3-style reasoning models add `extra: {chat_template_kwargs: {enable_thinking: false}}` |
| `claude-cli` | `claude -p` on your Claude Code login | Fallback when no API key is set |
| `none` | — | Quantitative scores only; everything else still works |

Structured output tries strict JSON Schema first, then JSON mode, then a prompt-only schema, so smaller and local
models still work. If a role's provider isn't configured, `llm.fallback` is tried in order (default `claude-cli`,
then `none`).

*Why no LLM gateway library?* Every provider here speaks the OpenAI-compatible Chat Completions protocol, so one
small adapter over `httpx` covers them with no extra dependency. A gateway holds all your keys, which makes it a
prime supply-chain target: LiteLLM's PyPI package shipped credential stealers twice in 2026. If you want a gateway
anyway (LiteLLM proxy, Portkey, any-llm-gateway), run it yourself and point a provider's `base_url` at it.

## Your settings vs. the repo
Everything personal stays out of git, so the repo can be public and shared:

| File | What | Committed? |
|---|---|---|
| `config/radar.yaml`, `seeds.yaml`, `taxonomy.yaml` | Shared defaults | yes |
| `config/local.yaml` | Your providers, endpoints, models, budgets, paths (deep-merged over `radar.yaml`; or set `RADAR_CONFIG_LOCAL`) | **no** |
| `config/seeds.local.yaml`, `config/taxonomy.local.yaml` | Your extra seeds and vocabulary (seeds append to the shared list) | **no** |
| `.env` | Your API keys (`radar setup`) | **no** |
| `data/`, `vault/Items`, `vault/Entities`, `vault/Daily`, … | What the radar generates for you | **no** |

Templates to start from: `config/local.example.yaml` and `.env.example`.

## AI coding agents (any harness)
`AGENTS.md` holds the project instructions in the open [AGENTS.md](https://agents.md) format (Codex, Cursor, Copilot,
Gemini CLI and more; Claude Code via `CLAUDE.md`, which imports it). The workflows are portable
[Agent Skills](https://agentskills.io) in `.agents/skills/` (`radar-daily`, `radar-deep-dive`, `radar-ask`,
`radar-expand`, `radar-calibrate`); `.claude/skills` points to them.

## X (x.com)
Much of the frontier is announced on X first. The radar reads X **only through sanctioned, pay-per-use APIs**. It
never scrapes: X's terms forbid crawling and scraping without written consent, and scraping needs account
credentials. Set one key in `.env`:

| Provider | Key | What it does | Cost (check current pricing) |
|---|---|---|---|
| `xapi` | `X_BEARER_TOKEN` (developer.x.com app) | Timelines of watched accounts, your public lists, recent-search queries, **who strong accounts follow** | ≈ $0.005 per post read, $0.010 per user read |
| `xai` | `XAI_API_KEY` | Grok `x_search`: account batches and semantic discovery queries. Posts are kept only if Grok **cited** them | $5 per 1k posts fetched, plus tokens |

`x.provider: auto` picks xapi, then xai, then off. With no key nothing is spent. Every call is projected against
`x.max_post_reads_per_run`, `x.max_user_reads_per_run` and `x.daily_usd_cap` ($8 by default) and refused if it
would cross a cap. `radar x-status` shows the provider, today's spend and the watched accounts.

What X adds:
- Posts linking arXiv, GitHub or Hugging Face resolve to the same canonical items as everything else. They carry
  X engagement and a `shared` edge from the account.
- A new **researcher attention** signal: an item shared early by several high-yield researchers scores higher.
- Expansion through the X follow graph with **co-follow** weighting. An account followed by many productive
  accounts rises fastest. X handles are also picked up from GitHub profiles, mentions next to research links,
  and early sharers of frontier items.
- Add your own curated X list ids under `x.lists` for a cheap, high-signal feed.

## Tuning
- Add seeds in `config/seeds.yaml`. One handle (`github`, `hf`, `feed`, `homepage`, `bluesky`, `x`,
  `arxiv_author`, `s2`) is enough.
- Change budgets, thresholds and sources in `config/radar.yaml`, and the vocabulary in `config/taxonomy.yaml`.
- For better embeddings: `pip install model2vec`, set `embedding.backend: model2vec`, then `radar reembed`.

## Tests
```bash
.venv/bin/python -m unittest discover -s tests -v
```

## License
MIT. See [LICENSE](LICENSE).
