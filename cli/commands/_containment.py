"""Containment detection + fail-closed git guard.

A contained PM runs under the capability floor: no shell, no raw file
write/edit, no process exec, no product-repo / work-root reach — it
touches the project only through the fixed Cartopian MCP toolset. The
protocol setting ``git.pm_owns_product_branches = true`` promises the PM owns
product-repo git plumbing (branch / stage / commit / push / ``gh pr ...``),
which requires exactly the shell + product-repo access the floor removes.

Until mediated-git commands exist, that combination is an UNSUPPORTED
contradiction. The selected behavior is to **fail closed**: refuse PM launch /
lifecycle entry with a structured ``[guard]`` line and a non-zero exit, so the
PM is never silently left running a setting it cannot honor.

This module is import-cycle-free (stdlib + nothing from the command modules)
so the config-resolution surface (``resolve_config``) and the orientation
aggregator (``next_action``) can both apply the guard.
"""
import os
import tomllib
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

# Deterministic containment signal. The contained PM launch profile sets this
# on the Cartopian MCP server process (wrappers/etc/mcp-cartopian-only.json),
# where the CLI handlers run in-process, and the launch wrapper also exports it.
# Absent (the default for an operator shell / uncontained PM) → not contained,
# so legacy behavior is unchanged (NF-004).
CONTAINMENT_ENV = "CARTOPIAN_PM_CONTAINED"
_TRUTHY = {"1", "true", "yes", "on"}


def pm_is_contained(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Return True iff the PM is running under the containment launch profile."""
    env = environ if environ is not None else os.environ
    return env.get(CONTAINMENT_ENV, "").strip().lower() in _TRUTHY


def resolve_pm_owns_product_branches(
    global_cfg: Optional[Dict[str, Any]], project_cfg: Optional[Dict[str, Any]]
) -> bool:
    """Resolve effective ``git.pm_owns_product_branches`` from parsed configs.

    Resolution order (project > global > protocol default false), matching
    ``plan_audit._resolve_pm_owns_product_branches``. Independent of
    ``git_versioning`` — the setting lives in the ``[git]`` table regardless.
    """
    p_git = (project_cfg or {}).get("git", {}) or {}
    if "pm_owns_product_branches" in p_git:
        return bool(p_git["pm_owns_product_branches"])
    g_git = (global_cfg or {}).get("git", {}) or {}
    if "pm_owns_product_branches" in g_git:
        return bool(g_git["pm_owns_product_branches"])
    return False


def _safe_load_toml(path: Path) -> Dict[str, Any]:
    """Load a TOML file; return {} on missing/unreadable/malformed (fail-soft).

    The guard resolves the effective setting from configs that
    ``resolve_config`` / ``next_action`` have already validated, so unreadable
    config is handled (and reported) by those surfaces; here we only need the
    git setting and must not raise.
    """
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def resolve_pm_owns_from_paths(project_path: Path, home: Optional[Path] = None) -> bool:
    """Resolve effective ``pm_owns_product_branches`` from on-disk configs."""
    home = home or Path.home()
    project_cfg = _safe_load_toml(project_path / "cartopian.toml")
    global_cfg = _safe_load_toml(home / ".cartopian" / "cartopian.toml")
    return resolve_pm_owns_product_branches(global_cfg, project_cfg)


def unsupported_combination_message() -> str:
    """The structured, deterministic guard message (no ``[guard]`` prefix)."""
    return (
        "unsupported-combination: git.pm_owns_product_branches=true under a "
        "contained PM. The capability floor gives the contained PM no shell to "
        "run git/gh, and mediated-git is not yet implemented, so the PM cannot "
        "perform the product-repo git plumbing this setting promises. Refusing "
        "PM launch fail-closed — no lifecycle action proceeds. Resolve by "
        "setting git.pm_owns_product_branches=false or launching the PM "
        "uncontained until mediated-git lands."
    )


def contained_pm_owned_git_block_message(
    pm_owns_product_branches: bool, contained: bool
) -> Optional[str]:
    """Return the guard message if the unsupported combo holds, else None."""
    if pm_owns_product_branches and contained:
        return unsupported_combination_message()
    return None
