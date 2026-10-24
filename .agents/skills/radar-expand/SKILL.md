---
name: radar-expand
description: "Grow the Frontier Radar source graph deliberately: resolve unresolved leads to real handles and add frontier sources in thin areas. Use when asked to find more sources, labs or researchers to follow."
---

# Radar Expand

Run commands from the repository root with `.venv/bin/python -m radar <command>`.

1. `.venv/bin/python -m radar unresolved -n 40`: find real handles for the leads worth following, and add them.
2. `.venv/bin/python -m radar stats` and `radar top -n 40 --days 7`: which modalities, areas or layers are thin?
3. For each thin area, find 3-5 frontier sources (small labs, research groups, independent researchers,
   infra projects) active in the last 90 days, and `radar add-entity` each with handles and a `--why`.
4. Report what you added and why, grouped by area.
