"""Site configuration for the UR7e x LeRobot x GELLO stack.

Everything that differs between the URSim box and the real-robot box lives in
one YAML file. Nothing in the plugin code hard-codes an IP, a serial path or a
camera index; they all read through :func:`get`.

Switching from URSim to the real UR7e is therefore a single-file edit
(``config/site.yaml``) -- see ``docs/MIGRATION.md``.
"""

from .config import CONFIG_ENV_VAR, get, load, reload, resolve_path, site_path

__all__ = ["CONFIG_ENV_VAR", "get", "load", "reload", "resolve_path", "site_path"]
