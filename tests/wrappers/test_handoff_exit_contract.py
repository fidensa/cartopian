"""Handoff exit-contract tests.

Regression coverage for the broken handoff completion signal: across a whole
session every PM-launched ``cartopian-claude`` / ``cartopian-codex`` handoff
*wrote a valid report and then lingered* until the wrapper's ``timeout`` killed
it — exit ``124``, ``<report>.status`` ``reason=timeout`` — even though the work
had finished. A successful handoff that always surfaces as a deadline kill is a
broken contract.

The contract these tests pin:

- **Clean exit on report-complete.** An assignee that has written its expected
  report exits ``0`` with ``.status`` ``reason=clean`` *promptly*, well before
  the ``CARTOPIAN_TIMEOUT`` deadline — even if the underlying CLI lingers after
  writing the report (MCP servers / stdio not closing, a trailing turn).
- **Deadline guarantee preserved.** An assignee that genuinely hangs and never
  writes a report still hits the ``CARTOPIAN_TIMEOUT`` deadline and reports
  ``timeout`` (exit ``124``) — the single-timer SSOT is intact.

The wrappers are exercised for real against a fake assignee (no live model), so
the report-completion supervisor and the OS deadline are both tested end to end.
The companion status-file bytes are fed back through the real consumer
(``wait_handoff._status_exit_code``) to assert producer/consumer agreement.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from cli.commands import wait_handoff

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "wrappers" / "bin"

# (wrapper filename, upstream CLI binary the wrapper invokes)
BASH_WRAPPERS = [
    ("cartopian-claude", "claude"),
    ("cartopian-codex", "codex"),
]

_TIMEOUT_BIN = shutil.which("timeout") or shutil.which("gtimeout")
pytestmark = pytest.mark.skipif(
    _TIMEOUT_BIN is None, reason="no coreutils timeout/gtimeout on PATH"
)

# A minimal but *complete* report body — carries the top-level ``Status:`` line
# the supervisor keys off as the authoritative completion signal.
COMPLETE_REPORT = """# REPORT-01-007

Status: complete

## Identity

- Task ID: TASK-01-007

## Ready for review

yes
"""


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / "prompts").mkdir(parents=True)
    (project / "reports").mkdir(parents=True)
    prompt = project / "prompts" / "PROMPT-01-007.md"
    prompt.write_text("do the thing\n", encoding="utf-8")
    return prompt


def _report_path(prompt: Path) -> Path:
    return prompt.parent.parent / "reports" / "REPORT-01-007.md"


def _status_path(prompt: Path) -> Path:
    return prompt.parent.parent / "reports" / "REPORT-01-007.md.status"


def _fake_bin(dir_path: Path, name: str, body: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_wrapper(wrapper: str, tool: str, prompt: Path, fake_body: str, *,
                 timeout_spec="30s", subprocess_timeout=60, extra_env=None):
    fakebin = prompt.parent.parent.parent / "fakebin"
    _fake_bin(fakebin, tool, fake_body)

    path_parts = [str(fakebin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    path_parts.insert(1, str(Path(_TIMEOUT_BIN).parent))

    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": os.environ.get("HOME", "/tmp"),
        "CARTOPIAN_TIMEOUT": timeout_spec,
        # Keep the supervisor's poll/grace tight so the tests run fast.
        "CARTOPIAN_REPORT_POLL": "0.2",
        "CARTOPIAN_REPORT_GRACE_POLLS": "2",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(BIN_DIR / wrapper), str(prompt)],
        env=env, capture_output=True, text=True, timeout=subprocess_timeout,
    )


def _parse_status(text: str) -> dict:
    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k.strip()] = v.strip()
    return fields


# --- the core contract: report-complete then linger => clean exit -----------


@pytest.mark.parametrize("wrapper,tool", BASH_WRAPPERS)
def test_report_complete_then_linger_exits_clean(tmp_path, wrapper, tool):
    """The reported failure: the assignee writes a valid report, then lingers
    (CLI does not exit). The wrapper must detect the authoritative report and
    exit 0 / reason=clean *before* the deadline — not 124 / timeout."""
    prompt = _make_project(tmp_path)
    report = _report_path(prompt)
    status = _status_path(prompt)

    # Fake assignee: write the complete report, then sleep far past nothing —
    # but well within CARTOPIAN_TIMEOUT (30s). The supervisor should reap it.
    body = (
        f"cat > '{report}' <<'REPORT_EOF'\n"
        f"{COMPLETE_REPORT}"
        "REPORT_EOF\n"
        "sleep 25\n"
        "exit 0\n"
    )
    proc = _run_wrapper(wrapper, tool, prompt, body,
                        timeout_spec="30s", subprocess_timeout=20)

    assert proc.returncode == 0, (
        f"{wrapper}: completed handoff did not exit clean "
        f"(rc={proc.returncode}); stderr={proc.stderr}"
    )
    assert status.is_file(), f"{wrapper}: no status file at {status}"
    fields = _parse_status(status.read_text(encoding="utf-8"))
    assert fields["state"] == "exited"
    assert fields["exit_code"] == "0", fields
    assert fields["reason"] == "clean", fields
    # A clean exit must NOT register as a crash for wait-handoff.
    assert wait_handoff._status_exit_code(status) is None


# --- the deadline guarantee: a genuine hang (no report) still times out ------


@pytest.mark.parametrize("wrapper,tool", BASH_WRAPPERS)
def test_genuine_hang_no_report_still_times_out(tmp_path, wrapper, tool):
    """An assignee that never writes a report must still be killed at the
    CARTOPIAN_TIMEOUT deadline (exit 124, reason=timeout). The supervisor must
    not mask a real hang."""
    prompt = _make_project(tmp_path)
    status = _status_path(prompt)

    proc = _run_wrapper(wrapper, tool, prompt, "sleep 30\nexit 0",
                        timeout_spec="1s", subprocess_timeout=30)

    assert proc.returncode == 124, (
        f"{wrapper}: genuine hang was not killed at the deadline "
        f"(rc={proc.returncode}); stderr={proc.stderr}"
    )
    fields = _parse_status(status.read_text(encoding="utf-8"))
    assert fields["exit_code"] == "124", fields
    assert fields["reason"] == "timeout", fields
    assert wait_handoff._status_exit_code(status) == 124


# --- sanity: report-complete AND self-exit is also clean --------------------


@pytest.mark.parametrize("wrapper,tool", BASH_WRAPPERS)
def test_report_complete_and_self_exit_is_clean(tmp_path, wrapper, tool):
    """When the assignee writes its report and exits on its own, the outcome is
    clean (the common non-lingering case stays unchanged)."""
    prompt = _make_project(tmp_path)
    report = _report_path(prompt)
    status = _status_path(prompt)

    body = (
        f"cat > '{report}' <<'REPORT_EOF'\n"
        f"{COMPLETE_REPORT}"
        "REPORT_EOF\n"
        "exit 0\n"
    )
    proc = _run_wrapper(wrapper, tool, prompt, body, timeout_spec="30s")

    assert proc.returncode == 0, proc.stderr
    fields = _parse_status(status.read_text(encoding="utf-8"))
    assert fields["exit_code"] == "0", fields
    assert fields["reason"] == "clean", fields
