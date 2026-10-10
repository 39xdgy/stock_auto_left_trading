"""Load config.toml, overlaid by config.local.toml when present."""

import tomllib
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent


def _merge(base, override):
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _to_ns(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in value.items()})
    return value


def load(path=None, overrides=None):
    path = Path(path) if path else ROOT / "config.toml"
    with open(path, "rb") as f:
        data = tomllib.load(f)
    local = path.with_name("config.local.toml")
    if local.exists():
        with open(local, "rb") as f:
            data = _merge(data, tomllib.load(f))
    if overrides:
        data = _merge(data, overrides)
    return _to_ns(data)
