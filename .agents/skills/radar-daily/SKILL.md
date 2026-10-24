---
name: radar-daily
description: "Run the Frontier daily pipeline, then do the agentic research pass APIs can't do: read top items' primary sources, hunt the web for missed frontier releases, resolve leads, and write research notes. Use for the daily radar routine or when asked to update the radar."
---

# Radar Daily

Run commands from the repository root with `.venv/bin/python -m radar <command>`.

1. Run `.venv/bin/python -m radar run` and wait for it. Report the JSON stats in one line.
2. Read today's brief (newest file in `vault/Daily/`) and `.venv/bin/python -m radar top -n 25 --days 1`.
3. For the top 5 items: open the primary source (paper abstract and method section, repo README, model card).
   If the reviewer missed something or overrated it, say so and `radar rate` it. If an item is clearly frontier,
   write `vault/Research/<date> <short title>.md` (template `Templates/Research note.md`) with what is new,
   the closest prior work, why it matters, and the skeptic's view. Link items as `[[Items/...]]` notes.
4. Gap hunt (web search, 6-10 queries, last 72 hours only): new model releases, small/independent labs,
   harnesses/agent frameworks, AI infra (databases, kernels, serving, compilers, hardware), and AI-for-science.
   Query in the forms "released" / "open weights" / "arXiv" / "GitHub" with the current month. Skip anything the
   radar already has (`radar ask` or `radar item`). Add genuinely new things with `radar add-item`. Add the
   people/orgs behind them with `radar add-entity` and real handles.
5. Resolve leads: `radar unresolved`. For each one worth following, find a real handle (GitHub, HF org, RSS
   feed, Bluesky, arXiv name) and `radar add-entity` with it.
6. Finish with a 5-line summary: what's most frontier today, what you added, what you followed.
