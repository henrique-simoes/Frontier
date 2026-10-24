"""X source tests (AC1-AC6 of docs/build-stream/2026-10-24-x-source.md). No network: _request is replaced."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

if "RADAR_ROOT" not in os.environ:   # standalone run; under discover, test_core already set this up
    TMP = Path(tempfile.mkdtemp(prefix="radar-xtest-"))
    ROOT = Path(__file__).resolve().parent.parent
    shutil.copytree(ROOT / "config", TMP / "config", ignore=shutil.ignore_patterns("local.yaml", "*.local.yaml"))
    txt = (TMP / "config" / "radar.yaml").read_text()
    (TMP / "config" / "radar.yaml").write_text(txt.replace("provider: anthropic", "provider: none")
                                               .replace("macos: true", "macos: false"))
    os.environ["RADAR_ROOT"] = str(TMP)
    os.environ["RADAR_CONFIG_LOCAL"] = str(TMP / "config" / "local.yaml")
TMP = Path(os.environ["RADAR_ROOT"])

from radar import novelty  # noqa: E402
from radar.expand import Expander  # noqa: E402
from radar.pipeline import Pipeline  # noqa: E402
from radar.sources import x as xsrc  # noqa: E402
from radar.sources import x_collect  # noqa: E402
from radar.store import Store  # noqa: E402
from radar.util import canonical_id  # noqa: E402

TIMELINE = {
    "data": [
        {"id": "111", "text": "New paper: sparse photonic attention https://t.co/a", "created_at": "2026-10-23T10:00:00Z",
         "author_id": "9", "public_metrics": {"like_count": 420, "retweet_count": 80, "quote_count": 12, "bookmark_count": 30},
         "entities": {"urls": [{"expanded_url": "https://arxiv.org/abs/2610.12345"}], "mentions": [{"username": "coauthor_x"}]}},
        {"id": "112", "text": "weights are up", "created_at": "2026-10-23T11:00:00Z", "author_id": "9",
         "public_metrics": {"like_count": 50},
         "entities": {"urls": [{"expanded_url": "https://huggingface.co/TinyLab/Photon-7B"},
                               {"expanded_url": "https://github.com/tinylab/photon"}]}},
        {"id": "113", "text": "lol", "created_at": "2026-10-23T12:00:00Z", "author_id": "9", "public_metrics": {}},
    ],
    "includes": {"users": [{"id": "9", "username": "tinylab_ai", "name": "Tiny Lab"}]},
}


class Recorder:
    """Stands in for x._request; records calls and returns canned responses by path."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for frag, resp in self.routes.items():
            if frag in url:
                return resp(kw) if callable(resp) else resp
        raise AssertionError(f"unexpected request {url}")


class XTestBase(unittest.TestCase):
    def setUp(self):
        self.store = Store(TMP / f"x{self._testMethodName}.db")
        self._env = {k: os.environ.pop(k, None) for k in ("X_BEARER_TOKEN", "XAI_API_KEY")}
        self._req = xsrc._request

    def tearDown(self):
        xsrc._request = self._req
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


class TestNoCredentials(XTestBase):
    def test_no_credentials_no_requests(self):  # AC1
        rec = Recorder({})
        xsrc._request = rec
        p = Pipeline(store=self.store, log=lambda m: None)
        stats = x_collect.Collector(self.store, p.ingest, p.exp, xsrc.Budget(self.store)).run()
        self.assertEqual(xsrc.provider(), "none")
        self.assertEqual(rec.calls, [])
        self.assertEqual(stats["posts"], 0)
        ev = self.store.q("SELECT message FROM events WHERE message LIKE 'x: skipped%'")
        self.assertTrue(ev)


