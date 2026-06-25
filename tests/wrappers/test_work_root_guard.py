"""Tests for the per-agent work-root scoping guard.

Every cartopian-* wrapper must FAIL CLOSED when the tool cannot scope the agent
to a non-empty resolved work-root set (protocol/CONVENTIONS.md § Work Roots;
the `[work-root]` stderr contract; NF-002). A copy-pasted bug made the guard a
no-op: the resolved roots were extracted with

    WORK_ROOTS=$(python3 - <<'PY' ... PY <<<"$RC_JSON")

a DOUBLE stdin redirect where the here-string ($RC_JSON) wins over the heredoc,
so python's *program* was the JSON (a syntax error), WORK_ROOTS came back blank,
and the guard never fired. These tests pin both halves of the fix:

- the shared-helper extraction reads RC_JSON from the ENVIRONMENT (program and
  data on separate channels) so WORK_ROOTS is populated; and
- end-to-end, each wrapper fails closed on an unscopable work-root set, while
  honoring the two documented bypasses (the per-tool unrestricted bypass and the
  agent-neutral reviewer-recapture read-only-source path).

No live model is needed: a fake ``cartopian`` shim stubs ``resolve-config`` and a
fake assignee binary stubs the upstream CLI, both on a restricted PATH.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "wrappers" / "bin"
STATUS_HELPER = BIN_DIR / "_cartopian-status.sh"

# (wrapper filename, upstream CLI binary, per-tool unrestricted bypass env var)
WRAPPERS = [
    ("cartopian-claude", "claude", "CARTOPIAN_CLAUDE_UNRESTRICTED"),
    ("cartopian-codex", "codex", "CARTOPIAN_CODEX_UNRESTRICTED"),
    ("cartopian-gemini", "gemini", "CARTOPIAN_GEMINI_UNRESTRICTED"),
    ("cartopian-devin", "devin", "CARTOPIAN_DEVIN_UNRESTRICTED"),
]

# Tools whose sandbox can scope a multi-directory union natively no longer fail
# closed on a non-empty work-root set — they launch SCOPED to the union (see
# test_work_root_scoping.py). Only a tool with no local path-scoping mechanism
# (Devin) stays fail-closed-or-bypass, so the "guard fires fail-closed on an
# unscopable set" assertion is now scoped to that tool. The missing-root,
# unrestricted-bypass, and reviewer-recapture paths below are unchanged and
# still exercised across every wrapper.
SCOPABLE = {"cartopian-claude", "cartopian-codex", "cartopian-gemini"}
UNSCOPABLE_WRAPPERS = [w for w in WRAPPERS if w[0] not in SCOPABLE]

CONFIG_BODY = """[project]
work_roots = ["product"]

