"""radar CLI. Run `python -m radar --help`."""
from __future__ import annotations

import argparse
import json
import sys

from .calibrate import LABEL_VALUE, centrality, fit, rescore, retro_labels
from .config import config, load_dotenv
from .store import Store


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_run(a) -> None:
    from .pipeline import Pipeline
    sweeps = a.sweeps.split(",") if a.sweeps else None
    _print(Pipeline().run(sweeps=sweeps, entity_budget=a.budget, judge=not a.no_judge))


def cmd_serve(a) -> None:
    import uvicorn
    cfg = config()
    uvicorn.run("radar.server.app:app", host=a.host or cfg.get("server.host"), port=a.port or cfg.get("server.port"))


def cmd_ask(a) -> None:
    from .ask import ask
    stream, items, _ = ask(Store(), " ".join(a.question))
    for chunk in stream:
        sys.stdout.write(chunk)
        sys.stdout.flush()
    print()


def cmd_top(a) -> None:
    s = Store()
    rows = s.q(f"""SELECT id, title, novelty, kind, published, note_path FROM items WHERE status != 'baseline'
                   AND discovered >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-{int(a.days)} days')
                   ORDER BY novelty DESC LIMIT ?""", (a.n,))
    for r in rows:
        print(f"{r['novelty'] or 0:5.1f}  {r['kind']:<9} {r['id']:<34} {r['title'][:90]}")


def cmd_item(a) -> None:
    s = Store()
    it = s.item(a.id)
    if not it:
        sys.exit(f"no item {a.id}")
    it["edges"] = s.neighbors(a.id, 50)
    _print(it)


def cmd_rate(a) -> None:
    if a.label not in LABEL_VALUE:
        sys.exit(f"label must be one of {list(LABEL_VALUE)}")
    s = Store()
    s.feedback(a.id, a.label, LABEL_VALUE[a.label], "user")
    s.event("feedback", f"rated {a.id} as {a.label}", a.id)
    s.commit()
    print("ok")


def cmd_add_entity(a) -> None:
    s = Store()
    handles = dict(h.split("=", 1) for h in a.handle or [])
    eid, new = s.upsert_entity(a.type, a.name, handles, prior=a.prior, via=a.via or "manual", status="active")
    s.event("discover", f"{'added' if new else 'updated'} {a.type} '{a.name}' manually ({a.why or 'no reason given'})", eid)
    s.commit()
    print(eid)


def cmd_add_item(a) -> None:
    from .models import Item
    from .pipeline import Pipeline
    from .util import canonical_id
    p = Pipeline()
    p.started = "9999"
    iid = p.ingest(Item(id=canonical_id(a.url), kind=a.kind, title=a.title, url=a.url, source="manual",
                        summary=a.summary or "", published=a.published, orgs=a.org or []))
    p.embed_new()
    p.score_quant()
    p.store.event("collect", f"manually added '{a.title[:80]}'", iid)
    p.store.commit()
    print(iid)


def cmd_calibrate(a) -> None:
    s = Store()
    retro_labels(s)
    rep = fit(s)
    centrality(s)
    s.commit()
    _print(rep)


def cmd_rescore(a) -> None:
    print(rescore(Store()))


def cmd_reembed(a) -> None:
    from .pipeline import Pipeline
    s = Store()
    s.db.execute("UPDATE items SET embedding=NULL, scored_at=NULL")
    s.db.execute("DELETE FROM concepts")
    s.db.execute("DELETE FROM concept_pairs")
    s.db.execute("DELETE FROM concept_items")
    s.set_meta("semantic_history", [])
    s.commit()
    p = Pipeline(store=s)
    p.started = "9999"
    p.embed_new()
    p.score_quant()
    print("re-embedded and re-scored")


def cmd_x_status(a) -> None:
    from .sources import x as xsrc
    s = Store()
    spend = s.get_meta("x_spend") or {}
    _print({"provider": xsrc.provider(), "configured": config().get("x.provider"),
            "daily_usd_cap": config().get("x.daily_usd_cap"), "spent_today_usd": spend.get(s.q("SELECT strftime('%Y-%m-%d','now') AS d")[0]["d"], 0),
            "spend_last_days": dict(list(spend.items())[-7:]),
            "watched_x_accounts": s.db.execute("""SELECT COUNT(*) FROM entities WHERE status IN ('active','candidate')
                                                  AND handles LIKE '%"x":%'""").fetchone()[0],
            "invalid_handles": [r["name"] for r in s.q("SELECT name FROM entities WHERE meta LIKE '%x_invalid%'")],
            "shares_recorded": s.db.execute("SELECT COUNT(*) FROM edges WHERE rel='shared'").fetchone()[0]})


def cmd_unresolved(a) -> None:
    s = Store()
    _print(s.q("""SELECT id, name, type, meta FROM entities WHERE meta LIKE '%needs_resolution%' AND status != 'muted'
                  ORDER BY prior DESC LIMIT ?""", (a.n,)))


def cmd_activity(a) -> None:
    for e in reversed(Store().q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (a.n,))):
        print(f"{e['ts']}  {e['kind']:<10} {e['message']}")


