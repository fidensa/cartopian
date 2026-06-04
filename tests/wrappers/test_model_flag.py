"""CARTOPIAN_MODEL wrapper-translation contract tests.

`[handoffs.<role>].model` is resolved project -> global and exported by
`cartopian dispatch` as the agent-neutral `CARTOPIAN_MODEL` environment
variable. Each shipped wrapper translates it into the tool-specific
model-selection flag (verified against the upstream CLI surfaces:
`claude --model`, `codex exec --model`, `gemini --model`, `devin --model`).

Two halves per wrapper, exercised against the *real* Bash wrappers with fake
CLIs capturing the exact argv the underlying tool receives:

1. With CARTOPIAN_MODEL set, the wrapper passes `--model <value>` adjacently.
2. With CARTOPIAN_MODEL unset, no model flag is passed at all — the tool's
   own default model applies and no stale/invented value leaks in.
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
WRAPPERS = ["cartopian-claude", "cartopian-codex", "cartopian-gemini", "cartopian-devin"]
# Each wrapper -> the underlying CLI binary it invokes.
WRAPPER_CLI = {
    "cartopian-claude": "claude",
    "cartopian-codex": "codex",
    "cartopian-gemini": "gemini",
    "cartopian-devin": "devin",
}

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
    # Deliberately free of any "--model"-like substring so an argv scan of the
    # fake CLI's arguments cannot be fooled by the prompt content itself.
    p.write_text("do the thing")
    return p


def _run_wrapper(wrapper: str, prompt: Path, fake_bin: Path, model: str | None):
    """Run a Bash wrapper with a fake assignee on a RESTRICTED PATH.

    PATH excludes ``cartopian`` so the wrapper's access-grants step is skipped,
    and contains only the fake assignee, core utilities, and a real timeout
    binary. Mirrors tests/wrappers/test_timeout_ssot.py::_run_wrapper.
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
    if model is not None:
        env["CARTOPIAN_MODEL"] = model
    return subprocess.run(
        [bash, str(WRAPPER_DIR / wrapper), str(prompt)],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _captured_argv(tmp_path: Path, wrapper: str, model: str | None) -> list[str]:
    root = _project(tmp_path)
    prompt = _prompt(root, "01-400")
    fake_bin = tmp_path / "fakebin"
    args_out = tmp_path / "argv.txt"
    cli = WRAPPER_CLI[wrapper]
    _make_fake_cli(fake_bin, cli, f'printf "%s\\n" "$@" > "{args_out}"\nexit 0')

    res = _run_wrapper(wrapper, prompt, fake_bin, model)
    assert res.returncode == 0, res.stderr
    assert args_out.exists(), f"{wrapper}: fake {cli} was never invoked: {res.stderr}"
    return args_out.read_text().splitlines()


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_wrapper_passes_cartopian_model_as_model_flag(tmp_path, wrapper):
    """With CARTOPIAN_MODEL set, the wrapper injects `--model <value>`."""
    received = _captured_argv(tmp_path, wrapper, "test-model-x")
    assert "--model" in received, (
        f"{wrapper}: CARTOPIAN_MODEL was set but no --model flag reached the "
        f"underlying CLI. argv={received!r}"
    )
    idx = received.index("--model")
    assert received[idx + 1] == "test-model-x", (
        f"{wrapper}: --model value mismatch. argv={received!r}"
    )


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_wrapper_passes_no_model_flag_when_unset(tmp_path, wrapper):
    """With CARTOPIAN_MODEL unset, the wrapper must not invent a model flag —
    the underlying tool's own default model applies."""
    received = _captured_argv(tmp_path, wrapper, None)
    assert "--model" not in received, (
        f"{wrapper}: passed --model with no CARTOPIAN_MODEL set; the tool's "
        f"default model must apply. argv={received!r}"
    )
