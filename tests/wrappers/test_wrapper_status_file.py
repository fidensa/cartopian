"""Tests for the wrapper status file.

The agent wrappers emit an optional early-crash signal — a status file at
``<report-path>.status`` — that ``cartopian wait-handoff`` consumes. These
tests pin the *producer* side to the *consumer* contract enforced by
``cli.commands.wait_handoff._status_exit_code``:

- the wrapper writes the file at the exact ``<report-path>.status`` path
  wait-handoff derives;
- the file records ``state=exited`` plus the assignee ``exit_code``, and
  distinguishes clean exit (0) / non-zero exit / timeout kill (124); and
- the bytes the Bash producer writes are parsed correctly by the real
  consumer function (producer/consumer agreement asserted directly).

PowerShell parity is asserted statically (no ``pwsh`` in CI): the ``.ps1``
helper and wrappers mirror the Bash field schema and call sites.
"""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from cli.commands import wait_handoff

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "wrappers" / "bin"
PS1_DIR = REPO_ROOT / "wrappers" / "ps1"
STATUS_HELPER = BIN_DIR / "_cartopian-status.sh"

# (wrapper filename, upstream CLI binary the wrapper invokes)
BASH_WRAPPERS = [
    ("cartopian-codex", "codex"),
    ("cartopian-claude", "claude"),
    ("cartopian-gemini", "gemini"),
    ("cartopian-devin", "devin"),
]

CONFIG_BODY = """[project]
work_roots = []

[roles]
coder = "Implements tasks per spec."
"""


def _make_project(tmp_path: Path) -> Path:
    """Scaffold a minimal project with a prompt and return the prompt path."""
    project = tmp_path / "proj"
    (project / "prompts").mkdir(parents=True)
    (project / "cartopian.toml").write_text(CONFIG_BODY, encoding="utf-8")
    prompt = project / "prompts" / "PROMPT-01-007.md"
    prompt.write_text("do the thing\n", encoding="utf-8")
    return prompt


def _expected_status_path(prompt: Path) -> Path:
    project = prompt.parent.parent
    return project / "reports" / "REPORT-01-007.md.status"


