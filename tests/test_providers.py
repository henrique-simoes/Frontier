"""Provider-agnostic LLM layer + per-user config overlay + publish hygiene (AC1-AC5). No network."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from radar import config as cfgmod
from radar import llm

ROOT = Path(__file__).resolve().parent.parent


class Resp:
    def __init__(self, code, payload=None, lines=None):
        self.status_code, self.payload, self.lines = code, payload, lines or []

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_lines(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def chat(content, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


class WithConfig(unittest.TestCase):
    """Point the overlay at a temp local.yaml for the duration of a test."""
    local = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "local.yaml"
        if self.local is not None:
            self.tmp.write_text(self.local)
        self._env = os.environ.get("RADAR_CONFIG_LOCAL")
        os.environ["RADAR_CONFIG_LOCAL"] = str(self.tmp)
        cfgmod.reload()
        self._post, self._reach = llm._oa_post, llm._reachable
        llm._reachable = lambda url: True        # fixture hosts are fictional; the probe has its own test

    def tearDown(self):
        llm._oa_post, llm._reachable = self._post, self._reach
        if self._env is None:
            os.environ.pop("RADAR_CONFIG_LOCAL", None)
        else:
            os.environ["RADAR_CONFIG_LOCAL"] = self._env
        cfgmod.reload()


class TestOverlay(WithConfig):
    local = """
llm:
  providers:
    box: {type: openai, base_url: "http://box:8080/v1", model: m-local}
  judge: {provider: box}
x: {daily_usd_cap: 1.5}
"""

    def test_overlay_merge(self):  # AC1
        c = cfgmod.config()
        self.assertEqual(c.get("x.daily_usd_cap"), 1.5)                  # overridden
        self.assertEqual(c.get("x.max_post_reads_per_run"), 1000)        # sibling default kept
        self.assertEqual(c.get("llm.judge.provider"), "box")
        self.assertEqual(c.get("llm.judge.effort"), "low")               # merged, not replaced
        self.assertIn("openai", c.get("llm.providers"))                  # shared providers kept
        merged = cfgmod.merge_seeds({"orgs": [{"name": "A", "github": "a"}]},
                                    {"orgs": [{"name": "A", "x": "a_x"}, {"name": "B"}], "people": [{"name": "P"}]})
        self.assertEqual(merged["orgs"], [{"name": "A", "github": "a", "x": "a_x"}, {"name": "B"}])
        self.assertEqual(merged["people"], [{"name": "P"}])

    def test_role_model_comes_from_provider(self):
        name, prof, rc = llm.resolve("judge")
        self.assertEqual(name, "box")
        self.assertEqual(llm._model(prof, rc), "m-local")                # no leak of the anthropic default model


class TestResolution(WithConfig):
    local = """
llm:
  providers:
    keyed: {type: openai, base_url: "https://api.example.com/v1", api_key_env: RADAR_TEST_MISSING_KEY, model: m}
  judge: {provider: keyed}
  fallback: [none]
"""

    def test_role_resolution_fallback(self):  # AC2
        os.environ.pop("RADAR_TEST_MISSING_KEY", None)
        self.assertEqual(llm.backend("judge"), "none")                   # key missing -> fallback chain
        os.environ["RADAR_TEST_MISSING_KEY"] = "k"
        try:
            self.assertEqual(llm.backend("judge"), "keyed")
            self.assertEqual(llm._oa_headers(llm.providers()["keyed"])["Authorization"], "Bearer k")
        finally:
            os.environ.pop("RADAR_TEST_MISSING_KEY")


class TestOpenAICompat(WithConfig):
    local = """
llm:
  providers:
    box:
      type: openai
      base_url: "http://box:8080/v1"
      model: m-local
      extra: {chat_template_kwargs: {enable_thinking: false}}
  judge: {provider: box}
  ask: {provider: box}
"""

    def test_openai_structured_fallbacks(self):  # AC3
        bodies = []

        def post(profile, body, stream=False):
            bodies.append(body)
            if body.get("response_format", {}).get("type") == "json_schema":
                return Resp(400, {"error": "unsupported"})
            return Resp(200, chat('<think>hmm</think>{"ranking": ["b", "a"]}'))

        llm._oa_post = post
        out = llm._structured("sys", "rank", llm.RANK_SCHEMA, "judge")
        self.assertEqual(out, {"ranking": ["b", "a"]})
        self.assertEqual([b.get("response_format", {}).get("type") for b in bodies], ["json_schema", "json_object"])
        self.assertEqual(bodies[0]["chat_template_kwargs"], {"enable_thinking": False})   # extra merged
        self.assertEqual(bodies[0]["model"], "m-local")
        self.assertIn("JSON Schema", bodies[1]["messages"][-1]["content"])

    def test_length_cutoff_is_reported(self):
        llm._oa_post = lambda profile, body, stream=False: Resp(200, chat('{"rank', finish="length"))
        with self.assertRaisesRegex(RuntimeError, "max_tokens"):
            llm._structured("sys", "rank", llm.RANK_SCHEMA, "judge")

    def test_openai_stream(self):  # AC4
        def ev(**delta):
            return "data: " + json.dumps({"choices": [{"delta": delta}]})
        lines = [ev(reasoning_content="secret plan"), ev(content="<thi"), ev(content="Hello <think>internal"),
                 ev(content=" still</think> world"), ev(content=" [arxiv:1]"), "", "data: [DONE]", ev(content="late")]
        llm._oa_post = lambda profile, body, stream=False: Resp(200, lines=lines)
        out = "".join(llm.ask_stream("q", "ctx"))
        self.assertNotIn("secret", out)
        self.assertNotIn("internal", out)
        self.assertNotIn("late", out)
        self.assertIn("Hello", out)
        self.assertIn("world [arxiv:1]", out)

    def test_offline_selfhosted_falls_back(self):  # F-1
        llm._reachable = self._reach              # the real probe
        llm._reachable.cache_clear()
        prof = {"type": "openai", "base_url": "http://127.0.0.1:9/v1", "model": "m"}   # discard port: refused
        self.assertFalse(llm.available(prof))
        self.assertTrue(llm.available({"type": "openai", "base_url": "https://x/v1", "api_key_env": "HOME"}))

    def test_normalize_scores(self):
        self.assertEqual(llm.normalize_scores({"novelty": 0.8, "rigor": 0.5, "impact": 0.3})["novelty"], 8.0)
        self.assertEqual(llm.normalize_scores({"novelty": 7, "rigor": 0.5, "impact": 3})["rigor"], 0.5)
        self.assertEqual(llm.normalize_scores({"novelty": 14, "rigor": 5, "impact": 5})["novelty"], 10.0)


class TestPublishHygiene(unittest.TestCase):
    def test_gitignore_covers_personal(self):  # AC5
        paths = ["config/local.yaml", "config/seeds.local.yaml", ".env", "data/radar.db", "vault/Items/2026-10/x.md",
                 "vault/Entities/People/x.md", "vault/Daily/2026-10-24.md", ".compass-forge/config.json",
                 "recipes/fastapi/recipe.toml", "vault/.obsidian/workspace.json"]
        r = subprocess.run(["git", "check-ignore", "--no-index", *paths], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(sorted(r.stdout.split()), sorted(paths))
        keep = ["config/local.example.yaml", ".env.example", "vault/Home.md", "vault/.obsidian/core-plugins.json",
                "AGENTS.md", ".agents/skills/radar-ask/SKILL.md"]
        r = subprocess.run(["git", "check-ignore", "--no-index", *keep], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
