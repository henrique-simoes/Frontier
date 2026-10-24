from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(os.environ.get("RADAR_ROOT", Path(__file__).resolve().parent.parent))


def _load(name: str, path: Optional[Path] = None) -> dict:
    path = path or ROOT / "config" / name
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def deep_merge(base: dict, over: dict) -> dict:
    """Recursively merge `over` into a copy of `base`. Dicts merge; anything else (lists included) is replaced."""
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def merge_seeds(base: dict, extra: dict) -> dict:
    """Personal seeds append to the shared lists; an entry with the same name replaces the shared one."""
    out = {k: list(v or []) for k, v in (base or {}).items()}
    for group, items in (extra or {}).items():
        names = {e.get("name"): i for i, e in enumerate(out.setdefault(group, []))}
        for e in items or []:
            if e.get("name") in names:
                out[group][names[e["name"]]] = {**out[group][names[e["name"]]], **e}
            else:
                out[group].append(e)
    return out


def local_config_path() -> Path:
    return Path(os.environ.get("RADAR_CONFIG_LOCAL", ROOT / "config" / "local.yaml"))


def load_dotenv(path: Optional[Path] = None) -> int:
    """Load KEY=VALUE lines from .env into os.environ without overriding anything already set.
    Returns how many variables were added."""
    path = path or ROOT / ".env"
    if not path.exists():
        return 0
    added = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        if key.isidentifier() and value and key not in os.environ:
            os.environ[key] = value
            added += 1
    return added


class Config:
    """Shared defaults (config/*.yaml, committed) overlaid by personal settings (gitignored):
    radar.yaml <- local.yaml (or $RADAR_CONFIG_LOCAL), seeds.yaml + seeds.local.yaml, taxonomy.yaml <- taxonomy.local.yaml.
    Dotted lookups: cfg.get("llm.judge.provider")."""

    def __init__(self) -> None:
        self.data = deep_merge(_load("radar.yaml"), _load("local.yaml", local_config_path()))
        self.seeds = merge_seeds(_load("seeds.yaml"), _load("seeds.local.yaml"))
        self.taxonomy = deep_merge(_load("taxonomy.yaml"), _load("taxonomy.local.yaml"))

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, key: str) -> Path:
        p = Path(self.get(f"paths.{key}"))
        return p if p.is_absolute() else ROOT / p

    @property
    def vault_name(self) -> str:
        return self.path("vault").name


_cfg: Config | None = None


def config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg


def reload() -> Config:
    """Re-read config after `radar setup` writes local.yaml."""
    global _cfg
    _cfg = Config()
    return _cfg
