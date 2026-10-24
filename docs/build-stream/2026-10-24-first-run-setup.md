# First-run setup: `radar setup` for API keys

```yaml
item: first-run-setup
branch: main
cf: { spec: CF-SPEC-2, tasks: [CF-32, CF-33, CF-34, CF-35, CF-36, CF-37, CF-38, CF-39, CF-40, CF-41, CF-42, CF-43, CF-44, CF-45] }
phase: "—"
stage: S5-ship
status: done
blocked_on: null
last: { agent: claude-opus-5-5, at: 2026-10-24T07:00:00Z, ledger: L-5 }
next_action: "None. Owner: run scripts/setup.sh (or radar setup) in your own terminal."
```

## Plan overview

### Problem
Setting up keys means hand-editing `.env` and knowing where each key comes from. Worse, only `scripts/run_daily.sh`
reads `.env`: `radar run`, `radar ask` and `radar serve` started from a terminal silently run without keys.

### Outcome (Working Backwards)
> *One command gets Frontier Radar ready.* `scripts/setup.sh` builds the environment and walks you through each
> key: what it unlocks, where to get it and what it costs. Input is hidden, each key is checked against the
> provider's free endpoint, and everything is saved to a private `.env` that every `radar` command reads. Run
> `radar setup --check` any time to see what is configured and working.

### Goal / non-goals
- **Goals:** an interactive wizard (hidden input, keep/replace/remove per key); free validation with clear
  results; paid X API validation only on explicit opt-in; `.env` written atomically with 0600 permissions,
  preserving comments and unknown lines; `--check` (status + validation, no prompts) and `--only KEY`; automatic
  `.env` loading for every CLI command and the server, never overriding variables already set; a setup.sh hand-off;
  an offer at the end to run the radar and install the schedule.
- **Non-goals:** a macOS Keychain store, a GUI, and the agent entering keys. Keys are typed by the owner in their
  own terminal, never in chat.

### Appetite
Small. Full-level ceremony, because secrets are involved.

### Acceptance criteria
| AC | Scenario | Verify |
|---|---|---|
| AC1 | Given an existing `.env` with comments and unknown keys, when one key is set, then only that line changes, the file is 0600 and nothing else is lost | `tests/test_setup.py::test_write_env_preserves_and_chmods` |
| AC2 | Given `FOO` already in the environment, when `.env` has `FOO=bar`, then the loader keeps the environment value; unset keys are loaded | `test_dotenv_never_overrides` |
| AC3 | Keys never appear in output: status shows only `set (…last4)` | `test_mask_and_check_output_has_no_secret` |
| AC4 | Validators call only the provider's free endpoint and map 200→ok, 401/403→invalid, other→unreachable | `test_validators_map_status` |
| AC5 | X API validation is skipped unless explicitly confirmed | `test_x_validation_is_opt_in` |
| AC6 | Full suite green; `radar setup --check` runs non-interactively | `unittest discover`; `radar setup --check` |
| AC7 | README, CLAUDE.md and the setup.sh output point to `radar setup` | review |

### Rollback
The change is additive: delete `radar/setup.py`, the subcommand and the loader call. `.env` stays compatible.

## Decision log

DEC-1 | 2026-10-24 | S0 | owner
Context: The owner asked for "a script or command I can use to install the API keys easily, like a first run".
Decision: The frame above is approved by that request; the standing CLAUDE.md rule covers using build-stream and
compass-forge.
Why: The request names the outcome directly.

DEC-2 | 2026-10-24 | S1 | claude-opus-5-5
Context: Where to keep keys: `.env`, macOS Keychain, or both.
Decision: `.env` (gitignored, 0600, atomic write), loaded automatically. No Keychain.
Why: launchd, the CLI and the server already share `.env`; the Keychain would need a second code path and
prompts in launchd. 0600 plus gitignore gives the same exposure as today, with correct permissions.

DEC-3 | 2026-10-24 | S1 | claude-opus-5-5
Context: Validation endpoints and cost.
Decision: Anthropic `GET /v1/models` (SDK `models.list`); GitHub `GET /rate_limit`; Semantic Scholar paper lookup;
xAI `GET /v1/models`; Hugging Face `GET /api/whoami-v2`; OpenAlex `GET /works?per-page=1` (fractions of a cent per
docs, accepted). X API: no free endpoint is documented, so validate only on opt-in with one user lookup (about $0.01).
Why: Validate everything that is free; never spend silently.

