---
name: radar-calibrate
description: "Audit the Frontier Radar novelty scorer against expert judgement, rate disagreements and re-fit weights. Use when scores look off or periodically to keep the scorer honest."
---

# Radar Calibrate

Run commands from the repository root with `.venv/bin/python -m radar <command>`.

1. `.venv/bin/python -m radar top -n 40 --days 7`. Also look at a sample of mid-scored items (40-60) via `radar ask`.
2. Find disagreements: overrated hype, underrated sleepers (especially small labs and infra). For each clear
   case, `radar rate <id> <label>` with a one-line reason in chat.
3. `.venv/bin/python -m radar calibrate` and report the held-out loss change and the weight shifts.
