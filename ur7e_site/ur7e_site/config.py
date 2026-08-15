"""Loader for ``config/site.yaml``.

Resolution order for the config file:

1. ``$UR7E_SITE_CONFIG`` (absolute path) -- lets you keep several profiles side
   by side and pick one per shell, e.g. ``UR7E_SITE_CONFIG=config/site.real.yaml``.
2. ``<repo>/config/site.yaml`` -- found by walking up from this file. Works
   because this package is always installed editable from the repo checkout.
3. Built-in defaults (below) -- so ``--help`` and unit tests never explode just
   because the file is missing.

Design rule: **loading must never raise.** These values feed dataclass
``default_factory`` hooks in the plugin configs, which run at import time; an
exception there would break every ``lerobot-*`` command including ``--help``.
A malformed file therefore degrades to defaults with a warning.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_ENV_VAR = "UR7E_SITE_CONFIG"

#: Values used when no site file can be read. They describe the URSim setup,
#: which is the configuration this repository was developed against.
DEFAULTS: dict[str, Any] = {
    "profile": "ursim",
    "robot": {
        "ip": "192.168.56.101",
        "use_gripper": False,
        "rtde_frequency": 500.0,
        # PolyScope X needs the External Control URCapX; PolyScope 5 does not.
        "use_external_control_urcap": True,
        "external_control": {"host_ip": "192.168.56.1", "port": 50002},
        "servoj": {
            "acceleration": 0.5,
            "speed": 0.5,
            "time_s": 0.008,
            "lookahead_time_s": 0.2,
            "gain": 300,
        },
        "cameras": {},
    },
    "teleop": {
        "gello": {
            "port": "/dev/ttyUSB0",
            "baudrate": 57600,
            "mock": False,
        }
    },
    "record": {"fps": 30},
}

_cache: dict[str, Any] | None = None
_cache_path: Path | None = None


def _repo_root() -> Path:
    """Repo checkout root, derived from this file's location."""
    # <repo>/ur7e_site/ur7e_site/config.py -> parents[2] == <repo>
    return Path(__file__).resolve().parents[2]


def site_path() -> Path | None:
    """Path of the site config that would be loaded, or ``None`` if absent."""
    from_env = os.environ.get(CONFIG_ENV_VAR)
    if from_env:
        p = Path(from_env).expanduser()
        return p if p.is_file() else None

    p = _repo_root() / "config" / "site.yaml"
    return p if p.is_file() else None


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Overlay wins, but keys absent from the overlay keep the base value."""
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict[str, Any]:
    """Return the merged site config (cached)."""
    global _cache, _cache_path
    if _cache is not None:
        return _cache

    path = site_path()
    data: dict[str, Any] = {}
    if path is not None:
        try:
            import yaml

            with open(path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                data = loaded
            else:
                logger.warning("%s is not a YAML mapping; ignoring it.", path)
        except Exception:
            # Never propagate: dataclass defaults are evaluated at import time.
            logger.warning("Could not read site config %s; using defaults.", path, exc_info=True)

    _cache = _deep_merge(DEFAULTS, data)
    _cache_path = path
    return _cache


def reload() -> dict[str, Any]:
    """Drop the cache and re-read the file. Used by tests and long-lived shells."""
    global _cache, _cache_path
    _cache = None
    _cache_path = None
    return load()


def get(dotted_key: str, default: Any = None) -> Any:
    """Look up ``"robot.external_control.port"`` style keys.

    Returns ``default`` when any segment is missing, so callers can add new
    settings without forcing every existing site file to be updated.
    """
    node: Any = load()
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def resolve_path(value: str | os.PathLike[str] | None) -> Path | None:
    """Resolve a possibly relative path from the site file against the repo root."""
    if value is None:
        return None
    p = Path(value).expanduser()
    return p if p.is_absolute() else (_repo_root() / p)


def describe() -> str:
    """One-line summary used by scripts to make the active profile visible."""
    load()
    where = _cache_path if _cache_path is not None else "<built-in defaults>"
    return (
        f"site profile={get('profile')} robot.ip={get('robot.ip')} "
        f"ext_ctrl={get('robot.use_external_control_urcap')}@"
        f"{get('robot.external_control.host_ip')}:{get('robot.external_control.port')} "
        f"(from {where})"
    )
