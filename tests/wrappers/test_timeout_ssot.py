"""Timeout single-source-of-truth (SSOT) contract tests — TASK-01-006 / FR-002.

These tests lock the invariant RM-003 is about: the handoff timeout has exactly
one source of truth (`[handoffs.<role>].timeout`, resolved project -> global),
passed to the wrapper as `CARTOPIAN_TIMEOUT`, and enforced *solely* by the
wrapper's OS-level deadline. No competing/second timer may exist.

Two halves, matching the evidence gate:

1. Launcher side — `cartopian handoff-packet` resolves the canonical timeout, and
   the launch skill (`skills/run-handoff.md`) exports exactly that value as
   `CARTOPIAN_TIMEOUT` (with the protocol 60m default) for the wrapper to consume.
2. Wrapper side — the wrapper is the sole enforcer:
   - it passes NO independent per-tool timeout flag to the underlying CLI (so the
     OS-level `CARTOPIAN_TIMEOUT` deadline is the only timer — the RM-003 guard);
   - it kills the assignee at the `CARTOPIAN_TIMEOUT` deadline (exit 124); and
   - no second, shorter timer fires before that deadline.

The wrapper-side tests exercise the *real* Bash wrappers against fake CLIs, so a
reintroduced competing timer (e.g. a hardcoded `--timeout`) fails here.

This file is independent of TASK-01-007's status-file tests; both must stay green.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER_DIR = REPO_ROOT / "wrappers" / "bin"
RUN_HANDOFF_SKILL = REPO_ROOT / "skills" / "run-handoff.md"
WRAPPERS = ["cartopian-claude", "cartopian-codex", "cartopian-gemini", "cartopian-devin"]
# Each wrapper -> the underlying CLI binary it invokes.
WRAPPER_CLI = {
    "cartopian-claude": "claude",
    "cartopian-codex": "codex",
    "cartopian-gemini": "gemini",
    "cartopian-devin": "devin",
}

# Per-tool timeout flags that would constitute a competing timer (RM-003). The
# only legitimate timeout is the OS-level `CARTOPIAN_TIMEOUT` deadline the
# wrapper itself applies; none of these may be passed to the underlying CLI.
FORBIDDEN_TIMEOUT_FLAGS = ("--timeout", "--max-time", "--deadline", "--time-limit")

bash = shutil.which("bash")
pytestmark = pytest.mark.skipif(bash is None, reason="bash not available")


# --- shared helpers -------------------------------------------------------

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
    # Deliberately free of any "--timeout"-like substring so an argv scan of the
    # fake CLI's arguments cannot be fooled by the prompt content itself.
    p.write_text("do the thing")
    return p


def _run_wrapper(wrapper: str, prompt: Path, fake_bin: Path, timeout_spec: str):
    """Run a Bash wrapper with a fake assignee on a RESTRICTED PATH.

    PATH excludes ``cartopian`` so the wrapper's access-grants step is skipped,
    and contains only the fake assignee, core utilities, and a real timeout
    binary. Mirrors tests/wrappers/test_wrapper_status_file.py::_run_wrapper.
    """
    path_parts = [str(fake_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    tbin = shutil.which("timeout") or shutil.which("gtimeout")
    if tbin:
        path_parts.insert(1, str(Path(tbin).parent))
    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": os.environ.get("HOME", "/tmp"),
        "CARTOPIAN_TIMEOUT": timeout_spec,
    }
    return subprocess.run(
        [bash, str(WRAPPER_DIR / wrapper), str(prompt)],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _read_status(status_path: Path) -> dict:
    out = {}
    for line in status_path.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


# --- launcher side (FR-002): single canonical timeout, exported once ------

def _handoff_packet_project(tmp_path: Path, toml_body: str, task_id: str):
    """Scaffold a minimal handoff-packet-resolvable project and return (root, task)."""
    root = tmp_path / "proj"
    (root / "phases").mkdir(parents=True)
    (root / "cartopian.toml").write_text(toml_body, encoding="utf-8")
    tasks = root / "tasks" / "in-progress"
    tasks.mkdir(parents=True)
    task = tasks / f"TASK-{task_id}-demo.md"
    task.write_text("# Demo task\n", encoding="utf-8")
    return root, task


def _run_packet(task: Path, role: str):
    return subprocess.run(
        [sys.executable, "-m", "cli", "handoff-packet", str(task), "--role", role],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


def test_launcher_resolves_configured_timeout(tmp_path):
    """handoff-packet resolves [handoffs.<role>].timeout to the one canonical value."""
    _root, task = _handoff_packet_project(
        tmp_path,
        '[handoffs.coder]\nagent = "cartopian-claude"\nauto_start = true\ntimeout = "17m"\n',
        "01-001",
    )
    res = _run_packet(task, "coder")
    assert res.returncode == 0, res.stderr
    rec = json.loads(res.stdout)
    assert rec["timeout"] == "17m"


def test_launcher_skill_exports_cartopian_timeout_as_sole_timer(tmp_path):
    """The launch skill exports the resolved timeout as CARTOPIAN_TIMEOUT (with the
    60m default) and declares the wrapper the sole enforcer with no second timer.

    The export itself is a skill instruction (no shell launcher binary exists), so
    the contract is asserted against the skill text; the *value* it exports is the
    handoff-packet `timeout` field exercised above.
    """
    text = RUN_HANDOFF_SKILL.read_text(encoding="utf-8")
    # The launch contract sets CARTOPIAN_TIMEOUT from the resolved packet timeout,
    # with the protocol 60m default applied by the wrapper.
    assert "CARTOPIAN_TIMEOUT" in text
    assert "resolved `[handoffs.<role>].timeout`" in text
    assert "protocol default of `60m`" in text
    # The wrapper is the enforcer (OS-level deadline, exit 124) and the PM imposes
    # no separate timer — the SSOT with no competing second timer.
    assert "exit `124`" in text
    assert "imposing a separate PM-side deadline" in text


def test_conventions_documents_timeout_ssot():
    """protocol/CONVENTIONS.md states the timeout SSOT explicitly."""
    text = (REPO_ROOT / "protocol" / "CONVENTIONS.md").read_text(encoding="utf-8")
    assert "single source of truth for the handoff deadline" in text
    assert "CARTOPIAN_TIMEOUT" in text
    assert "sole enforcer" in text
    assert "no per-tool CLI timeout flag" in text


# --- wrapper side (RM-003): no competing/second timer ---------------------

@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_wrapper_passes_no_independent_timeout_flag_to_cli(tmp_path, wrapper):
    """The wrapper must not hand the underlying CLI its own timeout flag.

    The only timer is the OS-level CARTOPIAN_TIMEOUT deadline the wrapper applies;
    a hardcoded (or even CARTOPIAN_TIMEOUT-derived) per-tool `--timeout` would be
    a second timer. We capture exactly the argv the underlying CLI receives and
    assert no forbidden timeout flag is present.
    """
    root = _project(tmp_path)
    prompt = _prompt(root, "01-300")
    fake_bin = tmp_path / "fakebin"
    args_out = tmp_path / "argv.txt"
    cli = WRAPPER_CLI[wrapper]
    _make_fake_cli(fake_bin, cli, f'printf "%s\\n" "$@" > "{args_out}"\nexit 0')

    res = _run_wrapper(wrapper, prompt, fake_bin, "30m")
    assert res.returncode == 0, res.stderr
    assert args_out.exists(), f"{wrapper}: fake {cli} was never invoked: {res.stderr}"

    received = args_out.read_text().splitlines()
    for flag in FORBIDDEN_TIMEOUT_FLAGS:
        assert flag not in received, (
            f"{wrapper}: passed competing timer flag {flag!r} to {cli}; the "
            f"CARTOPIAN_TIMEOUT OS-level deadline must be the sole timer. "
            f"argv={received!r}"
        )


@pytest.mark.skipif(
    not (shutil.which("timeout") or shutil.which("gtimeout")),
    reason="no coreutils timeout/gtimeout on PATH",
)
def test_wrapper_kills_at_ssot_deadline(tmp_path):
    """A real short CARTOPIAN_TIMEOUT kill yields the timeout outcome (exit 124)
    at the SSOT deadline — the wrapper is the enforcer."""
    root = _project(tmp_path)
    prompt = _prompt(root, "01-301")
    fake_bin = tmp_path / "fakebin"
    _make_fake_cli(fake_bin, "claude", "sleep 5\nexit 0")

    res = _run_wrapper("cartopian-claude", prompt, fake_bin, "1s")
    assert res.returncode == 124, (
        f"expected SSOT-deadline kill (124), got {res.returncode}: {res.stderr}"
    )
    data = _read_status(root / "reports" / "REPORT-01-301.md.status")
    assert data["exit_code"] == "124"
    assert data["reason"] == "timeout"


@pytest.mark.skipif(
    not (shutil.which("timeout") or shutil.which("gtimeout")),
    reason="no coreutils timeout/gtimeout on PATH",
)
def test_no_second_timer_kills_before_ssot_deadline(tmp_path):
    """A job comfortably under CARTOPIAN_TIMEOUT runs to completion — proving no
    competing shorter timer can kill a legitimate long-running handoff early."""
    root = _project(tmp_path)
    prompt = _prompt(root, "01-302")
    fake_bin = tmp_path / "fakebin"
    # Sleeps 2s; CARTOPIAN_TIMEOUT is 10s. Only a *second*, shorter timer could
    # kill this before it exits cleanly.
    _make_fake_cli(fake_bin, "claude", "sleep 2\nexit 0")

    res = _run_wrapper("cartopian-claude", prompt, fake_bin, "10s")
    assert res.returncode == 0, (
        f"a sub-deadline job was killed early (rc={res.returncode}); a competing "
        f"timer fired before the CARTOPIAN_TIMEOUT deadline: {res.stderr}"
    )
    data = _read_status(root / "reports" / "REPORT-01-302.md.status")
    assert data["exit_code"] == "0"
    assert data["reason"] == "clean"
