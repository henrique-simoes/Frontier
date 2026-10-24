"""Offline tests: store merge/dedupe, novelty signals, scoring, calibration, expansion, vault round-trip.
Run: .venv/bin/python -m unittest discover -s tests -v"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="radar-test-"))
ROOT = Path(__file__).resolve().parent.parent
shutil.copytree(ROOT / "config", TMP / "config", ignore=shutil.ignore_patterns("local.yaml", "*.local.yaml"))
cfg_text = (TMP / "config" / "radar.yaml").read_text()
(TMP / "config" / "radar.yaml").write_text(cfg_text.replace("provider: anthropic", "provider: none")
                                           .replace("macos: true", "macos: false"))
os.environ["RADAR_ROOT"] = str(TMP)
os.environ["RADAR_CONFIG_LOCAL"] = str(TMP / "config" / "local.yaml")   # never the developer's own overlay

from radar import novelty  # noqa: E402
from radar.calibrate import LABEL_VALUE, fit  # noqa: E402
from radar.embed import embed  # noqa: E402
from radar.expand import Expander  # noqa: E402
from radar.models import Author, Item  # noqa: E402
from radar.pipeline import Pipeline  # noqa: E402
from radar.store import Store  # noqa: E402
from radar.util import canonical_id  # noqa: E402
from radar.vault import Vault  # noqa: E402


def paper(n, title, summary="", authors=("Ada Lovelace",), pub="2026-10-20T00:00:00Z", **kw):
    return Item(id=f"arxiv:2610.{n:05d}", kind="paper", title=title, url=f"https://arxiv.org/abs/2610.{n:05d}",
                source="test", summary=summary, published=pub, authors=[Author(a) for a in authors], **kw)


class TestUtil(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(canonical_id("https://arxiv.org/pdf/2610.31667v2"), "arxiv:2610.31667")
        self.assertEqual(canonical_id("https://huggingface.co/papers/2610.31667"), "arxiv:2610.31667")
        self.assertEqual(canonical_id("https://github.com/Foo/Bar.git"), "gh:foo/bar")
        self.assertTrue(canonical_id("https://example.com/post/").startswith("url:"))


class TestStoreAndScoring(unittest.TestCase):
    def setUp(self):
        self.store = Store(TMP / f"t{self._testMethodName}.db")

    def test_merge_sources(self):
        a = paper(1, "Sparse Mixture Routing for Tiny Models", "abc")
        b = paper(1, "Sparse Mixture Routing for Tiny Models", "longer abstract text", metrics={"upvotes": 12})
        b.source = "hf_papers"
        self.store.upsert_item(a)
        iid, new = self.store.upsert_item(b)
        self.assertFalse(new)
        it = self.store.item(iid)
        self.assertEqual(sorted(it["sources"]), ["hf_papers", "test"])
        self.assertEqual(it["metrics"]["upvotes"], 12)
        self.assertEqual(it["summary"], "longer abstract text")

    def test_title_dedupe_across_ids(self):
        self.store.upsert_item(paper(2, "A Very Specific Title About Photonic Attention Kernels"))
        other = Item(id="url:abc", kind="post", title="A Very Specific Title About Photonic Attention Kernels",
                     url="https://x.y/z", source="rss")
        iid, new = self.store.upsert_item(other)
        self.assertEqual(iid, "arxiv:2610.00002")
        self.assertFalse(new)

    def test_semantic_novelty_ranks_outlier(self):
        p = Pipeline(store=self.store, log=lambda m: None)
        for i in range(40):
            p.ingest(paper(100 + i, f"Instruction tuning large language models variant {i}",
                           "We instruction tune a large language model on chat data and evaluate on benchmarks.",
                           pub=f"2026-10-{(i % 9) + 10:02d}T00:00:00Z"))
        p.ingest(paper(200, "Photonic reservoir computing for protein folding on optical chips",
                       "We build an optical reservoir computer that predicts protein contact maps from sequence.",
                       pub="2026-10-22T00:00:00Z"))
        p.ingest(paper(201, "Instruction tuning large language models variant 99",
                       "We instruction tune a large language model on chat data and evaluate on benchmarks.",
                       pub="2026-10-22T00:00:00Z"))
        p.embed_new()
        p.score_quant()
        out = self.store.item("arxiv:2610.00200")["quant"]["semantic_raw"]
        dup = self.store.item("arxiv:2610.00201")["quant"]["semantic_raw"]
        self.assertGreater(out, dup + 0.3)

    def test_frontier_score_monotone_in_llm_novelty(self):
        w = novelty.PRIOR_WEIGHTS
        q = {"semantic": .5, "combinatorial": .5, "emergence": .3, "periphery": .5, "traction": .2, "recency": .8}
        lo, _ = novelty.frontier_score(q, {"novelty": 2, "rigor": 5, "impact": 2, "novelty_class": "incremental"}, w)
        hi, ex = novelty.frontier_score(q, {"novelty": 9, "rigor": 7, "impact": 9, "novelty_class": "new_paradigm"}, w)
        self.assertGreater(hi, lo + 25)
        self.assertIn("llm_novelty", ex)

    def test_combinatorial_rare_pair(self):
        p = Pipeline(store=self.store, log=lambda m: None)
        for i in range(60):
            c = ["alpha", "beta"] if i % 2 else ["gamma", "delta"]
            novelty.record_concepts(self.store, f"x{i}", c, "2026-10-01T00:00:00Z")
        common = novelty.combinatorial(self.store, ["alpha", "beta"])
        rare = novelty.combinatorial(self.store, ["alpha", "gamma"])
        self.assertGreater(rare, common)

    def test_calibration_learns_from_labels(self):
        for i in range(40):
            good = i % 2 == 0
            q = {"semantic": 0.9 if good else 0.1, "combinatorial": .5, "emergence": .3, "periphery": .5,
                 "traction": .2, "recency": .8}
            self.store.upsert_item(paper(300 + i, f"Calibration item number {i} with distinct words {i * 7}"))
            self.store.update_item(f"arxiv:2610.{300 + i:05d}", quant=q)
            label = "frontier" if good else "incremental"
            self.store.feedback(f"arxiv:2610.{300 + i:05d}", label, LABEL_VALUE[label])
        self.store.commit()
        rep = fit(self.store)
        self.assertIn(rep["status"], ("updated", "rejected"))
        if rep["status"] == "updated":
            self.assertGreater(rep["weights"]["semantic"], novelty.PRIOR_WEIGHTS["semantic"])


class TestExpansion(unittest.TestCase):
    def setUp(self):
        self.store = Store(TMP / f"e{self._testMethodName}.db")
        self.exp = Expander(self.store)

    def test_seed_and_select(self):
        n = self.exp.seed()
        self.assertGreater(n, 50)
        picked = self.exp.select(10)
        self.assertEqual(len(picked), 10)

    def test_discovery_prior_decays_with_depth(self):
        self.store.upsert_item(paper(400, "Frontier thing", authors=("Grace Hopper",)))
        it = self.store.item("arxiv:2610.00400")
        it["novelty"] = 90
        self.exp.discover_from(it)
        e = self.store.entity("person:grace-hopper")
        self.assertEqual(e["depth"], 1)
        self.assertAlmostEqual(e["prior"], 0.9, places=2)
        self.store.update_entity(e["id"], depth=3)
        self.store.upsert_item(paper(401, "Another frontier thing", authors=("Alan Turing",)))
        it2 = self.store.item("arxiv:2610.00401")
        it2.update(novelty=90, via_entity=e["id"])
        self.exp.discover_from(it2)
        self.assertLess(self.store.entity("person:alan-turing")["prior"], 0.9 * 0.7 ** 2 + 1e-6)

    def test_yield_promotes_and_demotes(self):
        eid, _ = self.store.upsert_entity("org", "Tiny Lab", {"github": "tinylab"}, via="x")
        self.exp.record_yield(eid, 3, 1)
        self.assertEqual(self.store.entity(eid)["status"], "active")
        for _ in range(6):
            self.exp.record_yield(eid, 10, 0)
        self.assertEqual(self.store.entity(eid)["status"], "dormant")


class TestVault(unittest.TestCase):
    def test_roundtrip_preserves_user_text_and_reads_rating(self):
        store = Store(TMP / "v.db")
        store.upsert_item(paper(500, "Vault roundtrip paper"))
        store.update_item("arxiv:2610.00500", novelty=77.0, quant={"semantic": .8}, explain={"semantic": .9})
        v = Vault(store)
        rel = v.write_item(store.item("arxiv:2610.00500"))
        path = v.root / rel
        text = path.read_text().replace("rating: null", "rating: frontier") + "\nMy own insight.\n"
        path.write_text(text)
        v.write_item(store.item("arxiv:2610.00500"))
        new = path.read_text()
        self.assertIn("My own insight.", new)
        self.assertIn("rating: frontier", new)
        self.assertEqual(v.sync_ratings(), 1)


if __name__ == "__main__":
    unittest.main()
