"""Top-level shim.

Every package in this repo uses a ``<pkg>/<pkg>/`` layout, so when a command is
run from the repo root the outer directory shadows the installed package on
``sys.path``. Without this shim ``ur7e_site`` would resolve to a namespace
package with no attributes, and every plugin config would fail to import --
but only when the cwd happens to be the repo root, which is the confusing part.

Re-exporting here makes both import paths equivalent.
"""

from .ur7e_site import CONFIG_ENV_VAR, get, load, reload, resolve_path, site_path

__all__ = ["CONFIG_ENV_VAR", "get", "load", "reload", "resolve_path", "site_path"]
