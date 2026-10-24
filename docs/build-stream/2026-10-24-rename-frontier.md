# Rename the project to Frontier

```yaml
item: rename-frontier
branch: main
cf: { tasks: [CF-57, CF-58, CF-59, CF-60, CF-61] }
phase: "—"
stage: S5-ship
status: done
blocked_on: null
last: { agent: claude-opus-5-5, at: 2026-10-24T09:10:00Z, ledger: L-1 }
next_action: "None."
```

## Plan overview
Owner: "The name of the project is Frontier". Standard change (CF classify: standard_change). The display name
"Frontier Radar" becomes "Frontier" in docs, UI, skill descriptions, package metadata (`frontier`), User-Agent and
launchd labels (`com.frontier.*`). The `radar` CLI and Python module keep their names: "the radar" is what the tool
does, and renaming them would break every command in the docs and skills (DEC-1). Historical lifecycle records keep
the old name (immutable intent docs).
Acceptance: no old name outside `docs/build-stream/`; the suite stays green; no new drift.

## Decision log
DEC-1 | 2026-10-24 | S1 | claude-opus-5-5
Context: Renaming the project could also mean renaming the `radar` command and package.
Decision: Rename the display and distribution name only.
Why: The command's name describes its function, not the brand; renaming it breaks users' muscle memory, the
skills and the docs for no gain.

## Ledger
### L-1 | 2026-10-24T09:10:00Z | S2-execute→S5-ship | claude-opus-5-5 | executor | —
Did: Replaced the name in 18 files (see commit); `pyproject` name `frontier`; User-Agent points to the GitHub repo.
Result: Done. Self-review: grep confirms no stale references; UI title and nav brand read "Frontier".
Verified: `git grep -i "frontier radar|frontier-radar"` outside docs/build-stream → none; `.venv/bin/python -m unittest
discover -s tests` → OK; `compass-forge gate after --task CF-59 --summary` → 0 new.
Next: none.
