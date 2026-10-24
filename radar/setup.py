"""First-run setup: `radar setup` walks through each API key, validates it against the provider's free endpoint
and saves it to a private .env (0600). `radar setup --check` reports status without prompting.

Keys are typed with hidden input, never printed (only `…last4`), never logged, and sent only to their own
provider's validation endpoint. The X API has no free endpoint, so it is validated only on explicit opt-in.
"""
from __future__ import annotations

import getpass
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from .config import ROOT

ENV_PATH = ROOT / ".env"
LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


@dataclass
class Key:
    name: str
    title: str
    unlocks: str
    where: str
    cost: str
    validate: Callable[[str], str]
    paid_check: bool = False


# ---------------------------------------------------------------- validators (ok | invalid | unreachable)

def _get(url: str, headers: Optional[dict] = None, params: Optional[dict] = None) -> int:
    """One unauthenticated-by-default GET; no redirects (a key never follows a redirect), no cache."""
    return httpx.get(url, headers=headers or {}, params=params, timeout=15, follow_redirects=False).status_code


def _verdict(code: int) -> str:
    return "ok" if 200 <= code < 300 else "invalid" if code in (401, 403) else f"unreachable (HTTP {code})"


def _safe(fn: Callable[[], int]) -> str:
    try:
        return _verdict(fn())
    except httpx.HTTPError as exc:
        return f"unreachable ({type(exc).__name__})"


def v_anthropic(key: str) -> str:
    try:
        import anthropic
    except ImportError:
        return "unreachable (anthropic SDK not installed)"
    try:
        anthropic.Anthropic(api_key=key, max_retries=0, timeout=15).models.list(limit=1)
        return "ok"
    except anthropic.AuthenticationError:
        return "invalid"
    except anthropic.PermissionDeniedError:
        return "invalid"
    except anthropic.APIConnectionError:
        return "unreachable (connection)"
    except anthropic.APIStatusError as exc:
        return f"unreachable (HTTP {exc.status_code})"


def v_github(key: str) -> str:
    return _safe(lambda: _get("https://api.github.com/rate_limit", {"Authorization": f"Bearer {key}"}))


def v_s2(key: str) -> str:
    return _safe(lambda: _get("https://api.semanticscholar.org/graph/v1/paper/arXiv:1706.03762",
                              {"x-api-key": key}, {"fields": "title"}))


def v_xai(key: str) -> str:
    return _safe(lambda: _get("https://api.x.ai/v1/models", {"Authorization": f"Bearer {key}"}))


def v_x(key: str) -> str:   # paid: one user lookup (~$0.01)
    return _safe(lambda: _get("https://api.x.com/2/users/by/username/XDevelopers", {"Authorization": f"Bearer {key}"}))


def v_openai(key: str) -> str:
    return _safe(lambda: _get("https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"}))


def v_gemini(key: str) -> str:
    return _safe(lambda: _get("https://generativelanguage.googleapis.com/v1beta/openai/models",
                              {"Authorization": f"Bearer {key}"}))


def v_openrouter(key: str) -> str:
    return _safe(lambda: _get("https://openrouter.ai/api/v1/key", {"Authorization": f"Bearer {key}"}))


def v_hf(key: str) -> str:
    return _safe(lambda: _get("https://huggingface.co/api/whoami-v2", {"Authorization": f"Bearer {key}"}))


def v_openalex(key: str) -> str:
    return _safe(lambda: _get("https://api.openalex.org/works", params={"per-page": 1, "api_key": key}))


