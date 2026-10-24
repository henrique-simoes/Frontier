# Open-source readiness and provider-agnostic LLMs

```yaml
item: open-source-llm-providers
branch: main
cf: { spec: CF-SPEC-3, tasks: [CF-46, CF-47, CF-48, CF-49, CF-50, CF-51, CF-52, CF-53, CF-54, CF-55, CF-56] }
phase: "—"
stage: S5-ship
status: done
blocked_on: null
last: { agent: claude-opus-5-5, at: 2026-10-24T08:30:00Z, ledger: L-6 }
next_action: "Owner decision: amend the single unpushed commit (history without personal/generated files) or add a new commit."
```

## Plan overview

### Problem
1. The radar only talks to Anthropic, or Claude through the `claude` CLI. The owner wants any model: local
   (their self-hosted Qwen on llama.cpp), Anthropic, OpenAI, Gemini, or a router such as OpenRouter.
2. The repository is to be open-sourced, but personal settings would leak: there is no place for per-user
   configuration, and the first commit tracks machine-specific Compass Forge state (absolute paths) and 2,240
   generated vault notes.

### Outcome
> *Bring your own model, keep your own settings.* Point the reviewer and chat at Claude, GPT, Gemini, OpenRouter or
> a model on your own GPU box, separately per role. Your endpoints, model choices, extra seeds and keys live in
> gitignored files (`config/local.yaml`, `.env`), so the public repo ships only defaults and templates.

### Goal / non-goals
- **Goals:** a deep-merged overlay (`config/local.yaml`, `config/seeds.local.yaml`, `config/taxonomy.local.yaml`,
  `RADAR_CONFIG_LOCAL`); a provider registry with `anthropic` (native SDK), `openai` (any OpenAI-compatible
  endpoint over httpx), `claude-cli` and `none`; per-role routing (`llm.judge`, `llm.ask`) with a fallback chain;
  structured output that degrades gracefully (json_schema → json_object → prompt-only); streaming chat;
  `<think>` stripping and per-provider `extra` body for local models; setup-wizard support (keys plus a model picker
  that lists models via the free `/models` endpoint and writes `config/local.yaml`); `.gitignore` and untracking of
  personal and generated files; a committed `config/local.example.yaml`; docs; the owner's self-hosted Qwen configured
  locally (in gitignored config/local.yaml) and verified live.
- **Non-goals:** bundling a third-party gateway library; tool use across providers; a hosted proxy.

### Acceptance criteria
| AC | Scenario | Verify |
|---|---|---|
| AC1 | `config/local.yaml` deep-merges over `radar.yaml`, and local seed lists append | `tests/test_providers.py::test_overlay_merge` |
| AC2 | Role → provider resolution honours the fallback chain when the chosen provider lacks credentials | `test_role_resolution_fallback` |
| AC3 | The OpenAI-compatible adapter sends json_schema, falls back to json_object, then prompt-only on 400, strips `<think>` and merges `extra` | `test_openai_structured_fallbacks` |
| AC4 | Streaming chat over SSE yields only content deltas (not reasoning) | `test_openai_stream` |
| AC5 | No personal or generated file is tracked by git, and `.gitignore` covers them | `git ls-files` check + `test_gitignore_covers_personal` |
| AC6 | The owner's self-hosted Qwen reviews a real item end to end via `config/local.yaml` (live, free) | `radar llm-test --role judge` |
| AC7 | Full suite green; README/CLAUDE.md document providers and per-user config | `unittest discover`; review |

### Rollback
Delete `config/local.yaml` to fall back to the defaults (Anthropic, then claude-cli, then none). The provider layer
is backward compatible with the old `llm.backend` / `judge_model` keys.

## Decision log

DEC-1 | 2026-10-24 | S0 | owner
Context: Two mid-turn owner messages: "be able to use a local model … Anthropic API, OpenAI, Gemini … implement an
agnostic API handling … I want to use my [self-hosted] qwen model", and "set it up … ready to be open-source on github, so
these personal configurations are not the ones that will go to the github repo … supports other users and their
settings".
Decision: The frame above.
Why: It restates both requests directly.

DEC-2 | 2026-10-24 | S0 | claude-opus-5-5
Context: `compass-forge classify` returned local_fix/lite with no risk signals.
Decision: Treat as Full.
Why: The change routes credentials to new endpoints, sends item text to third parties, and decides what gets
published (the CF skill says to pick the level yourself when the classification is wrong, and say so).

DEC-3 | 2026-10-24 | S1 | claude-opus-5-5
Context: Options for provider-agnostic calls: LiteLLM (broadest coverage), any-llm (Mozilla.ai, Apache-2.0, wraps
official SDKs, Python 3.11+), aisuite (MIT, pins openai 1.x), OpenRouter (hosted, not open source), Portkey, or a
thin adapter over the OpenAI-compatible Chat Completions protocol.
Decision: A thin in-repo OpenAI-compatible adapter (httpx, already a dependency) beside the native Anthropic SDK. No
gateway library. OpenRouter, a LiteLLM/Portkey proxy you run yourself, or any-llm-gateway can still be used by
pointing a profile's `base_url` at them.
Why: LiteLLM's PyPI package was backdoored with credential stealers twice in 2026 (1.82.7/1.82.8 in March;
1.107.0 on 2026-10-21), and an LLM gateway holds every provider key. The OpenAI-compatible protocol is served
natively by OpenAI, Gemini's compatibility endpoint, OpenRouter, xAI, Groq, Mistral, DeepSeek, Together, llama.cpp,
vLLM, Ollama and LM Studio, so one adapter of about 150 lines covers them with zero new supply-chain surface. The
native SDK stays for Claude to keep its features (effort, fallbacks).

