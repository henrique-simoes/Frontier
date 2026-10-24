---
name: radar-ask
description: "Answer a question from the Frontier knowledge graph with cited radar ids, researching gaps on the web and adding what is found. Use for questions about recent AI research, labs, people, releases or trends."
---

# Radar Ask

Run commands from the repository root with `.venv/bin/python -m radar <command>`.

Answer the user's question.

Run `.venv/bin/python -m radar ask "<the user's input>"` for the grounded answer. If the evidence is thin or stale, do
targeted web research (newest first), add what you find with `radar add-item` / `radar add-entity`, and
answer with radar ids cited in brackets. Lead with the answer.
