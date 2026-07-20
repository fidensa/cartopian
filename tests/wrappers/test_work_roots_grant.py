"""CARTOPIAN_WORK_ROOTS wrapper-translation contract tests.

The launch contract grants the assignee write access to the union of the
cartopian project root and the project's declared work roots. `cartopian
dispatch` resolves the work roots and exports them as the agent-neutral
`CARTOPIAN_WORK_ROOTS` environment variable (colon-joined absolute paths on
POSIX). Wrappers whose agent CLI imposes its own filesystem sandbox rooted at
the launch cwd must widen it to cover the declared work roots — otherwise
every work-root write fails ("Operation not permitted", the reported
cartopian-codex bug):

* cartopian-codex — `--sandbox workspace-write` confines writes to the launch
  cwd; the wrapper adds `-c sandbox_workspace_write.writable_roots=[...]`.
* cartopian-claude — the wrapper passes `--add-dir <root>` per work root so
  the grant is explicit in every permission mode.
* cartopian-gemini / cartopian-devin — their sandboxes expose no per-path
  grant surface; when the sandbox is active and work roots are declared, the
  wrapper warns on stderr so a work-root write failure is traceable.

Exercised against the *real* Bash wrappers with fake CLIs capturing the exact
argv the underlying tool receives (same harness as test_model_flag.py).
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER_DIR = REPO_ROOT / "wrappers" / "bin"

bash = shutil.which("bash")
pytestmark = pytest.mark.skipif(bash is None, reason="bash not available")


def _make_fake_cli(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "prompts").mkdir(parents=True)
    (root / "reports").mkdir(parents=True)
    return root


def _prompt(root: Path, rid: str) -> Path:
    p = root / "prompts" / f"PROMPT-{rid}.md"
    # Deliberately free of any flag-like substring so an argv scan of the fake
    # CLI's arguments cannot be fooled by the prompt content itself.
    p.write_text("do the thing")
    return p


def _run_wrapper(
    wrapper: str,
    prompt: Path,
    fake_bin: Path,
    extra_env: dict[str, str] | None = None,
):
    """Run a Bash wrapper with a fake assignee on a RESTRICTED PATH.

    Mirrors tests/wrappers/test_model_flag.py::_run_wrapper.
    """
    path_parts = [str(fake_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    tbin = shutil.which("timeout") or shutil.which("gtimeout")
    if tbin:
        path_parts.insert(1, str(Path(tbin).parent))
    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": os.environ.get("HOME", "/tmp"),
        "CARTOPIAN_TIMEOUT": "30m",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [bash, str(WRAPPER_DIR / wrapper), str(prompt)],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _captured(tmp_path: Path, wrapper: str, cli: str, extra_env: dict[str, str] | None):
    root = _project(tmp_path)
    prompt = _prompt(root, "01-400")
    fake_bin = tmp_path / "fakebin"
    args_out = tmp_path / "argv.txt"
    _make_fake_cli(fake_bin, cli, f'printf "%s\\n" "$@" > "{args_out}"\nexit 0')

    res = _run_wrapper(wrapper, prompt, fake_bin, extra_env)
    assert res.returncode == 0, res.stderr
    assert args_out.exists(), f"{wrapper}: fake {cli} was never invoked: {res.stderr}"
    return args_out.read_text().splitlines(), res


ROOTS = ["/tmp/wr-product", "/tmp/wr-docs"]
ROOTS_ENV = {"CARTOPIAN_WORK_ROOTS": ":".join(ROOTS)}


def test_codex_widens_workspace_write_sandbox_with_work_roots(tmp_path):
    """The default workspace-write sandbox is widened with every declared
    work root via codex's writable_roots config override — the reported bug's
    direct fix."""
    received, _ = _captured(tmp_path, "cartopian-codex", "codex", ROOTS_ENV)
    expected = 'sandbox_workspace_write.writable_roots=["/tmp/wr-product", "/tmp/wr-docs"]'
    assert expected in received, (
        f"codex sandbox not widened to the declared work roots. argv={received!r}"
    )
    idx = received.index(expected)
    assert received[idx - 1] == "-c", f"writable_roots must ride a -c override. argv={received!r}"
    # The base sandbox scope is untouched — widening is additive.
    assert "--sandbox" in received and received[received.index("--sandbox") + 1] == "workspace-write"


def test_codex_no_writable_roots_when_unset(tmp_path):
    received, _ = _captured(tmp_path, "cartopian-codex", "codex", None)
    assert not any("sandbox_workspace_write" in a for a in received), (
        f"codex received a writable_roots override with no work roots declared. argv={received!r}"
    )


def test_codex_bypass_mode_passes_no_writable_roots(tmp_path):
    # --dangerously-bypass-approvals-and-sandbox disables the sandbox
    # entirely; a writable_roots override would be dead config.
    env = dict(ROOTS_ENV, CARTOPIAN_CODEX_BYPASS="true")
    received, _ = _captured(tmp_path, "cartopian-codex", "codex", env)
    assert "--dangerously-bypass-approvals-and-sandbox" in received
    assert not any("sandbox_workspace_write" in a for a in received), (
        f"bypass mode must not compose sandbox config. argv={received!r}"
    )


def test_claude_adds_each_work_root_as_add_dir(tmp_path):
    received, _ = _captured(tmp_path, "cartopian-claude", "claude", ROOTS_ENV)
    pairs = [
        received[i + 1]
        for i, a in enumerate(received)
        if a == "--add-dir" and i + 1 < len(received)
    ]
    assert pairs == ROOTS, (
        f"claude must receive one --add-dir per declared work root, in order. argv={received!r}"
    )


def test_claude_no_add_dir_when_unset(tmp_path):
    received, _ = _captured(tmp_path, "cartopian-claude", "claude", None)
    assert "--add-dir" not in received, (
        f"claude received --add-dir with no work roots declared. argv={received!r}"
    )


def test_gemini_warns_when_sandbox_active_with_work_roots(tmp_path):
    env = dict(ROOTS_ENV, CARTOPIAN_GEMINI_SANDBOX="true")
    received, res = _captured(tmp_path, "cartopian-gemini", "gemini", env)
    assert "--sandbox" in received
    assert "declared work roots may not be writable inside the sandbox" in res.stderr, (
        f"gemini sandbox + work roots must warn on stderr. stderr={res.stderr!r}"
    )


def test_gemini_default_no_sandbox_no_warning(tmp_path):
    received, res = _captured(tmp_path, "cartopian-gemini", "gemini", ROOTS_ENV)
    assert "--sandbox" not in received
    assert "may not be writable" not in res.stderr


def test_devin_warns_when_sandbox_active_with_work_roots(tmp_path):
    # The fake devin accepts every probe (exit 0), so the wrapper detects the
    # four-mode surface with --sandbox support and composes the sandboxed
    # autonomous launch — which cannot cover the declared work roots.
    received, res = _captured(tmp_path, "cartopian-devin", "devin", ROOTS_ENV)
    assert "--sandbox" in received
    assert "declared work roots may not be writable inside the sandbox" in res.stderr, (
        f"devin sandbox + work roots must warn on stderr. stderr={res.stderr!r}"
    )
