"""CARTOPIAN_EFFORT wrapper-translation contract tests.

`[handoffs.<role>].effort` is resolved project -> global and exported by
`cartopian dispatch` as the agent-neutral `CARTOPIAN_EFFORT` environment
variable. Unlike model (uniform `--model`), the translation is per-agent:

* cartopian-claude — `--effort <level>`, CLI-wide vocabulary
  low|medium|high|xhigh|max;
* cartopian-codex — `-c model_reasoning_effort=<level>`, CLI-wide vocabulary
  low|medium|high|xhigh|max|ultra;
* cartopian-gemini / cartopian-devin — the underlying CLI has no
  effort/thinking flag; the wrapper ignores CARTOPIAN_EFFORT with a notice.

Fallback contract: the value is lowercased and checked against the wrapper's
CLI-wide vocabulary; anything outside it produces a one-line stderr notice and
NO flag, so the tool launches at its own default effort (never a hard failure).

Exercised against the *real* Bash wrappers with fake CLIs capturing the exact
argv the underlying tool receives, mirroring test_model_flag.py.
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
# Wrappers whose CLI supports an effort flag, and those that must ignore it.
TRANSLATING_WRAPPERS = ["cartopian-claude", "cartopian-codex"]
IGNORING_WRAPPERS = ["cartopian-gemini", "cartopian-devin"]
WRAPPERS = TRANSLATING_WRAPPERS + IGNORING_WRAPPERS
# Each wrapper -> the underlying CLI binary it invokes.
WRAPPER_CLI = {
    "cartopian-claude": "claude",
    "cartopian-codex": "codex",
    "cartopian-gemini": "gemini",
    "cartopian-devin": "devin",
}
# Tokens that must never appear in argv unless the wrapper translated a valid
# effort value. The prompt body is free of these substrings by construction.
EFFORT_TOKENS = ("--effort", "model_reasoning_effort")

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
    # Deliberately free of any effort-flag-like substring so an argv scan of
    # the fake CLI's arguments cannot be fooled by the prompt content itself.
    p.write_text("do the thing")
    return p


def _run_wrapper(wrapper: str, prompt: Path, fake_bin: Path, effort: str | None):
    """Run a Bash wrapper with a fake assignee on a RESTRICTED PATH.

    PATH excludes ``cartopian`` so the wrapper's access-grants step is skipped,
    and contains only the fake assignee, core utilities, and a real timeout
    binary. Mirrors tests/wrappers/test_model_flag.py::_run_wrapper.
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
    if effort is not None:
        env["CARTOPIAN_EFFORT"] = effort
    return subprocess.run(
        [bash, str(WRAPPER_DIR / wrapper), str(prompt)],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _captured(tmp_path: Path, wrapper: str, effort: str | None):
    """Return (argv_lines, stderr) after running the wrapper to completion."""
    root = _project(tmp_path)
    prompt = _prompt(root, "01-400")
    fake_bin = tmp_path / "fakebin"
    args_out = tmp_path / "argv.txt"
    cli = WRAPPER_CLI[wrapper]
    _make_fake_cli(fake_bin, cli, f'printf "%s\\n" "$@" > "{args_out}"\nexit 0')

    res = _run_wrapper(wrapper, prompt, fake_bin, effort)
    assert res.returncode == 0, res.stderr
    assert args_out.exists(), f"{wrapper}: fake {cli} was never invoked: {res.stderr}"
    return args_out.read_text().splitlines(), res.stderr


def _assert_no_effort_argv(wrapper: str, received: list[str]) -> None:
    for token in EFFORT_TOKENS:
        assert not any(token in arg for arg in received), (
            f"{wrapper}: effort flag {token!r} reached the underlying CLI when "
            f"it must not have. argv={received!r}"
        )


def test_claude_translates_effort_to_effort_flag(tmp_path):
    received, _ = _captured(tmp_path, "cartopian-claude", "high")
    assert "--effort" in received, f"argv={received!r}"
    idx = received.index("--effort")
    assert received[idx + 1] == "high", f"argv={received!r}"


def test_codex_translates_effort_to_reasoning_config(tmp_path):
    received, _ = _captured(tmp_path, "cartopian-codex", "high")
    assert "-c" in received, f"argv={received!r}"
    idx = received.index("-c")
    assert received[idx + 1] == "model_reasoning_effort=high", f"argv={received!r}"


@pytest.mark.parametrize("wrapper", TRANSLATING_WRAPPERS)
def test_uppercase_effort_is_lowercased(tmp_path, wrapper):
    """The exported value is case-normalized before both the vocabulary check
    and the flag append, so `HIGH` behaves exactly like `high`."""
    received, _ = _captured(tmp_path, wrapper, "HIGH")
    flat = "\n".join(received)
    assert "high" in flat and "HIGH" not in flat, f"argv={received!r}"


@pytest.mark.parametrize("wrapper", TRANSLATING_WRAPPERS)
def test_invalid_effort_warns_and_omits_flag(tmp_path, wrapper):
    """A value outside the CLI-wide vocabulary must not reach the underlying
    CLI (the tool's default effort applies) and must be visible in stderr."""
    received, stderr = _captured(tmp_path, wrapper, "bogus")
    _assert_no_effort_argv(wrapper, received)
    assert "CARTOPIAN_EFFORT=bogus" in stderr, (
        f"{wrapper}: expected a fallback notice naming the rejected value; "
        f"stderr={stderr!r}"
    )
    assert "default effort" in stderr, f"stderr={stderr!r}"


def test_claude_rejects_codex_only_level(tmp_path):
    """`ultra` is codex vocabulary, not claude's — claude must fall back."""
    received, stderr = _captured(tmp_path, "cartopian-claude", "ultra")
    _assert_no_effort_argv("cartopian-claude", received)
    assert "CARTOPIAN_EFFORT=ultra" in stderr, f"stderr={stderr!r}"


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_no_effort_argv_when_unset(tmp_path, wrapper):
    """With CARTOPIAN_EFFORT unset, no wrapper may invent an effort flag —
    the underlying tool's own default effort applies."""
    received, stderr = _captured(tmp_path, wrapper, None)
    _assert_no_effort_argv(wrapper, received)
    assert "CARTOPIAN_EFFORT" not in stderr, (
        f"{wrapper}: emitted an effort notice with no CARTOPIAN_EFFORT set; "
        f"stderr={stderr!r}"
    )


@pytest.mark.parametrize("wrapper", IGNORING_WRAPPERS)
def test_unsupported_cli_ignores_effort_with_notice(tmp_path, wrapper):
    """gemini/devin have no effort/thinking flag: even a vocabulary-valid
    value is ignored with a notice; nothing effort-shaped reaches the CLI."""
    received, stderr = _captured(tmp_path, wrapper, "high")
    _assert_no_effort_argv(wrapper, received)
    assert "ignoring CARTOPIAN_EFFORT" in stderr, (
        f"{wrapper}: expected an ignore notice; stderr={stderr!r}"
    )
