# Blind measurement sheet: x-source (S3 phase 1)

Reviewer: claude-opus-5-5 (same model as the implementer; see DEC-3, so coverage is `self-only`)
Measured: 2026-10-24, before re-reading the lifecycle file's `Verified:` claims. **Frozen.**

| Question (open form) | Command run | Measured |
|---|---|---|
| With no X credentials, how many X/xAI requests does a collection make? | `unittest -k test_no_credentials_no_requests tests.test_x` | 0 requests; test OK |
| Do posts linking arXiv/HF/GitHub become canonical items with X metrics and share edges? | `-k test_xapi_timeline_resolves_links` | OK |
| How many uncited Grok posts survive extraction? | `-k test_xai_citation_grounding` | 0 survive; test OK |
| Is any request made when the projected cost crosses the daily cap? | `-k test_spend_cap_refuses` | none; budget event logged; OK |
| Does a co-followed account's prior rise monotonically with the number of strong followers? | `-k test_cofollow_prior` | yes; OK |
| Does `attention` separate shared from unshared items, and leave old items' scores unchanged? | `-k test_attention_signal` | yes; OK |
| How many tests in the whole suite pass? | `.venv/bin/python -m unittest discover -s tests` | 19 of 19 |
| Do CLI, pipeline and server import? | `python -c "import radar.cli, radar.pipeline, radar.server.app"` | ok |
| Do README / ARCHITECTURE / NOVELTY / CLAUDE.md describe X setup and the new signal? | `grep -c "x_search\|X_BEARER_TOKEN"` on each | **0 in all four: AC8 not met** |
| New architecture drift vs baseline? | `compass-forge gate after --task CF-3 --summary` | 0 new warnings (4 inherited) |