def _fake_bin(dir_path: Path, name: str, body: str) -> None:
    """Drop an executable shim named ``name`` running ``body`` into ``dir_path``."""
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_wrapper(wrapper: str, prompt: Path, fake_body: str, *, timeout_spec="60m",
                 with_timeout_bin=True):
    """Run a Bash wrapper with a fake assignee on a restricted PATH.

    PATH excludes ``cartopian`` so the wrapper's access-grants step is skipped,
    and contains only the fake assignee plus core utilities (and optionally a
    real ``timeout``). Returns the CompletedProcess.
    """
    tool = dict(BASH_WRAPPERS)[wrapper]
    fakebin = prompt.parent.parent.parent / "fakebin"
    _fake_bin(fakebin, tool, fake_body)

    path_parts = [str(fakebin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    if with_timeout_bin:
        tbin = shutil.which("timeout") or shutil.which("gtimeout")
        if tbin:
            path_parts.insert(1, str(Path(tbin).parent))

    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": os.environ.get("HOME", "/tmp"),
        "CARTOPIAN_TIMEOUT": timeout_spec,
        # Only read by cartopian-devin (ignored by the other wrappers). The
        # bare `exit N` fake assignee models neither the permission-surface nor
        # the --sandbox probe, so both fail and the default `autonomous` mode
        # would fail closed before launch. `bypass` needs no --sandbox, so the
        # wrapper reaches the real run and this test exercises status-file
        # writing on assignee exit (its actual subject), not probe composition.
        "CARTOPIAN_DEVIN_PERMISSION": "bypass",
    }
    return subprocess.run(
        ["bash", str(BIN_DIR / wrapper), str(prompt)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _parse_status(text: str) -> dict:
    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k.strip()] = v.strip()
    return fields


# --- status-path derivation matches the consumer ----------------------------


def test_helper_status_path_matches_report_suffix(tmp_path):
    prompt = _make_project(tmp_path)
    out = subprocess.run(
        ["bash", "-c",
         f'source "{STATUS_HELPER}"; cartopian_status_path "{prompt}"'],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    expected = _expected_status_path(prompt).resolve()
    assert Path(out) == expected
    # The path is exactly the report path with a ".status" suffix.
    assert out.endswith("reports/REPORT-01-007.md.status")


# --- end-to-end: every wrapper writes the status file on assignee exit -------


@pytest.mark.parametrize("wrapper,tool", BASH_WRAPPERS)
def test_wrapper_writes_status_on_nonzero_exit(tmp_path, wrapper, tool):
    prompt = _make_project(tmp_path)
    status_path = _expected_status_path(prompt)

    proc = _run_wrapper(wrapper, prompt, "exit 3")

    assert proc.returncode == 3, proc.stderr
    assert status_path.is_file(), f"{wrapper}: no status file at {status_path}"
    fields = _parse_status(status_path.read_text(encoding="utf-8"))
    assert fields["state"] == "exited"
    assert fields["exit_code"] == "3"
    assert fields["reason"] == "error"

    # Producer/consumer agreement: the real consumer reads our bytes and sees
    # the crash exit code.
    assert wait_handoff._status_exit_code(status_path) == 3


def test_wrapper_clean_exit_is_not_a_crash(tmp_path):
    prompt = _make_project(tmp_path)
    status_path = _expected_status_path(prompt)

    proc = _run_wrapper("cartopian-codex", prompt, "exit 0")

    assert proc.returncode == 0, proc.stderr
    fields = _parse_status(status_path.read_text(encoding="utf-8"))
    assert fields["state"] == "exited"
    assert fields["exit_code"] == "0"
    assert fields["reason"] == "clean"
    # Clean exit must NOT register as a crash for wait-handoff.
    assert wait_handoff._status_exit_code(status_path) is None


@pytest.mark.skipif(
    not (shutil.which("timeout") or shutil.which("gtimeout")),
    reason="no coreutils timeout/gtimeout on PATH",
)
def test_wrapper_timeout_kill_records_124(tmp_path):
    prompt = _make_project(tmp_path)
    status_path = _expected_status_path(prompt)

    # Assignee sleeps past the 1s deadline; the OS timeout kills it (exit 124).
    proc = _run_wrapper("cartopian-codex", prompt, "sleep 30", timeout_spec="1s")

    assert proc.returncode == 124, proc.stderr
    fields = _parse_status(status_path.read_text(encoding="utf-8"))
    assert fields["state"] == "exited"
    assert fields["exit_code"] == "124"
    assert fields["reason"] == "timeout"
    # A timeout kill IS a crash signal for wait-handoff.
    assert wait_handoff._status_exit_code(status_path) == 124


# --- helper unit: timeout reason without depending on a timeout binary -------


def test_helper_write_status_timeout_reason(tmp_path):
    status_path = tmp_path / "reports" / "REPORT-01-007.md.status"
    subprocess.run(
        ["bash", "-c",
         f'source "{STATUS_HELPER}"; '
         f'cartopian_write_status "{status_path}" 124 true'],
        check=True,
    )
    fields = _parse_status(status_path.read_text(encoding="utf-8"))
    assert fields == {"state": "exited", "exit_code": "124", "reason": "timeout"}
    assert wait_handoff._status_exit_code(status_path) == 124


def test_helper_absent_path_is_noop(tmp_path):
    # An empty status path (prompt outside a project layout) must not error.
    res = subprocess.run(
        ["bash", "-c",
         f'source "{STATUS_HELPER}"; cartopian_write_status "" 5 false; echo ok'],
        capture_output=True, text=True, check=True,
    )
    assert res.stdout.strip() == "ok"


# --- PowerShell parity (static; no pwsh in CI) ------------------------------


def test_powershell_helper_mirrors_field_schema():
    text = (PS1_DIR / "CartopianStatus.ps1").read_text(encoding="utf-8")
    # Same three fields, same crash sentinel.
    assert "state=exited" in text
    assert "exit_code=$code" in text
    assert "reason=$reason" in text
    assert "'timeout'" in text and "124" in text
    assert "Get-CartopianStatusPath" in text
    assert "Write-CartopianStatus" in text


@pytest.mark.parametrize("wrapper,_tool", BASH_WRAPPERS)
def test_bash_wrapper_sources_helper_and_writes_status(wrapper, _tool):
    text = (BIN_DIR / wrapper).read_text(encoding="utf-8")
    assert "_cartopian-status.sh" in text
    assert "cartopian_write_status" in text
    # The wrapper must no longer hand off via exec (which would skip the
    # post-exit status write).
    assert not re.search(r"^exec \"\$\{CMD\[@\]\}\"", text, re.MULTILINE), (
        f"{wrapper} still uses exec; status file would never be written"
    )


@pytest.mark.parametrize("wrapper", [
    "cartopian-codex.ps1", "cartopian-claude.ps1",
    "cartopian-gemini.ps1", "cartopian-devin.ps1",
])
def test_ps1_wrapper_dot_sources_helper_and_writes_status(wrapper):
    text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
    assert "CartopianStatus.ps1" in text
    assert "Write-CartopianStatus" in text
