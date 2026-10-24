# Frontier: instructions for AI coding agents

This file follows the open [AGENTS.md](https://agents.md) format and is read by Codex, Cursor, GitHub Copilot,
Gemini CLI, Claude Code (through `CLAUDE.md`, which imports it) and other harnesses.

This repo is a self-expanding research radar for the AI frontier. A deterministic Python pipeline (`radar/`)
collects, scores and links items into SQLite (`data/radar.db`) and an Obsidian vault (`vault/`). Your job in this
repo is to be the **agentic layer** on top of it: research what APIs can't reach, resolve leads, and answer
questions grounded in the radar.

Always use the venv: `.venv/bin/python -m radar <cmd>`. Run `--help` for commands.

## Principles
- **The radar is the memory.** Anything you learn goes back in through the CLI (`add-item`, `add-entity`,
  `rate`) or as a note in `vault/Research/`. Never edit generated content between `<!-- radar:auto -->` markers.
- **Newest and deepest first.** Prefer primary sources (paper, repo, model card, lab post) over coverage. Give
  extra attention to small labs, independent researchers, infra (databases, kernels, serving, compilers),
  harnesses/agent frameworks, and science-for-AI. Big-lab news matters, but it is rarely what the radar is for.
- **Novelty is relative to prior work.** When judging, name the closest prior work and what actually differs.
  Always state the skeptic's reading.
- **Cite ids.** Refer to items and entities by radar id (`arxiv:2610.31667`, `org:sakana-ai`) so links resolve.

## Keys
API keys live in `.env` (0600, gitignored, auto-loaded). Never ask for keys in chat and never type them anywhere:
the user runs `radar setup` in their own terminal. You may run `radar setup --check` (it prints masked values only).

## Personal vs shared settings
Shared defaults: `config/radar.yaml`, `config/seeds.yaml`, `config/taxonomy.yaml` (committed). Personal settings:
`config/local.yaml` (endpoints, model choices, budgets, paths), `config/seeds.local.yaml`, `.env` (all gitignored).
Never put a personal host, path, key or model choice into a committed file; use `config/local.yaml`.
Which model reviews and chats is `llm.judge` / `llm.ask` (any provider in `llm.providers`); check with
`radar llm-test --role judge`.

## Commands you will use
| Need | Command |
|---|---|
| Today's top items | `radar top -n 30 --days 1` |
| One item with its edges | `radar item <id>` |
| What the radar did | `radar activity -n 60` |
| Grounded answer | `radar ask "<question>"` |
| Follow a person/org | `radar add-entity person "Name" --handle github=x --handle arxiv_author="Name" --why "..."` |
| Add something APIs missed | `radar add-item <url> --title "..." --kind release --summary "..." --org "Lab"` |
| Leads needing a handle | `radar unresolved` |
| Rate (trains the scorer) | `radar rate <id> frontier\|solid\|incremental\|hype\|irrelevant` |

Handles: `github`, `hf` (Hugging Face org), `bluesky`, `x` (X handle, no @), `feed` (RSS/Atom), `homepage` (feed
auto-discovered), `arxiv_author`, `s2` (Semantic Scholar author id).

X is read only through the official X API or xAI `x_search` (`radar x-status`). Never scrape x.com, never ask for or
use X account credentials, and never raise `x.daily_usd_cap` on your own: spending more is the user's decision.

## Skills
Portable [Agent Skills](https://agentskills.io) live in `.agents/skills/` (each a `SKILL.md`); `.claude/skills` points to
the same folder. Harnesses that look elsewhere can be pointed there, e.g. `ln -s ../.agents/skills .codex/skills`.
- `radar-daily`: run the pipeline, then the agentic research pass, then the research note.
- `radar-deep-dive`: read one item's primary source and write an expert note.
- `radar-ask`: answer from the radar, researching gaps on the web.
- `radar-expand`: resolve leads and find frontier sources the graph is missing.
- `radar-calibrate`: review the scorer against expert judgement and propose rating updates.