def cmd_stats(a) -> None:
    s = Store()
    one = lambda sql: s.db.execute(sql).fetchone()[0]  # noqa: E731
    _print({
        "items": one("SELECT COUNT(*) FROM items"), "judged": one("SELECT COUNT(*) FROM items WHERE qual != '{}'"),
        "entities_watched": one("SELECT COUNT(*) FROM entities WHERE status IN ('active','candidate')"),
        "entities_known": one("SELECT COUNT(*) FROM entities"), "edges": one("SELECT COUNT(*) FROM edges"),
        "ratings": one("SELECT COUNT(*) FROM feedback WHERE source='user'"),
        "runs": one("SELECT COUNT(*) FROM runs"), "weights": s.get_meta("weights") or "prior",
    })


def cmd_llm_test(a) -> None:
    """One small real call through the provider a role resolves to (costs whatever that provider charges)."""
    import time
    from . import llm
    name, profile, rc = llm.resolve(a.role)
    model = rc.get("model") or profile.get("model") or "(provider default)"
    print(f"role={a.role} provider={name} type={profile.get('type')} model={model}")
    if name == "none":
        sys.exit("no provider available for this role; run `radar setup --llm`")
    t0 = time.time()
    if a.role == "judge":
        s = Store()
        rows = s.q("SELECT * FROM items WHERE kind='paper' AND summary != '' ORDER BY novelty DESC LIMIT 1")
        item = rows[0] if rows else {"id": "test:1", "title": "A 2x faster attention kernel for long context",
                                     "kind": "paper", "summary": "We present a fused attention kernel.", "authors": []}
        out = llm.judge(item, [])
        print(f"judged [{item['id']}] in {time.time() - t0:.1f}s:")
        _print({k: out.get(k) for k in ("novelty_class", "novelty", "rigor", "impact", "what_is_new", "confidence")})
    else:
        for chunk in llm.ask_stream("In one sentence, what is this radar for?", "[test:1] Frontier test context"):
            sys.stdout.write(chunk)
        print(f"\n({time.time() - t0:.1f}s)")


def cmd_setup(a) -> None:
    from . import setup
    if a.check:
        sys.exit(setup.check(allow_paid=a.paid))
    if a.llm:
        sys.exit(setup.run_llm_wizard())
    sys.exit(setup.run_wizard(only=a.only))


def main(argv=None) -> None:
    load_dotenv()   # every command sees the keys saved by `radar setup` (never overrides the environment)
    ap = argparse.ArgumentParser(prog="radar", description="Frontier")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("llm-test", help="one real call through the configured provider for a role")
    p.add_argument("--role", choices=["judge", "ask"], default="judge")
    p.set_defaults(fn=cmd_llm_test)

    p = sub.add_parser("setup", help="first-run wizard: API keys (hidden input), validation, first run")
    p.add_argument("--check", action="store_true", help="show key status and validate, no prompts")
    p.add_argument("--only", help="set just one key, e.g. --only XAI_API_KEY")
    p.add_argument("--paid", action="store_true", help="with --check: also validate the X API key (~$0.01)")
    p.add_argument("--llm", action="store_true", help="choose providers/models for review and chat (writes config/local.yaml)")
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("run", help="daily pipeline")
    p.add_argument("--sweeps", help="comma list: arxiv,hf_papers,hf_models,github,hn")
    p.add_argument("--budget", type=int, help="entity checks this run")
    p.add_argument("--no-judge", action="store_true", help="skip LLM review")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("serve", help="web app")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("ask", help="ask the radar")
    p.add_argument("question", nargs="+")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("top", help="top items")
    p.add_argument("-n", type=int, default=25)
    p.add_argument("--days", type=float, default=2)
    p.set_defaults(fn=cmd_top)

    p = sub.add_parser("item", help="show one item with edges")
    p.add_argument("id")
    p.set_defaults(fn=cmd_item)

    p = sub.add_parser("rate", help="label an item")
    p.add_argument("id")
    p.add_argument("label")
    p.set_defaults(fn=cmd_rate)

    p = sub.add_parser("add-entity", help="watch a person/org: --handle github=x --handle feed=https://...")
    p.add_argument("type", choices=["person", "org"])
    p.add_argument("name")
    p.add_argument("--handle", action="append")
    p.add_argument("--prior", type=float, default=0.6)
    p.add_argument("--via")
    p.add_argument("--why")
    p.set_defaults(fn=cmd_add_entity)

    p = sub.add_parser("add-item", help="add something the APIs missed")
    p.add_argument("url")
    p.add_argument("--title", required=True)
    p.add_argument("--kind", default="post", choices=["paper", "model", "repo", "post", "release"])
    p.add_argument("--summary")
    p.add_argument("--published")
    p.add_argument("--org", action="append")
    p.set_defaults(fn=cmd_add_item)

    for name, fn, help_ in (("calibrate", cmd_calibrate, "re-fit weights, retro labels, centrality"),
                            ("rescore", cmd_rescore, "recompute frontier scores with current weights"),
                            ("reembed", cmd_reembed, "re-embed everything (after changing embedding backend)"),
                            ("stats", cmd_stats, "store statistics")):
        sub.add_parser(name, help=help_).set_defaults(fn=fn)

    sub.add_parser("x-status", help="X provider, spend and watched accounts").set_defaults(fn=cmd_x_status)

    p = sub.add_parser("unresolved", help="entities the reviewer suggested but no handle is known")
    p.add_argument("-n", type=int, default=30)
    p.set_defaults(fn=cmd_unresolved)

    p = sub.add_parser("activity", help="what the radar did")
    p.add_argument("-n", type=int, default=40)
    p.set_defaults(fn=cmd_activity)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