DEC-4 | 2026-10-24 | S1 | claude-opus-5-5
Context: The no-subagents rule (as in the x-source plan, DEC-3).
Decision: S3 is a same-agent blind pass; coverage is recorded as `pending`.
Why: The owner's global instruction takes precedence.

## Ledger

### L-1 | 2026-10-24T06:35:00Z | S0-frame | claude-opus-5-5 | framer | —
Did: Framed the request. CF classify → security_or_architecture / full (risk signals: permission). Created
CF-SPEC-2; clarified, planned and generated tasks (CF-32..45).
Result: Frame above; DEC-1.
Verified: `compass-forge classify … --recipe fastapi` → full, basis scored.
Next: S1.

### L-2 | 2026-10-24T06:40:00Z | S1-plan | claude-opus-5-5 | planner | —
Did: Design below; DEC-2..4. Impact on radar/cli.py, radar/config.py and radar/server/app.py: resolved, complete,
high, repo_index. config.py is imported by every module, so the loader must be additive and non-overriding.
Work order for CF-34; `gate before --task CF-34`.
Result: Ready for S2.
Verified: `compass-forge intelligence impact --path radar/config.py` → complete/high.
Next: S2.

## Design (S1)
- `radar/config.py`: `load_dotenv(path=ROOT/.env)` is a small parser (`KEY=VALUE`, optional quotes, `#` comments,
  `export ` prefix) that sets only missing variables. It is called from `cli.main()` and at server import.
- `radar/setup.py`: a `KEYS` registry (name, purpose, URL, cost note, validator, paid flag); `mask()`;
  `read_env()` / `write_env()` (atomic temp file + `os.replace`, chmod 0600, preserving lines); validators returning
  `ok | invalid | unreachable | skipped`; `run_wizard()` (getpass prompts, keep/replace/remove), `check()`.
- `cli.py`: `radar setup [--check] [--only KEY]`.
- `scripts/setup.sh`: after installing, run `radar setup` when stdin is a TTY.
- Tests in `tests/test_setup.py`, with HTTP replaced by stubs.

### L-3 | 2026-10-24T06:55:00Z | S2-execute | claude-opus-5-5 | executor | —
Did: `radar/setup.py` (KEYS registry, validators, mask, atomic 0600 `write_env`, `check`, `run_wizard`);
`config.load_dotenv` (non-overriding), called from `cli.main` and at server import; `radar setup [--check] [--only]
[--paid]`; `scripts/setup.sh` chmods `.env` and hands off to the wizard on a TTY; README and CLAUDE.md; `tests/test_setup.py`.
Result: AC1–AC7 met.
Verified: `.venv/bin/python -m unittest discover -s tests` → Ran 28 tests OK; `radar setup --check` → exit 0, masked
table; `compass-forge gate after --task CF-34 --summary` → new_warnings 0, new_issue_count 0.
Next: S3.

## Review (S3)
Blind pass (same agent, DEC-4): re-ran AC1–AC6 as open questions; all measured green, and they match L-3's claims.

| ID | Sev | Dim | Where | Finding | CF task | Status |
|----|-----|-----|-------|---------|---------|--------|
| F-1 | Minor | Security | existing `.env` | A `.env` created earlier by `cp .env.example` was 0644 | CF-34 | fixed |
| F-2 | Minor | Security | setup.v_openalex | OpenAlex takes its key as a query parameter (the provider's design), so it is in the request URL; errors print only the exception type, never the URL | — | accepted-risk |
| F-3 | Nit | Quality | read_env | An inline ` #` inside an unquoted value is treated as a comment (quote such values) | — | accepted-risk |

### L-4 | 2026-10-24T06:58:00Z | S4-remediate | claude-opus-5-5 | remediator | —
Did: F-1: `check` warns when `.env` is group- or world-readable and prints the chmod fix; `setup.sh` runs `chmod 600`;
`write_env` always writes 0600. F-2 and F-3 accepted with the reasons above.
Result: Register closed; the re-review of the changed lines found nothing new.
Verified: `radar setup --check` → WARNING line shown for the 644 file; 28 tests OK.
Next: S5.

### L-5 | 2026-10-24T07:00:00Z | S5-ship | claude-opus-5-5 | executor | —
Did: CF evidence and finish on CF-32..45; `spec accept CF-SPEC-2`; evaluation recorded; Status Block → done.
Result: Shipped to the working tree (uncommitted).
Retro: the first run of `--check` on the real repo found F-1 that the unit tests had not modelled; keep running new
commands against the real working tree before review.
Verified: `compass-forge spec accept CF-SPEC-2`; `verify_lifecycle.py` (exit recorded in the next owner-visible report).
Next: none.
