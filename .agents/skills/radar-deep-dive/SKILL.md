---
name: radar-deep-dive
description: "Expert deep dive on one Frontier item: read the primary source, identify what is new versus prior work, judge evidence, and write a research note into the vault. Use when asked to dig into, explain or assess a specific paper, model, repo or radar id."
---

# Radar Deep Dive

Run commands from the repository root with `.venv/bin/python -m radar <command>`.

Deep dive on the item the user names (a radar id or URL).

1. `.venv/bin/python -m radar item <the user's input>` (if a URL is given, find its id via `radar ask`).
2. Read the primary source fully (paper/README/model card). Identify the core contribution, the exact
   method, the evidence (benchmarks, ablations), and the closest prior work. Check the radar's
   "closest prior" ids, and look for others on the web.
3. Write `vault/Research/<date> <short title>.md`: TL;DR, what's actually new, how it works, evidence quality,
   relation to prior work (with [[links]] to radar notes), who is behind it, what would change if it holds up,
   the skeptic's view, and what to watch next.
4. Follow the important people/orgs with `radar add-entity` (real handles), and `radar rate` the item.
