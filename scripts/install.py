#!/usr/bin/env python3
"""Cartopian install / upgrade script.

Installs or upgrades a Cartopian tree at ``~/.cartopian/`` (or
``%USERPROFILE%\\.cartopian`` on native Windows) from a cloned source repo.

Mechanics follow the STANDARDS.md "Build / Distribution" install-behavior
table: tool-shipped paths (``protocol/``, ``templates/``, ``skills/``,
``wrappers/``, ``bin/cartopian``, ``bin/cartopian.cmd``, ``cli/``,
``CHANGELOG.md``) are symlinked or copied from the source repo and
replaced on upgrade; operator-owned paths (``cartopian.toml``,
``projects.json``) are seeded on first install and never overwritten
thereafter.

``bin/cartopian.cmd`` is the native-Windows PATH shim that forwards to
the extensionless ``bin/cartopian`` Python entrypoint via the system
``python``. It is installed on every platform (harmless on Unix, where
``.cmd`` files are not in ``PATHEXT``); the install root is single-tree
across platforms and tests assume the shim is present.

The canonical V1 install path is a manual ``git clone + symlink``; this
script is a zero-extra-dependency helper that performs that flow uniformly
across platforms. It requires Python 3.11+ and invokes no package managers
or ``pip install``.

Re-running the script after ``git pull`` in the source repo is the
upgrade flow: symlink targets resolve automatically; copies are
refreshed in place; operator-owned files are preserved.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_BAD_PYTHON = 3

# Tool-shipped paths: (target_name_in_install_root, source_path_in_repo).
# "replace on upgrade" — these are re-created every install run.
TOOL_SHIPPED: Tuple[Tuple[str, str], ...] = (
    ("protocol", "protocol"),
    ("templates", "templates"),
    ("skills", "skills"),
    ("wrappers", "wrappers"),
    ("cli", "cli"),
    ("mcp_server", "mcp_server"),
    ("bin/cartopian", "bin/cartopian"),
    ("bin/cartopian.cmd", "bin/cartopian.cmd"),
    ("bin/cartopian-mcp", "bin/cartopian-mcp"),
    ("bin/cartopian-mcp.cmd", "bin/cartopian-mcp.cmd"),
    ("install-cartopian.md", "install-cartopian.md"),
    ("CHANGELOG.md", "protocol/CHANGELOG.md"),
)

# CHANGELOG.md is documented as "copy" (not "copy or symlink") in the
# install-behavior table — keep it a real copy even in symlink mode so
# upgrades replace its content rather than chasing a symlink that already
# lives inside ``protocol/``.
COPY_ALWAYS = frozenset({"CHANGELOG.md"})

# Operator-owned paths: seeded only if absent, never overwritten.
OPERATOR_TOML = "cartopian.toml"
OPERATOR_REGISTRY = "projects.json"
GLOBAL_TOML_TEMPLATE = "templates/global.cartopian.toml"
EMPTY_REGISTRY = "[]\n"  # empty registry initialised as a JSON empty array.

MIN_PYTHON = (3, 11)


def default_install_root() -> Path:
    """Return the default install root for the current platform.

    macOS / Linux / WSL: ``$HOME/.cartopian``
    Native Windows (PowerShell, cmd): ``%USERPROFILE%\\.cartopian``
    """
    if os.name == "nt":
        base = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    else:
        base = os.environ.get("HOME") or os.path.expanduser("~")
    return Path(base) / ".cartopian"


def _eprint(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)


def _require_python() -> None:
    if sys.version_info < MIN_PYTHON:
        found = ".".join(str(p) for p in sys.version_info[:3])
        need = ".".join(str(p) for p in MIN_PYTHON)
        _eprint(
            f"[error] cartopian install requires Python {need}+ (found {found}).\n"
            "        On macOS the stock /usr/bin/python3 is 3.9.x; install\n"
            "        Homebrew python@3.11 (or any >=3.11 interpreter on PATH)\n"
            "        before re-running this installer."
        )
        sys.exit(EXIT_BAD_PYTHON)


def _resolve_source_root(explicit: Optional[Path]) -> Path:
    if explicit is not None:
        root = explicit.expanduser().resolve()
    else:
        # scripts/install.py lives one directory below the source repo root.
        root = Path(__file__).resolve().parent.parent
    if not (root / "bin" / "cartopian").exists():
        raise SystemExit(
            f"[error] source repo root {root} does not contain bin/cartopian;\n"
            "        pass --source <path> if the script is run outside the repo."
        )
    return root


def _atomic_remove(path: Path) -> None:
    """Remove a file, directory, or symlink at ``path`` if present."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)