DEC-4 | 2026-10-24 | S1 | claude-opus-5-5
Context: Live probe of the owner's GPU host: llama.cpp `llama-server` on :8080, model `qwen3.8-27b-ud-q4k-xl`. With thinking on,
300 tokens ran out before the JSON; with `chat_template_kwargs.enable_thinking=false` it returned schema-valid
JSON in 29 tokens, but scored on a 0–1 scale.
Decision: Profiles accept an `extra` body (e.g. `enable_thinking: false`). Judge scores are normalised: when
novelty, rigor and impact are all ≤ 1, they are multiplied by 10.
Why: This keeps small and local models usable without changing the strict schema shared with Anthropic.

DEC-5 | 2026-10-24 | S1 | claude-opus-5-5
Context: The initial commit (0da155d, unpushed) contains `.compass-forge/` and `recipes/` with absolute local paths,
plus generated vault notes.
Decision: Untrack them and add them to `.gitignore` in the working tree now. Whether to rewrite the single local
commit (to keep them out of history) or add a new commit is an owner decision: Autonomy hard stop #1, history
rewrite.
Why: Removing them from the index is reversible and local; rewriting history is not.

## Ledger

### L-1 | 2026-10-24T07:15:00Z | S0-frame | claude-opus-5-5 | framer | —
Did: Researched gateways (LiteLLM incidents, any-llm, aisuite, OpenRouter, Portkey). Read the GPU host's ssh alias (no
secrets) and probed :8000..:30000 with GET /v1/models: llama.cpp on :8080. Live test of json_schema with and without
thinking. CF classify (lite → overridden to Full, DEC-2); CF-SPEC-3 created. Scanned tracked files for personal
data.
Result: Frame, DEC-1..5.
Verified: `curl http://<gpu-host>:8080/v1/chat/completions …` → `{"novelty": 0.85, "novelty_class": "incremental"}`,
finish stop; `git ls-files | xargs grep -l <home path>` → .compass-forge/*, recipes/*.
Next: S1.

### L-2 | 2026-10-24T07:25:00Z | S1-plan | claude-opus-5-5 | planner | —
Did: Design below; CF clarify/plan/tasks; impact on config.py and llm.py; gate before.
Result: Ready for S2.
Verified: see CF record.
Next: S2.

## Design (S1)
- **config.py**: `deep_merge(base, over)` for dicts (lists are replaced, except seed lists, which are appended by
  `name`). Load order: `config/radar.yaml` ← `config/local.yaml` (or `$RADAR_CONFIG_LOCAL`).
  `seeds.yaml` + `seeds.local.yaml`; `taxonomy.yaml` ← `taxonomy.local.yaml`.
- **llm.py**: `llm.providers.<name> = {type, base_url?, api_key_env?, model?, headers?, extra?, timeout?}`;
  `llm.judge` / `llm.ask` = `{provider, model?, effort?, max_tokens?}`; `llm.fallback = [claude-cli, none]`.
  `resolve(role)` returns the first available `(name, profile, model)`. Adapters: `_anthropic_*` (existing code),
  `_openai_structured` (json_schema → json_object → plain, with `<think>` stripped), `_openai_stream` (SSE),
  `_cli_*`. `backend(role)` keeps returning a name for the UI and pipeline.
- **setup.py**: keys `OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` (validated via `/models`); `radar setup
  --llm`: pick a provider per role, list models from `/models`, write `config/local.yaml`.
- **cli**: `radar llm-test [--role judge|ask]` runs one tiny real call through the resolved provider.
- **Repo**: `.gitignore` adds `config/local.yaml`, `config/*.local.yaml`, `.compass-forge/`, `recipes/`, vault
  output folders; `git rm --cached` on them; `config/local.example.yaml` committed; vault keeps only the template
  (Home, Dashboards, Templates, `.obsidian` config).

DEC-6 | 2026-10-24 | S2 | claude-opus-5-5
Context: The owner added mid-flight: "the things you put in the .claude folder … in an open agents and skills format
other harnesses can understand".
Decision: In scope. `AGENTS.md` (agents.md format) is the canonical instruction file; `CLAUDE.md` is `@AGENTS.md`
(Claude Code reads AGENTS.md only when there is no CLAUDE.md). The five commands become Agent Skills (agentskills.io
`SKILL.md`, quoted YAML frontmatter, names prefixed `radar-`) in `.agents/skills/`; `.claude/skills` is a relative
symlink to it; `.claude/commands/` is removed.
Why: These are the two open formats most harnesses recognise. Skill discovery paths differ per harness, so a
symlink keeps a single source of truth.

DEC-7 | 2026-10-24 | S5 | claude-opus-5-5
Context: Owner instruction: personal configuration must not be published. This file named the owner's private host
in DEC-1, DEC-4 and L-1, and is publishable.
Decision: Redact the hostname to "self-hosted / GPU host" before the file is first committed. The facts are
unchanged; the host details live in the gitignored `config/local.yaml`.
Why: Privacy instruction from the owner; the file was never committed, so no history is rewritten.

### L-3 | 2026-10-24T08:05:00Z | S2-execute | claude-opus-5-5 | executor | —
Did: `config.py`: `deep_merge`, `merge_seeds`, `local_config_path` (`RADAR_CONFIG_LOCAL`), `reload`. `llm.py` rewritten:
provider registry (anthropic, openai-compatible, claude-cli, none), role routing with a fallback chain,
json_schema → json_object → prompt-only, `<think>` stripping (structured and streaming), `extra`/`headers`
per provider, `normalize_scores`. Default models moved from roles to providers (found live: the role-level default
model leaked into a local provider). `config/radar.yaml` gains providers openai/gemini/openrouter/ollama/llamacpp;
`config/local.example.yaml` committed, `config/local.yaml` (owner's host) gitignored. `setup.py`: OpenAI, Gemini and
OpenRouter keys plus validators; `run_llm_wizard` (lists /models, writes local.yaml); `radar setup --llm`;
`radar llm-test`. `.gitignore` covers personal and generated files; `git rm --cached` on .compass-forge, recipes and
vault output (2,330 files, kept on disk). AGENTS.md + 5 Agent Skills + `.claude/skills` symlink (DEC-6). README and
ARCHITECTURE updated. Tests: `tests/test_providers.py`; fixtures in test_core/test_x made hermetic (ignore personal
overlays, LLM forced to none).
Result: AC1–AC7 met.
Verified: `.venv/bin/python -m unittest discover -s tests` → OK; `radar llm-test --role judge` → owner's host judged
arxiv:2610.28698 in 12.1s (incremental, 5/7/4); `radar llm-test --role ask` → streamed in 1.7s; `git ls-files | xargs
grep -l <home path|host>` → only this file, then redacted (DEC-7); `compass-forge gate after --task CF-48` → 0 new.
Next: S3.

## Review (S3)
Blind pass (same agent; DEC-3 of the x-source plan applies): AC1–AC7 re-measured green; no divergence from L-3.

| ID | Sev | Dim | Where | Finding | CF task | Status |
|----|-----|-----|-------|---------|---------|--------|
| F-1 | Major | Bugs | llm.available | A self-hosted endpoint counted as available without a check: an offline host would make the judge wait out the timeout (900 s × 40 items) | CF-48 | fixed |
| F-2 | Major | Privacy | this file | Named the owner's private host | CF-48 | fixed |
| F-3 | Minor | Quality | setup.write_local | Rewriting local.yaml drops the user's comments (YAML round-trip) | — | accepted-risk |
| F-4 | Minor | Integration | .claude/skills | Symlink: on Windows without symlink support git checks it out as a text file | — | accepted-risk |

### L-4 | 2026-10-24T08:20:00Z | S4-remediate | claude-opus-5-5 | remediator | —
Did: F-1: `_reachable(base_url)` probes `/models` (4 s, cached per process) for key-less endpoints, so an offline
host falls back to the chain immediately; the judge loop stops after 3 consecutive failures; test
`test_offline_selfhosted_falls_back`. F-2: DEC-7 redaction. F-3: accepted; the file starts with a pointer to the
example, which keeps the comments. F-4: accepted; AGENTS.md documents the skills path, so harnesses can be pointed
at it directly.
Result: Register closed; re-review of the changed lines found nothing new.
Verified: `.venv/bin/python -m unittest discover -s tests` → OK (37 tests); `pyflakes radar/` → clean apart from the
pre-existing hf.py nit.
Next: S5.

### L-5 | 2026-10-24T08:25:00Z | S5-ship | claude-opus-5-5 | executor | —
Did: CF evidence and finish on CF-46..56; `spec accept CF-SPEC-3`; evaluation recorded; blind-coverage row; docs graduated.
Result: Shipped to the working tree. Not committed: amending the single unpushed commit is a history rewrite
(Autonomy hard stop #1), so the owner decides.
Retro: Two defects came from configuration interacting, not from code (the role model leaking through the deep
merge, and fixtures inheriting the developer's overlay). Next time, test overlays against the real repository
config and keep test fixtures isolated from personal files.
Verified: see L-6.
Next: owner decision.

### L-6 | 2026-10-24T08:30:00Z | S5-ship | claude-opus-5-5 | executor | —
Did: Status Block → done; ran `verify_lifecycle.py`.
Result: See the owner report.
Verified: `python3 ~/.claude/skills/build-stream/scripts/verify_lifecycle.py docs/build-stream/2026-10-24-open-source-llm-providers.md`.
Next: owner decision (commit strategy).