KEYS = [
    Key("ANTHROPIC_API_KEY", "Claude (Anthropic)", "LLM reviewer, daily tournament, chat",
        "https://console.anthropic.com/settings/keys",
        "~$1-2/day at defaults. Optional: without it the radar uses your Claude Code login, or quantitative scores only",
        v_anthropic),
    Key("OPENAI_API_KEY", "OpenAI", "use GPT models for review or chat (llm.providers.openai)",
        "https://platform.openai.com/api-keys", "pay-per-token; optional", v_openai),
    Key("GEMINI_API_KEY", "Google Gemini", "use Gemini models for review or chat (llm.providers.gemini)",
        "https://aistudio.google.com/apikey", "free tier + pay-per-token; optional", v_gemini),
    Key("OPENROUTER_API_KEY", "OpenRouter", "one key for hundreds of hosted models (llm.providers.openrouter)",
        "https://openrouter.ai/keys", "provider price + OpenRouter fee; optional", v_openrouter),
    Key("GITHUB_TOKEN", "GitHub", "higher rate limits for repo search, owners, follows",
        "https://github.com/settings/tokens (fine-grained, public read-only is enough)", "free", v_github),
    Key("S2_API_KEY", "Semantic Scholar", "reliable reference/citation snowballing (keyless is heavily rate-limited)",
        "https://www.semanticscholar.org/product/api#api-key-form", "free", v_s2),
    Key("X_BEARER_TOKEN", "X API v2", "X timelines, lists, search and follow graph",
        "https://developer.x.com (create an app, copy its Bearer Token, buy credits)",
        "pay-per-use: ~$0.005/post, $0.010/user; capped by x.daily_usd_cap ($8)", v_x, paid_check=True),
    Key("XAI_API_KEY", "xAI (Grok x_search)", "X search when you don't use the X API",
        "https://console.x.ai", "$5 per 1k posts fetched + tokens; capped by x.daily_usd_cap ($8)", v_xai),
    Key("HF_TOKEN", "Hugging Face", "higher rate limits on papers/models", "https://huggingface.co/settings/tokens",
        "free", v_hf),
    Key("OPENALEX_API_KEY", "OpenAlex", "reserved for OpenAlex enrichment (keys required since Feb 2026)",
        "https://openalex.org (sign up, then API key)", "free daily budget", v_openalex),
]


# ---------------------------------------------------------------- .env io

def mask(value: Optional[str]) -> str:
    if not value:
        return "not set"
    return f"set (…{value[-4:]})" if len(value) >= 12 else "set"


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return re.sub(r"\s+#.*$", "", v)


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if (m := LINE_RE.match(line)) and not line.lstrip().startswith("#"):
                out[m.group(1)] = _unquote(m.group(2))
    return out