class TestXApi(XTestBase):
    def test_xapi_timeline_resolves_links(self):  # AC2
        os.environ["X_BEARER_TOKEN"] = "test-token"
        rec = Recorder({"/users/by": {"data": [{"id": "9", "username": "tinylab_ai", "name": "Tiny Lab"}]},
                        "/users/9/tweets": TIMELINE, "/tweets/search/recent": {"data": []}})
        xsrc._request = rec
        eid, _ = self.store.upsert_entity("org", "Tiny Lab", {"x": "tinylab_ai"}, prior=0.6, status="active", via="seed")
        p = Pipeline(store=self.store, log=lambda m: None)
        x_collect.Collector(self.store, p.ingest, p.exp, xsrc.Budget(self.store)).run()
        paper = self.store.item("arxiv:2610.12345")
        self.assertIsNotNone(paper)
        self.assertEqual(paper["metrics"]["x_likes"], 420)
        self.assertEqual(paper["kind"], "paper")
        self.assertIsNotNone(self.store.item("hf:tinylab/photon-7b"))
        self.assertIsNotNone(self.store.item("gh:tinylab/photon"))
        self.assertIsNone(self.store.item("x:113"))                    # short, link-less chatter is ignored
        shares = self.store.q("SELECT * FROM edges WHERE rel='shared' AND src=?", (eid,))
        self.assertEqual(len(shares), 3)
        self.assertIn("2610.12345", self.store.get_meta("pending_arxiv"))
        self.assertTrue(all(c[2]["headers"]["Authorization"] == "Bearer test-token" for c in rec.calls))
        lead = self.store.resolve_handle("x", "coauthor_x")
        self.assertIsNotNone(lead)                                     # mention next to a research link -> lead

    def test_provisional_item_upgraded_by_real_source(self):
        from radar.models import Item
        post = xsrc._post("1", "a", "A", "look at this", "2026-10-23T00:00:00Z",
                          ["https://arxiv.org/abs/2610.99999"], {"likes": 3}, [])
        items, _ = x_collect.post_items(post, None, True)
        self.store.upsert_item(items[0])
        self.store.upsert_item(Item(id="arxiv:2610.99999", kind="paper", title="The Real Title Of The Paper",
                                    url="https://arxiv.org/abs/2610.99999", source="arxiv", summary="abstract"))
        it = self.store.item("arxiv:2610.99999")
        self.assertEqual(it["title"], "The Real Title Of The Paper")
        self.assertEqual(it["metrics"]["x_likes"], 3)
        self.assertEqual(sorted(it["sources"]), ["arxiv", "x"])

    def test_spend_cap_refuses(self):  # AC4
        os.environ["X_BEARER_TOKEN"] = "t"
        b = xsrc.Budget(self.store)
        b.charge(usd=7.995)   # any further call (>= $0.01) would cross the $8 cap
        rec = Recorder({"/tweets/search/recent": TIMELINE})
        xsrc._request = rec
        with self.assertRaises(xsrc.BudgetExceeded):
            xsrc.XApi(b).search("q", "2026-10-20T00:00:00Z", limit=100)   # 100 * $0.005 + users > $0.01 headroom
        self.assertEqual(rec.calls, [])                                    # refused BEFORE the request
        p = Pipeline(store=self.store, log=lambda m: None)
        self.store.upsert_entity("org", "Tiny Lab", {"x": "tinylab_ai"}, prior=0.6, status="active", via="seed")
        x_collect.Collector(self.store, p.ingest, p.exp, b).run()
        self.assertTrue(self.store.q("SELECT 1 FROM events WHERE kind='budget'"))
        self.assertEqual(rec.calls, [])


class TestXai(XTestBase):
    def test_xai_citation_grounding(self):  # AC3
        os.environ["XAI_API_KEY"] = "k"
        text = """```json
[{"id": "201", "url": "https://x.com/res_a/status/201", "author_handle": "res_a", "author_name": "Res A",
  "created_at": "2026-10-23T09:00:00Z", "text": "Our new model", "links": ["https://arxiv.org/abs/2610.22222"]},
 {"id": "202", "url": "https://x.com/res_b/status/202", "author_handle": "res_b", "author_name": "Res B",
  "created_at": "2026-10-23T09:00:00Z", "text": "Invented", "links": ["https://arxiv.org/abs/2610.33333"]}]
```"""
        resp = {"output": [{"type": "message", "content": [{"type": "output_text", "text": text, "annotations": [
                    {"type": "url_citation", "url": "https://x.com/res_a/status/201"}]}]}],
                "usage": {"input_tokens": 1000, "output_tokens": 500,
                          "server_side_tool_usage_details": {"x_posts_fetched": 12}}}
        xsrc._request = Recorder({"api.x.ai": resp})
        b = xsrc.Budget(self.store)
        posts = xsrc.XAi(b).search("frontier", "2026-10-20T00:00:00Z")
        self.assertEqual([p["id"] for p in posts], ["201"])               # 202 was never cited -> dropped
        self.assertAlmostEqual(b.usd, 12 * 0.005 + 1000 / 1e6 * 3.0 + 500 / 1e6 * 15.0, places=6)
        self.assertEqual(xsrc.provider(), "xai")


