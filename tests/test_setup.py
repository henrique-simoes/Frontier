"""radar setup: .env handling, masking, validators, opt-in paid check (AC1-AC5). No network."""
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path

from radar import setup
from radar.config import load_dotenv

SECRET = "sk-test-0123456789abcdefWXYZ"


class TestEnvFile(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / ".env"

    def test_write_env_preserves_and_chmods(self):  # AC1
        self.path.write_text("# my comment\nGITHUB_TOKEN=old\nCUSTOM=keep me\nexport S2_API_KEY='q'\n")
        os.chmod(self.path, 0o644)
        setup.write_env({"GITHUB_TOKEN": "new", "HF_TOKEN": "hf_x", "S2_API_KEY": None}, self.path)
        text = self.path.read_text()
        self.assertIn("# my comment", text)
        self.assertIn("CUSTOM=keep me", text)
        self.assertIn("GITHUB_TOKEN=new", text)
        self.assertNotIn("old", text)
        self.assertIn("HF_TOKEN=hf_x", text)
        self.assertEqual(setup.read_env(self.path).get("S2_API_KEY"), "")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.dir.iterdir()], [".env"])      # no temp file left behind

    def test_dotenv_never_overrides(self):  # AC2
        self.path.write_text("RADAR_T_A=from_file\nRADAR_T_B='quoted value'\nRADAR_T_C=x # note\n# RADAR_T_D=no\n")
        os.environ["RADAR_T_A"] = "from_env"
        try:
            n = load_dotenv(self.path)
            self.assertEqual(os.environ["RADAR_T_A"], "from_env")
            self.assertEqual(os.environ["RADAR_T_B"], "quoted value")
            self.assertEqual(os.environ["RADAR_T_C"], "x")
            self.assertNotIn("RADAR_T_D", os.environ)
            self.assertEqual(n, 2)
        finally:
            for k in ("RADAR_T_A", "RADAR_T_B", "RADAR_T_C"):
                os.environ.pop(k, None)


class TestNoLeaks(unittest.TestCase):
    def test_mask_and_check_output_has_no_secret(self):  # AC3
        self.assertEqual(setup.mask(SECRET), "set (…WXYZ)")
        self.assertEqual(setup.mask("short"), "set")
        self.assertEqual(setup.mask(None), "not set")
        buf = io.StringIO()
        old_env, old_validate = setup.ENV_PATH, [k.validate for k in setup.KEYS]
        try:
            setup.ENV_PATH = Path(tempfile.mkdtemp()) / ".env"
            setup.write_env({"GITHUB_TOKEN": SECRET}, setup.ENV_PATH)
            for k in setup.KEYS:
                k.validate = lambda v: "ok"
            setup.check(out=lambda s: buf.write(s + "\n"))
        finally:
            setup.ENV_PATH = old_env
            for k, v in zip(setup.KEYS, old_validate):
                k.validate = v
        self.assertNotIn(SECRET, buf.getvalue())
        self.assertNotIn(SECRET[:-4], buf.getvalue())

    def test_wizard_never_echoes_input(self):
        buf, saved = io.StringIO(), {}
        old_write, old_check = setup.write_env, setup.check
        setup.write_env = lambda updates, path=None: saved.update(updates)
        setup.check = lambda out=print, allow_paid=False: 0
        try:
            gh = next(k for k in setup.KEYS if k.name == "GITHUB_TOKEN")
            orig = gh.validate
            gh.validate = lambda v: "ok"
            setup.run_wizard(only="GITHUB_TOKEN", ask=lambda p: "n", secret=lambda p: SECRET,
                             out=lambda s: buf.write(s + "\n"), offer_next=False)
        finally:
            setup.write_env, setup.check, gh.validate = old_write, old_check, orig
            os.environ.pop("GITHUB_TOKEN", None)
        self.assertEqual(saved, {"GITHUB_TOKEN": SECRET})
        self.assertNotIn(SECRET, buf.getvalue())


class TestValidators(unittest.TestCase):
    def setUp(self):
        self._get = setup._get
        self.calls = []

    def tearDown(self):
        setup._get = self._get

    def stub(self, code):
        def f(url, headers=None, params=None):
            self.calls.append(url)
            return code
        setup._get = f

    def test_validators_map_status(self):  # AC4
        for code, want in ((200, "ok"), (401, "invalid"), (403, "invalid"), (500, "unreachable (HTTP 500)")):
            self.stub(code)
            self.assertEqual(setup.v_github("t"), want)
        self.stub(200)
        for fn, host in ((setup.v_github, "api.github.com"), (setup.v_s2, "api.semanticscholar.org"),
                         (setup.v_xai, "api.x.ai"), (setup.v_hf, "huggingface.co"), (setup.v_openalex, "api.openalex.org")):
            fn("t")
            self.assertIn(host, self.calls[-1])

    def test_x_validation_is_opt_in(self):  # AC5
        self.stub(200)
        xkey = next(k for k in setup.KEYS if k.name == "X_BEARER_TOKEN")
        self.assertTrue(setup.validate(xkey, "t", allow_paid=False).startswith("not validated"))
        self.assertEqual(self.calls, [])
        self.assertEqual(setup.validate(xkey, "t", allow_paid=True), "ok")
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
