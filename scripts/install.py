#!/usr/bin/env python3
"""Cartopian install / upgrade script.

Installs or upgrades a Cartopian tree at ``~/.cartopian/`` (or
``%USERPROFILE%\\.cartopian`` on native Windows) from a cloned source repo,
or — with ``--from-github`` — self-bootstraps from the GitHub release
tarball using only the Python standard library (``urllib`` + ``tarfile``),
so no ``curl``, ``tar``, or PowerShell cmdlets are involved on any platform.

The script also owns the side effects the install runbook used to script
by hand in shell:

- ``--ref <ref>`` records the installed git ref at ``<root>/VERSION``
  (``--from-github`` records the resolved ref automatically).
- ``--patch-path`` idempotently exposes ``<root>/bin`` and the platform
  wrapper directory on the user PATH — the registry-backed user PATH on
  Windows (via ``winreg``), the login shell's rc file on Unix.

Because the script ships itself into the install root
(``<root>/scripts/install.py``), an upgrade is a single one-line command
that behaves identically from PowerShell, cmd, Git Bash, zsh, or bash::

    python3 ~/.cartopian/scripts/install.py --from-github --patch-path
    py -3 "%USERPROFILE%\\.cartopian\\scripts\\install.py" --from-github --patch-path

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
import json
import os
import platform
import shutil
import ssl
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
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
    # Ship the installer itself so upgrades need no bootstrap download:
    # the next upgrade is `<python> <root>/scripts/install.py --from-github`.
    ("scripts/install.py", "scripts/install.py"),
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


# --- GitHub self-bootstrap (--from-github) ---------------------------------
# Download and extraction run entirely on the standard library so the same
# one-line command works from PowerShell, cmd, Git Bash, zsh, and bash — no
# curl (schannel issues in Git Bash), no tar (Git's bundled tar cannot
# extract to C:\ paths), no multi-line PowerShell.
GITHUB_REPO = "fidensa/cartopian"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"


def _ssl_context() -> ssl.SSLContext:
    """Default SSL context, with a fallback CA bundle when it holds no CAs.

    python.org macOS framework builds ship without a CA bundle until the
    user runs "Install Certificates.command"; without this fallback every
    HTTPS request dies with CERTIFICATE_VERIFY_FAILED. Windows and Linux
    load their system stores fine via the default context.
    """
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"] > 0:
        return ctx
    for bundle in (
        "/etc/ssl/cert.pem",                     # macOS system bundle
        "/etc/ssl/certs/ca-certificates.crt",    # Debian/Ubuntu
        "/etc/pki/tls/certs/ca-bundle.crt",      # RHEL/Fedora
    ):
        if os.path.exists(bundle):
            return ssl.create_default_context(cafile=bundle)
    return ctx


def _github_open(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "cartopian-install",
            "Accept": "application/vnd.github+json",
        },
    )
    return urllib.request.urlopen(req, timeout=120, context=_ssl_context())


def resolve_github_ref(explicit_ref: Optional[str]) -> Tuple[str, str]:
    """Return ``(ref, tarball_url)`` for the ref to install.

    An explicit ref wins; otherwise the latest release tag, falling back
    to ``main`` when no release has been published yet (HTTP 404).
    """
    if explicit_ref:
        return explicit_ref, f"{GITHUB_API}/tarball/{explicit_ref}"
    try:
        with _github_open(f"{GITHUB_API}/releases/latest") as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "main", f"{GITHUB_API}/tarball/main"
        raise SystemExit(
            f"[error] GitHub release lookup failed: HTTP {exc.code} {exc.reason}"
        )
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"[error] cannot reach api.github.com: {exc}")
    tag = data.get("tag_name")
    if not tag:
        raise SystemExit("[error] GitHub release response carries no tag_name.")
    return tag, data.get("tarball_url") or f"{GITHUB_API}/tarball/{tag}"


def fetch_github_source(tarball_url: str, workdir: Path) -> Path:
    """Download ``tarball_url`` into ``workdir``, extract it, and return the
    extracted repo root (GitHub tarballs hold a single top-level directory)."""
    tarball = workdir / "cartopian.tar.gz"
    try:
        with _github_open(tarball_url) as resp, tarball.open("wb") as out:
            shutil.copyfileobj(resp, out)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"[error] tarball download failed: {exc}")
    with tarfile.open(tarball, "r:gz") as archive:
        try:
            archive.extractall(workdir, filter="data")
        except TypeError:  # Python < 3.11.4 lacks the filter parameter.
            archive.extractall(workdir)
    tops = [p for p in workdir.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise SystemExit(
            "[error] expected one top-level directory in the GitHub tarball, "
            f"found {len(tops)}."
        )
    root = tops[0]
    if not (root / "bin" / "cartopian").exists():
        raise SystemExit(
            f"[error] extracted tree {root} does not look like a cartopian repo."
        )
    return root


def write_version_marker(install_root: Path, ref: str, actions: List[str]) -> None:
    (install_root / "VERSION").write_text(f"{ref}\n", encoding="utf-8")
    actions.append(f"recorded   VERSION = {ref}")


# --- Protocol-version reconciliation gate -----------------------------------
# After the tool tree is refreshed, every registered project's
# [project].protocol_version is compared against the shipped protocol version
# (the topmost CHANGELOG entry) so a stale config cannot drift silently across
# releases. Classification and message text live in cli/protocol_gate.py in
# the source tree being installed; it is loaded by file path (stdlib
# importlib) so this script stays a standalone bootstrap. The gate only
# detects and reports — it never writes a project's cartopian.toml.


def _load_protocol_gate(source_root: Path):
    """Load ``cli/protocol_gate.py`` from the source tree, or None if the
    source predates the gate (e.g. installing an older --ref)."""
    gate_path = source_root / "cli" / "protocol_gate.py"
    if not gate_path.is_file():
        return None
    import importlib.util

    spec = importlib.util.spec_from_file_location("_cartopian_protocol_gate", gate_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_declared_protocol_version(project_toml: Path):
    """Return ``(declared, error)`` for a project config's protocol_version.

    ``declared`` is the raw marker value (None when the key or file content
    leaves it unset — the CHANGELOG's "unset, missing" migratable case);
    ``error`` is a message when the config cannot be read at all.
    """
    import tomllib

    try:
        with project_toml.open("rb") as fh:
            cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, f"config unreadable: {project_toml} — {exc}"
    project_table = cfg.get("project")
    if not isinstance(project_table, dict):
        return None, f"no [project] table in {project_toml}"
    return project_table.get("protocol_version"), None


def reconcile_registered_projects(
    install_root: Path, source_root: Path, actions: List[str]
) -> List[str]:
    """Run the protocol-version gate over every registered project.

    Migratable projects print a ``[migration]`` line naming the detected and
    shipped versions; unknown/newer (or unreadable) configs print a
    ``[residual]`` line and are returned so the caller can fail closed.
    Current projects produce no output.
    """
    gate = _load_protocol_gate(source_root)
    if gate is None:
        actions.append(
            "skipped    protocol-version reconciliation (source ships no gate module)"
        )
        return []

    registry_path = install_root / OPERATOR_REGISTRY
    if not registry_path.is_file():
        return []

    residuals: List[str] = []
    try:
        entries = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("registry is not a JSON array")
    except (OSError, ValueError) as exc:
        line = (
            f"protocol-version reconciliation impossible: registry unreadable "
            f"({registry_path} — {exc}); registered project configs cannot be "
            f"verified against the shipped schema"
        )
        _eprint(f"[residual] {line}")
        residuals.append(line)
        return residuals

    shipped = gate.read_shipped_protocol_version(
        source_root / "protocol" / "CHANGELOG.md"
    )
    checked = 0
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        checked += 1
        project_id = entry.get("id") or "<unknown>"
        project_toml = Path(str(entry["path"])) / "cartopian.toml"
        declared, error = _read_declared_protocol_version(project_toml)
        if error is not None:
            line = (
                f"project {project_id}: config-schema gate failed closed "
                f"(residual: {gate.RESIDUAL_NAME}): {error}; the config cannot "
                f"be validated against the shipped protocol {shipped} and is "
                f"left unmodified"
            )
            _eprint(f"[residual] {line}")
            residuals.append(line)
            continue
        verdict = gate.classify_protocol_version(declared, shipped)
        if verdict["status"] == gate.GATE_CURRENT:
            continue
        line = f"project {project_id} ({project_toml.parent}): {verdict['detail']}"
        if verdict["status"] == gate.GATE_MIGRATE:
            _eprint(f"[migration] {line}")
            actions.append(f"migration  {project_id}: {verdict['detected_version']} -> {shipped}")
        else:
            _eprint(f"[residual] {line}")
            residuals.append(line)
    if checked:
        actions.append(
            f"reconciled protocol_version for {checked} registered project(s) "
            f"against shipped {shipped}"
        )
    return residuals


# --- User-PATH patching (--patch-path) --------------------------------------
# Idempotent across re-runs. Windows edits the registry-backed user PATH
# (the same value ``[Environment]::SetEnvironmentVariable(..., "User")``
# writes); Unix appends one export line to the login shell's rc file.
_RC_BY_SHELL = {"zsh": ".zshrc", "bash": ".bashrc"}


def _patch_windows_user_path(entries: List[str], actions: List[str]) -> None:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
    ) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in current.split(";") if p]
        present = {p.casefold().rstrip("\\") for p in parts}
        missing = [e for e in entries if e.casefold().rstrip("\\") not in present]
        if not missing:
            actions.append("unchanged  user PATH (entries already present)")
            return
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(missing + parts))
    try:
        # Best-effort WM_SETTINGCHANGE broadcast so newly launched shells
        # pick up the change without a logoff.
        import ctypes

        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None
        )
    except Exception:
        pass
    for entry in missing:
        actions.append(f"path       + {entry} (user PATH; open a new terminal)")


def _patch_unix_rc(bin_dir: Path, wrappers_dir: Path, actions: List[str]) -> None:
    line = f'export PATH="{bin_dir}:{wrappers_dir}:$PATH"'
    shell = Path(os.environ.get("SHELL", "")).name
    rc_name = _RC_BY_SHELL.get(shell)
    if rc_name is None:
        actions.append(
            f"path       unrecognized shell {shell or '(unset)'}; add manually: {line}"
        )
        return
    rc_path = Path.home() / rc_name
    content = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
    if str(bin_dir) in content and str(wrappers_dir) in content:
        actions.append(f"unchanged  PATH in ~/{rc_name}")
        return
    with rc_path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n# Cartopian\n{line}\n")
    actions.append(f"patched    ~/{rc_name} (source it or open a new terminal)")


def patch_user_path(install_root: Path, actions: List[str]) -> None:
    """Expose ``bin/`` and the platform wrapper directory on the user PATH."""
    bin_dir = install_root / "bin"
    if os.name == "nt":
        _patch_windows_user_path(
            [str(bin_dir), str(install_root / "wrappers" / "ps1")], actions
        )
    else:
        _patch_unix_rc(bin_dir, install_root / "wrappers" / "bin", actions)


# --- Claude Code refusal-adapter hook registration (operator-invoked) -----
# `--claude-hook <project-dir>` merges the PreToolUse registration for
# cli/claude_hook.py into <project-dir>/.claude/settings.json — project-level
# Claude Code settings only. It is never run implicitly and never touches any
# user-global settings file.
# Read tools first, then the mutation tools: the hook gates both axes, and
# the containment matrix claims read enforcement only when the registered
# matcher actually covers the read tools.
CLAUDE_HOOK_MATCHER = "Read|NotebookRead|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit"


def _claude_hook_command(install_root: Path) -> str:
    hook_path = install_root / "cli" / "claude_hook.py"
    return f'"{sys.executable}" "{hook_path}"'


def register_claude_hook(
    project_dir: Path, install_root: Path, actions: List[str]
) -> None:
    """Merge the refusal-adapter PreToolUse hook into the project's
    ``.claude/settings.json``. Idempotent: an existing claude_hook.py entry is
    replaced in place; all other settings are preserved."""
    import json

    settings_path = project_dir / ".claude" / "settings.json"
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(
                f"[error] cannot merge into {settings_path}: {exc}\n"
                "        fix or remove the file, then re-run."
            )
        if not isinstance(settings, dict):
            raise SystemExit(
                f"[error] {settings_path} is not a JSON object; not merging."
            )
    hooks = settings.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    entry = {
        "matcher": CLAUDE_HOOK_MATCHER,
        "hooks": [
            {"type": "command", "command": _claude_hook_command(install_root)}
        ],
    }
    kept = [
        item
        for item in pre
        if "claude_hook.py"
        not in "".join(
            h.get("command", "")
            for h in (item.get("hooks", []) if isinstance(item, dict) else [])
            if isinstance(h, dict)
        )
    ]
    kept.append(entry)
    hooks["PreToolUse"] = kept
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    actions.append(f"registered claude refusal-adapter hook in {settings_path}")


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
        default=None,
        help=(
            "how to materialize tool-shipped paths (default: symlink; "
            "--from-github implies copy)."
        ),
    )
    p.add_argument(
        "--from-github",
        action="store_true",
        help=(
            "download the source from GitHub (latest release, or --ref) instead "
            "of installing from a local repo; implies --mode copy."
        ),
    )
    p.add_argument(
        "--ref",
        default=None,
        help=(
            "git ref to install and record in VERSION. With --from-github: the "
            "tag/branch to download (default: latest release, falling back to "
            "main). Without --from-github, --ref only records the marker."
        ),
    )
    p.add_argument(
        "--patch-path",
        action="store_true",
        help=(
            "idempotently add bin/ and the platform wrapper directory to the "
            "user PATH (registry-backed user PATH on Windows, shell rc file "
            "on Unix)."
        ),
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="suppress per-action stdout; print only the final summary line.",
    )
    p.add_argument(
        "--claude-hook",
        type=Path,
        default=None,
        metavar="PROJECT_DIR",
        help=(
            "also register the Claude Code refusal-adapter PreToolUse hook in "
            "PROJECT_DIR/.claude/settings.json (project-level settings only; "
            "never modifies user-global settings)."
        ),
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    _require_python()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.from_github and args.source is not None:
        parser.error("--from-github and --source are mutually exclusive")
    mode = args.mode or ("copy" if args.from_github else "symlink")
    if args.from_github and mode == "symlink":
        parser.error(
            "--from-github requires copy mode "
            "(the downloaded source is deleted after install)"
        )
    install_root = (args.prefix or default_install_root()).expanduser().resolve()

    workdir: Optional[Path] = None
    try:
        if args.from_github:
            ref, tarball_url = resolve_github_ref(args.ref)
            if not args.quiet:
                print(f"fetching cartopian {ref} from {tarball_url}")
            workdir = Path(tempfile.mkdtemp(prefix="cartopian-install-"))
            source_root = fetch_github_source(tarball_url, workdir)
        else:
            source_root = _resolve_source_root(args.source)
            ref = args.ref

        actions = install(source_root, install_root, mode=mode)
        gate_residuals = reconcile_registered_projects(install_root, source_root, actions)
        if ref:
            write_version_marker(install_root, ref, actions)
        if args.patch_path:
            patch_user_path(install_root, actions)
        if args.claude_hook is not None:
            register_claude_hook(
                args.claude_hook.expanduser().resolve(), install_root, actions
            )
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)

    if not args.quiet:
        for line in actions:
            print(line)
    print(f"cartopian installed at {install_root} (mode={mode}).")
    coreutils_note = _check_optional_coreutils()
    if coreutils_note:
        print(coreutils_note)
    if gate_residuals:
        _eprint(
            f"[residual] {len(gate_residuals)} registered project config(s) failed "
            "the protocol-version gate (fail-closed); see [residual] lines above. "
            "No cartopian.toml was modified."
        )
        return EXIT_FAIL
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