[roles]
coder = "Implements tasks per spec."
"""


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / "prompts").mkdir(parents=True)
    (project / "cartopian.toml").write_text(CONFIG_BODY, encoding="utf-8")
    prompt = project / "prompts" / "PROMPT-03-008.md"
    prompt.write_text("do the thing\n", encoding="utf-8")
    return prompt


def _shim(dir_path: Path, name: str, body: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_guard_wrapper(wrapper, tool, prompt, work_root, *, extra_env=None):
    """Run a wrapper with a fake ``cartopian`` that resolves ``work_root``.

    The fake ``cartopian resolve-config`` emits a single JSON line declaring one
    work root mapped to ``work_root`` (an absolute path). The fake assignee exits
    0. PATH carries only the fakes + core utils (+ a real timeout if available).
    Returns the CompletedProcess.
    """
    fakebin = prompt.parent.parent.parent / "fakebin"
    _shim(fakebin, tool, "exit 0")
    # JSON on stdout, single line; ignore args. Use printf to avoid quoting woes.
    _shim(
        fakebin,
        "cartopian",
        f'if [ "$1" = "resolve-config" ]; then '
        f"printf '%s\\n' '{{\"work_roots\": {{\"product\": \"{work_root}\"}}}}'; fi",
    )

    path_parts = [str(fakebin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    tbin = shutil.which("timeout") or shutil.which("gtimeout")
    if tbin:
        path_parts.insert(1, str(Path(tbin).parent))

    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": os.environ.get("HOME", "/tmp"),
        "CARTOPIAN_TIMEOUT": "60m",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(BIN_DIR / wrapper), str(prompt)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- shared-helper extraction: the actual double-redirect fix ----------------


def test_extract_work_roots_reads_env_not_double_redirect():
    """cartopian_extract_work_roots returns the roots from RC_JSON via env."""
    rc_json = '{"work_roots": {"product": "/abs/one", "design": "/abs/two"}}'
    out = subprocess.run(
        ["bash", "-c",
         f'source "{STATUS_HELPER}"; cartopian_extract_work_roots \'{rc_json}\''],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert set(out) == {"/abs/one", "/abs/two"}


def test_extract_work_roots_empty_for_no_roots():
    out = subprocess.run(
        ["bash", "-c",
         f'source "{STATUS_HELPER}"; cartopian_extract_work_roots \'{{"work_roots": {{}}}}\''],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == ""


# --- static: the buggy double-stdin-redirect is gone from every wrapper ------


@pytest.mark.parametrize("wrapper,_tool,_var", WRAPPERS)
def test_no_double_stdin_redirect(wrapper, _tool, _var):
    text = (BIN_DIR / wrapper).read_text(encoding="utf-8")
    # The here-string feeding RC_JSON as a *second* stdin to python is the bug.
    assert '<<<"$RC_JSON"' not in text, f"{wrapper} still double-redirects RC_JSON"
    assert "sys.stdin.read()" not in text, (
        f"{wrapper} still reads the work-root JSON from stdin"
    )


# --- end-to-end: the guard FIRES fail-closed (the core acceptance) -----------


@pytest.mark.parametrize("wrapper,tool,_var", UNSCOPABLE_WRAPPERS)
def test_guard_fires_fail_closed(tmp_path, wrapper, tool, _var):
    """A non-empty work root the tool CANNOT scope natively must fail closed.

    Scopable tools (claude/codex/gemini) now launch scoped to the union instead —
    that path is pinned in test_work_root_scoping.py; here we keep the fail-closed
    contract for a genuinely-unscopable tool (Devin)."""
    prompt = _make_project(tmp_path)
    work_root = tmp_path / "product"
    work_root.mkdir()

    proc = _run_guard_wrapper(wrapper, tool, prompt, str(work_root))

    assert proc.returncode != 0, (
        f"{wrapper}: guard did NOT fire — proceeded instead of failing closed\n"
        f"stderr:\n{proc.stderr}"
    )
    assert "[work-root]" in proc.stderr, (
        f"{wrapper}: no [work-root] stderr line\nstderr:\n{proc.stderr}"
    )


@pytest.mark.parametrize("wrapper,tool,_var", WRAPPERS)
def test_guard_missing_root_fails_closed(tmp_path, wrapper, tool, _var):
    """A declared work root that is absent on disk must fail closed."""
    prompt = _make_project(tmp_path)
    missing = tmp_path / "does-not-exist"

    proc = _run_guard_wrapper(wrapper, tool, prompt, str(missing))

    assert proc.returncode != 0, f"{wrapper}: missing root did not fail closed"
    assert "[work-root] missing:" in proc.stderr, (
        f"{wrapper}: no '[work-root] missing' line\nstderr:\n{proc.stderr}"
    )


# --- documented bypass 1: per-tool unrestricted ------------------------------


@pytest.mark.parametrize("wrapper,tool,var", WRAPPERS)
def test_unrestricted_bypass_proceeds(tmp_path, wrapper, tool, var):
    prompt = _make_project(tmp_path)
    work_root = tmp_path / "product"
    work_root.mkdir()

    proc = _run_guard_wrapper(
        wrapper, tool, prompt, str(work_root), extra_env={var: "true"}
    )

    assert proc.returncode == 0, (
        f"{wrapper}: unrestricted bypass did not proceed\nstderr:\n{proc.stderr}"
    )
    assert "unrestricted mode enabled" in proc.stderr


# --- documented bypass 2: reviewer-recapture read-only source ----------------


@pytest.mark.parametrize("wrapper,tool,_var", WRAPPERS)
def test_recapture_readonly_bypass_proceeds(tmp_path, wrapper, tool, _var):
    """The agent-neutral reviewer-recapture path treats roots as read-only and
    must NOT be made to fail closed by the guard."""
    prompt = _make_project(tmp_path)
    work_root = tmp_path / "product"
    work_root.mkdir()

    proc = _run_guard_wrapper(
        wrapper, tool, prompt, str(work_root),
        extra_env={"CARTOPIAN_REVIEW_RECAPTURE": "1"},
    )

    assert proc.returncode == 0, (
        f"{wrapper}: reviewer-recapture path failed closed (regression)\n"
        f"stderr:\n{proc.stderr}"
    )
    # The recapture banner names the read-only source root.
    assert "read-only source work root:" in proc.stderr
    assert str(work_root) in proc.stderr