class TestExpansionAndScoring(XTestBase):
    def test_cofollow_prior(self):  # AC5
        exp = Expander(self.store)
        followed = [{"id": "77", "username": "hidden_gem", "name": "Hidden Gem"}]
        priors = []
        for i in range(3):
            fid, _ = self.store.upsert_entity("person", f"Strong {i}", {"x": f"strong{i}"}, status="active", via="seed")
            self.store.update_entity(fid, alpha=8, beta=2)
            exp.cofollow(self.store.entity(fid), followed)
            priors.append(self.store.entity(self.store.resolve_handle("x", "hidden_gem"))["prior"])
        self.assertTrue(priors[0] < priors[1] < priors[2])
        gem = self.store.entity(self.store.resolve_handle("x", "hidden_gem"))
        self.assertEqual(gem["status"], "candidate")
        self.assertEqual(len(self.store.q("SELECT * FROM edges WHERE dst=? AND rel='follows'", (gem["id"],))), 3)

    def test_attention_signal(self):  # AC6
        from radar.models import Item
        for n in ("arxiv:2610.40001", "arxiv:2610.40002"):
            self.store.upsert_item(Item(id=n, kind="paper", title=f"Paper {n}", url="u", source="arxiv",
                                        published="2026-10-22T00:00:00Z"))
        for i in range(4):
            eid, _ = self.store.upsert_entity("person", f"Researcher {i}", {"x": f"r{i}"}, status="active")
            self.store.update_entity(eid, alpha=6, beta=2, centrality=0.5)
            self.store.edge(eid, "arxiv:2610.40001", "shared", 1, f"x:{i}@2026-10-22T08:00:00Z")
        shared = novelty.attention(self.store, "arxiv:2610.40001", "2026-10-22T00:00:00Z")
        unshared = novelty.attention(self.store, "arxiv:2610.40002", "2026-10-22T00:00:00Z")
        self.assertEqual(unshared, 0.0)
        self.assertGreater(shared, 0.8)
        q = {"semantic": .5, "combinatorial": .5, "emergence": .3, "periphery": .5, "traction": .2, "recency": .8}
        lo, _ = novelty.frontier_score({**q, "attention": unshared}, {}, novelty.PRIOR_WEIGHTS)
        hi, ex = novelty.frontier_score({**q, "attention": shared}, {}, novelty.PRIOR_WEIGHTS)
        self.assertGreater(hi, lo)
        self.assertIn("attention", ex)
        base, _ = novelty.frontier_score(q, {}, novelty.PRIOR_WEIGHTS)   # old items without the field
        self.assertEqual(base, lo)                                         # are not penalised or boosted

    def test_discovery_ignores_generic_links(self):  # F-2
        post = xsrc._post("5", "stranger", "S", "Big news about a new model release today", "2026-10-23T00:00:00Z",
                          ["https://news.example.com/story"], {}, [])
        self.assertEqual(x_collect.post_items(post, None, watched=False)[0], [])
        items, _ = x_collect.post_items(post, "org:tiny-lab", watched=True)
        self.assertEqual(len(items), 1)                                   # watched + AI-looking -> kept
        research = xsrc._post("6", "stranger", "S", "x", None, ["https://github.com/a/b"], {}, [])
        self.assertEqual(x_collect.post_items(research, None, watched=False)[0][0].id, "gh:a/b")

    def test_silent_x_account_demoted(self):  # F-3 / PQ-1
        eid, _ = self.store.upsert_entity("person", "Quiet One", {"x": "quiet"}, status="active", via="x")
        p = Pipeline(store=self.store, log=lambda m: None)
        p.x_polled = [eid]
        for _ in range(10):   # ~10 empty daily polls (about $1 on the X API) before going dormant
            p.expand()
        self.assertEqual(self.store.entity(eid)["status"], "dormant")

    def test_canonical_x_status(self):  # F-7
        self.assertEqual(canonical_id("https://x.com/karpathy/status/1234567890"), "x:1234567890")
        self.assertEqual(canonical_id("https://twitter.com/a_b/status/42?s=20"), "x:42")
        self.assertTrue(canonical_id("https://notx.com/a/status/1").startswith("url:"))

    def test_canonical_hf_model(self):
        self.assertEqual(canonical_id("https://huggingface.co/Qwen/Qwen3-8B"), "hf:qwen/qwen3-8b")
        self.assertTrue(canonical_id("https://huggingface.co/datasets/foo/bar").startswith("url:"))
        self.assertTrue(canonical_id("https://huggingface.co/blog/some-post").startswith("url:"))


if __name__ == "__main__":
    unittest.main()