def _install_one(
    install_root: Path,
    target_rel: str,
    source: Path,
    mode: str,
    actions: List[str],
) -> None:
    target = install_root / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)

    # CHANGELOG.md is always a real copy per the install-behavior table.
    effective_mode = "copy" if target_rel in COPY_ALWAYS else mode

    if effective_mode == "symlink":
        if target.is_symlink():
            try:
                current = os.readlink(target)
            except OSError:
                current = None
            if current and Path(current) == source:
                actions.append(f"unchanged  symlink  {target_rel} -> {source}")
                return
        _atomic_remove(target)
        try:
            os.symlink(str(source), str(target), target_is_directory=source.is_dir())
        except OSError as exc:
            # Native Windows without Developer Mode / admin: symlink creation
            # may fail. Surface a clear error pointing at --mode copy.
            raise SystemExit(
                f"[error] failed to create symlink {target} -> {source}: {exc}\n"
                "        On native Windows, enable Developer Mode or re-run\n"
                "        with --mode copy."
            )
        actions.append(f"linked     {target_rel} -> {source}")
        return

    # copy mode
    _atomic_remove(target)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=False)
    else:
        shutil.copy2(source, target)
    actions.append(f"copied     {target_rel} <- {source}")


def _seed_operator_files(install_root: Path, source_root: Path, actions: List[str]) -> None:
    toml_target = install_root / OPERATOR_TOML
    if toml_target.exists():
        actions.append(f"preserved  {OPERATOR_TOML}")
    else:
        template = source_root / GLOBAL_TOML_TEMPLATE
        if not template.exists():
            raise SystemExit(
                f"[error] global TOML template missing at {template}"
            )
        shutil.copy2(template, toml_target)
        actions.append(f"seeded     {OPERATOR_TOML} <- {template}")

    registry_target = install_root / OPERATOR_REGISTRY
    if registry_target.exists():
        actions.append(f"preserved  {OPERATOR_REGISTRY}")
    else:
        registry_target.write_text(EMPTY_REGISTRY, encoding="utf-8")
        actions.append(f"seeded     {OPERATOR_REGISTRY} (empty array)")


def install(
    source_root: Path,
    install_root: Path,
    mode: str = "symlink",
) -> List[str]:
    """Perform install or upgrade; return human-readable action log."""
    if mode not in ("symlink", "copy"):
        raise ValueError(f"unknown mode: {mode!r}")

    install_root.mkdir(parents=True, exist_ok=True)
    actions: List[str] = []

    for target_rel, source_rel in TOOL_SHIPPED:
        source = source_root / source_rel
        if not source.exists():
            raise SystemExit(
                f"[error] expected source path missing: {source}"
            )
        _install_one(install_root, target_rel, source, mode, actions)

    _seed_operator_files(install_root, source_root, actions)
    return actions


def _check_optional_coreutils() -> Optional[str]:
    """Return a recommendation string if macOS is missing coreutils.

    The bash wrappers in ``wrappers/bin/`` rely on ``timeout`` (GNU
    coreutils) or ``gtimeout`` (Homebrew coreutils on macOS) to enforce
    ``CARTOPIAN_TIMEOUT`` at the OS level. Without it the wrapper warns
    and runs unbounded, which is functional but means a hung assignee
    will not be killed at the configured deadline. We intentionally do
    not run ``brew`` — this installer is zero-extra-dependency by
    design. Return ``None`` when no recommendation is needed.
    """
    if platform.system() != "Darwin":
        return None
    if shutil.which("timeout") or shutil.which("gtimeout"):
        return None
    return (
        "[recommended] GNU coreutils not detected on PATH. The bash\n"
        "              wrappers use `gtimeout` to enforce CARTOPIAN_TIMEOUT;\n"
        "              without it, handoffs run unbounded. Install with:\n"
        "                  brew install coreutils"
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cartopian-install",
        description=(
            "Install or upgrade Cartopian at ~/.cartopian/ from a cloned "
            "source repo. Re-run after `git pull` to upgrade."
        ),
    )
    p.add_argument(
        "--prefix",
        type=Path,
        default=None,
        help="install root (default: ~/.cartopian on Unix, %%USERPROFILE%%\\.cartopian on Windows).",
    )
    p.add_argument(
        "--source",
        type=Path,
        default=None,
        help="cartopian source repo root (default: the repo containing this script).",
    )
    p.add_argument(
        "--mode",
        choices=("symlink", "copy"),
        default="symlink",
        help="how to materialize tool-shipped paths (default: symlink).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="suppress per-action stdout; print only the final summary line.",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    _require_python()
    parser = _build_parser()
    args = parser.parse_args(argv)
    source_root = _resolve_source_root(args.source)
    install_root = (args.prefix or default_install_root()).expanduser().resolve()

    actions = install(source_root, install_root, mode=args.mode)
    if not args.quiet:
        for line in actions:
            print(line)
    print(f"cartopian installed at {install_root} (mode={args.mode}).")
    coreutils_note = _check_optional_coreutils()
    if coreutils_note:
        print(coreutils_note)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