def write_env(updates: dict[str, Optional[str]], path: Path = ENV_PATH) -> None:
    """Set (value) or clear (None) keys in place, keeping comments and other lines. Atomic, 0600."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        m = LINE_RE.match(line)
        if m and not line.lstrip().startswith("#") and m.group(1) in pending:
            val = pending.pop(m.group(1))
            lines[i] = f"{m.group(1)}={val or ''}"
    lines += [f"{k}={v}" for k, v in pending.items() if v]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", text=True)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------------- LLM routing (config/local.yaml)

def list_models(profile: dict) -> list[str]:
    """Model ids from an OpenAI-compatible /models endpoint (free). Empty on any failure."""
    from .llm import _oa_headers
    try:
        r = httpx.get(profile["base_url"].rstrip("/") + "/models", headers=_oa_headers(profile), timeout=15,
                      follow_redirects=False)
        r.raise_for_status()
        return sorted(m.get("id", "") for m in r.json().get("data", []) if m.get("id"))
    except (httpx.HTTPError, ValueError, KeyError):
        return []


def write_local(update: dict, path: Optional[Path] = None) -> Path:
    import yaml
    from .config import deep_merge, local_config_path
    path = path or local_config_path()
    current = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = deep_merge(current or {}, update)
    body = "# Personal settings (gitignored). See config/local.example.yaml.\n" + \
        yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)
    path.write_text(body, encoding="utf-8")
    return path


def run_llm_wizard(ask: Callable[[str], str] = input, out=print) -> int:
    from . import config as cfgmod
    from . import llm
    provs = llm.providers()
    names = [n for n in provs if n != "none"]
    out("\nLLM routing: which model reviews items (judge) and which answers chat (ask)?")
    for n in names:
        p = provs[n]
        where = p.get("base_url", "") if p.get("type") == "openai" else p.get("type")
        out(f"  {n:<12} {'ready' if llm.available(p) else 'not configured':<15} {where}")
    out("  custom       add your own OpenAI-compatible endpoint (local GPU box, vLLM, LM Studio, a gateway...)")
    update: dict = {"llm": {}}
    for role in ("judge", "ask"):
        cur = llm.role_config(role).get("provider")
        choice = ask(f"\n{role} provider [{cur}]: ").strip() or cur
        if choice == "custom":
            name = ask("  name for it (e.g. my-gpu): ").strip() or "custom"
            base = ask("  base URL (e.g. http://my-gpu-host:8080/v1): ").strip()
            env = ask("  env var holding its API key (Enter if none): ").strip()
            prof = {"type": "openai", "base_url": base, **({"api_key_env": env} if env else {}), "timeout": 600}
            if _yes(ask, "  Is it a reasoning model whose thinking should be switched off (Qwen3-style)?"):
                prof["extra"] = {"chat_template_kwargs": {"enable_thinking": False}}
            update["llm"].setdefault("providers", {})[name] = prof
            provs[name] = prof
            choice = name
        if choice not in provs:
            out(f"  unknown provider {choice}; keeping {cur}")
            continue
        role_cfg: dict = {"provider": choice}
        prof = provs[choice]
        if prof.get("type") == "openai":
            models = list_models(prof)
            if models:
                out("  models: " + ", ".join(models[:25]) + (" ..." if len(models) > 25 else ""))
            default = prof.get("model") or (models[0] if len(models) == 1 else "")
            m = ask(f"  model [{default or 'type an id'}]: ").strip() or default
            if m:
                role_cfg["model"] = m
        update["llm"][role] = role_cfg
    path = write_local(update)
    cfgmod.reload()
    out(f"\nSaved to {path}. Test it with: radar llm-test --role judge   (and --role ask)")
    return 0


# ---------------------------------------------------------------- check & wizard

def validate(key: Key, value: str, allow_paid: bool) -> str:
    if key.paid_check and not allow_paid:
        return "not validated (the check costs ~$0.01; run `radar setup --only X_BEARER_TOKEN` to test it)"
    return key.validate(value)


def check(out=print, allow_paid: bool = False) -> int:
    from .llm import backend
    from .sources.x import provider
    env = {**read_env(), **{k.name: os.environ[k.name] for k in KEYS if os.environ.get(k.name)}}
    out(f"\nFrontier Radar keys ({ENV_PATH})\n")
    bad = 0
    for k in KEYS:
        value = env.get(k.name)
        result = validate(k, value, allow_paid) if value else "-"
        bad += result == "invalid"
        out(f"  {k.title:<22} {mask(value):<16} {result}")
    mode = ENV_PATH.stat().st_mode & 0o777 if ENV_PATH.exists() else None
    perms = "missing" if mode is None else oct(mode)[2:]
    out(f"\n  LLM: judge → {backend('judge')}, ask → {backend('ask')}   X provider: {provider()}   "
        f".env permissions: {perms}")
    if mode is not None and mode & 0o077:
        out(f"  WARNING: .env is readable by other users. Fix: chmod 600 {ENV_PATH}")
    out("")
    return 1 if bad else 0


def _yes(ask: Callable[[str], str], prompt: str, default: bool = False) -> bool:
    ans = ask(f"{prompt} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not ans else ans in ("y", "yes")


def run_wizard(only: Optional[str] = None, ask: Callable[[str], str] = input,
               secret: Callable[[str], str] = getpass.getpass, out=print, offer_next: bool = True) -> int:
    keys = [k for k in KEYS if not only or k.name == only.upper()]
    if not keys:
        out(f"Unknown key {only}. Known: {', '.join(k.name for k in KEYS)}")
        return 2
    env = read_env()
    out("\nFrontier Radar setup. Input is hidden; press Enter to keep or skip. Keys are saved to .env (private, 0600).\n")
    updates: dict[str, Optional[str]] = {}
    for k in keys:
        current = env.get(k.name)
        out(f"── {k.title} ({k.name})\n   Unlocks: {k.unlocks}\n   Get it:  {k.where}\n   Cost:    {k.cost}\n"
            f"   Now:     {mask(current)}")
        hint = "new value to replace, '-' to remove, Enter to keep" if current else "paste key, or Enter to skip"
        value = secret(f"   {k.name} ({hint}): ").strip()
        if not value:
            out("")
            continue
        if value == "-":
            updates[k.name] = None
            out("   removed\n")
            continue
        paid = k.paid_check and _yes(ask, "   Validate with one paid X API lookup (~$0.01)?")
        result = validate(k, value, paid)
        out(f"   check: {result}")
        if result == "invalid" and not _yes(ask, "   The provider rejected it. Save anyway?"):
            out("")
            continue
        updates[k.name] = value
        out("   saved\n")
    if updates:
        write_env(updates)
        for name, val in updates.items():   # this process sees the new values immediately
            if val:
                os.environ[name] = val
            else:
                os.environ.pop(name, None)
    check(out)
    if not only and _yes(ask, "Choose which models review items and answer chat now?", default=True):
        run_llm_wizard(ask=ask, out=out)
    if offer_next and not only:
        if _yes(ask, "Run the radar now (first run takes a few minutes)?", default=True):
            subprocess.run([sys.executable, "-m", "radar", "run"], cwd=str(ROOT))
        if sys.platform == "darwin" and _yes(ask, "Install the daily 06:30 run and the always-on web app (launchd)?"):
            subprocess.run([str(ROOT / "scripts" / "install_launchd.sh")], cwd=str(ROOT))
        out("Open http://127.0.0.1:7777 (after `radar serve` or the launchd install) and the vault/ folder in Obsidian.")
    return 0
