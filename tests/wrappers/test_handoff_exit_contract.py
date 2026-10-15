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
import time
from pathlib import Path
from unittest import mock

import pytest

from cli import output_safety
from cli.commands import wait_handoff

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "wrappers" / "bin"

# (wrapper filename, upstream CLI binary the wrapper invokes)
BASH_WRAPPERS = [
    ("cartopian-claude", "claude"),
    ("cartopian-codex", "codex"),
    ("cartopian-opencode", "opencode"),
    ("cartopian-hermes", "hermes"),
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

## Completion evidence

The handoff completed.

## Remaining risks

None.

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


def _wrapper_env(fakebin: Path, timeout_spec: str, extra_env=None) -> dict:
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
    return env


def _run_wrapper(wrapper: str, tool: str, prompt: Path, fake_body: str, *,
                 timeout_spec="30s", subprocess_timeout=60, extra_env=None):
    fakebin = prompt.parent.parent.parent / "fakebin"
    _fake_bin(fakebin, tool, fake_body)
    env = _wrapper_env(fakebin, timeout_spec, extra_env)
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


# --- stopping the watcher releases the caller's output promptly --------------


@pytest.mark.parametrize("with_report", [True, False])
@pytest.mark.parametrize("wrapper,tool", BASH_WRAPPERS)
def test_self_exit_releases_output_without_waiting_out_the_poll(
    tmp_path, wrapper, tool, with_report
):
    """A finished handoff returns its output without waiting out a poll.

    The watcher used to be killed mid-``sleep``, and the orphaned sleep held
    the wrapper's stdout/stderr open, so every caller that captures output
    (dispatch, tests) waited up to one full poll interval after the assignee
    had already exited. With a deliberately long poll, the captured run must
    still finish well inside it and leave no watcher sleep behind.
    """
    import time

    prompt = _make_project(tmp_path)
    report = _report_path(prompt)
    body = (
        f"cat > '{report}' <<'REPORT_EOF'\n{COMPLETE_REPORT}REPORT_EOF\n"
        if with_report
        else ""
    ) + "exit 0\n"
    started = time.monotonic()
    proc = _run_wrapper(
        wrapper, tool, prompt, body, timeout_spec="30s",
        extra_env={"CARTOPIAN_REPORT_POLL": "7"},
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 5, f"{wrapper}: captured output held for {elapsed:.1f}s"
    if shutil.which("pgrep"):
        stray = subprocess.run(
            ["pgrep", "-f", "^sleep 7$"], capture_output=True, text=True
        )
        assert stray.stdout.strip() == "", f"{wrapper}: orphaned watcher sleep"


# --- the reap reaches the assignee, not only `timeout` ----------------------


def _pid_gone(pid: int, within: float = 3.0) -> bool:
    deadline = time.monotonic() + within
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _supervise_wrapper(wrapper: str, tool: str, prompt: Path, fake_body: str,
                       *, wrapper_env=None):
    """Run the wrapper under the real dispatch supervisor, as dispatch does."""
    fakebin = prompt.parent.parent.parent / "fakebin"
    _fake_bin(fakebin, tool, fake_body)
    env = _wrapper_env(fakebin, "30s", wrapper_env)
    report = _report_path(prompt)
    with mock.patch.dict(
        os.environ,
        {
            output_safety.REPORT_POLL_ENV: "0.2",
            output_safety.REPORT_GRACE_POLLS_ENV: "1",
        },
    ):
        return output_safety.supervise(
            ["env", "-i", *(f"{k}={v}" for k, v in env.items()),
             "bash", str(BIN_DIR / wrapper), str(prompt)],
            status_path=_status_path(prompt),
            report_path=report,
            log_path=Path(str(report) + ".launch.log"),
            launch_id="reap-test",
            expected_variant="task",
            limits=output_safety.OutputLimits(),
        )


@pytest.mark.parametrize("wrapper,tool", BASH_WRAPPERS)
def test_wrapper_reap_does_not_orphan_a_slow_to_stop_assignee(
    tmp_path, wrapper, tool
):
    """`timeout` puts the agent CLI in its own process group, and a SIGKILL
    sent to `timeout` alone cannot be forwarded. An assignee that does not
    stop on TERM must still be gone when the wrapper reports its exit,
    instead of surviving as an orphan that can rewrite the report."""
    prompt = _make_project(tmp_path)
    report = _report_path(prompt)
    pidfile = tmp_path / "assignee.pid"
    body = (
        "trap '' TERM\n"
        f"echo $$ > '{pidfile}'\n"
        f"cat > '{report}' <<'REPORT_EOF'\n{COMPLETE_REPORT}REPORT_EOF\n"
        "exec sleep 25\n"
    )
    proc = _run_wrapper(wrapper, tool, prompt, body,
                        timeout_spec="30s", subprocess_timeout=20)

    assert proc.returncode == 0, proc.stderr
    pid = int(pidfile.read_text().strip())
    assert _pid_gone(pid), f"{wrapper}: assignee {pid} outlived the wrapper"


@pytest.mark.parametrize("wrapper,tool", BASH_WRAPPERS)
def test_supervisor_reap_reaches_assignee_outside_wrapper_group(
    tmp_path, wrapper, tool
):
    """The dispatch supervisor reaps the wrapper's process group, which does
    not contain the `timeout`-launched assignee. The wrapper forwards that
    TERM to the assignee, so the launch ends promptly with the writer gone.
    The wrapper's own watcher is given a long grace so the supervisor's reap
    is the one under test."""
    prompt = _make_project(tmp_path)
    report = _report_path(prompt)
    pidfile = tmp_path / "assignee.pid"
    body = (
        f"echo $$ > '{pidfile}'\n"
        f"cat > '{report}' <<'REPORT_EOF'\n{COMPLETE_REPORT}REPORT_EOF\n"
        "exec sleep 25\n"
    )
    started = time.monotonic()
    result = _supervise_wrapper(
        wrapper, tool, prompt, body,
        wrapper_env={"CARTOPIAN_REPORT_GRACE_POLLS": "100"},
    )
    elapsed = time.monotonic() - started

    assert result.exit_code == 0
    pid = int(pidfile.read_text().strip())
    assert _pid_gone(pid, within=0.0), (
        f"{wrapper}: state=exited published while assignee {pid} was alive"
    )
    assert elapsed < 10, f"{wrapper}: reap took {elapsed:.1f}s"
    assert output_safety._WRITER_SURVIVED_MARKER not in Path(
        result.retained_log_path
    ).read_bytes()


@pytest.mark.parametrize("wrapper,tool", BASH_WRAPPERS)
def test_supervisor_waits_for_assignee_that_survives_the_reap(
    tmp_path, wrapper, tool
):
    """The reported anomaly: the completion report was replaced on disk after
    the clean exited status was published. An assignee that survives every
    signal the reap can deliver must still hold publication until it exits,
    so the exited status names the bytes it left behind."""
    prompt = _make_project(tmp_path)
    report = _report_path(prompt)
    rewritten = COMPLETE_REPORT.replace(
        "The handoff completed.", "The handoff completed, then revised."
    )
    body = (
        "trap '' TERM\n"
        f"cat > '{report}' <<'REPORT_EOF'\n{COMPLETE_REPORT}REPORT_EOF\n"
        "sleep 4\n"
        f"cat > '{report}.tmp' <<'REPORT_EOF'\n{rewritten}REPORT_EOF\n"
        f"mv -f '{report}.tmp' '{report}'\n"
    )
    result = _supervise_wrapper(
        wrapper, tool, prompt, body,
        wrapper_env={"CARTOPIAN_REPORT_GRACE_POLLS": "100"},
    )

    assert result.exit_code == 0
    assert report.read_text(encoding="utf-8") == rewritten, (
        f"{wrapper}: state=exited published before the surviving writer "
        "finished rewriting the report"
    )
    fields = _parse_status(_status_path(prompt).read_text(encoding="utf-8"))
    assert fields["state"] == "exited"
    assert output_safety._WRITER_SURVIVED_MARKER in Path(
        result.retained_log_path
    ).read_bytes()


# --- readiness token may carry a rationale ----------------------------------


@pytest.mark.parametrize("wrapper,tool", BASH_WRAPPERS)
def test_readiness_rationale_still_counts_as_complete(tmp_path, wrapper, tool):
    """`no — closure is not mine to certify` is a finished report.

    The readiness value is a yes/no token optionally followed by a rationale
    on the same line (the REPORT-05-010 form). The producer-side completion
    proxy must agree with the canonical parser, or a finished handoff lingers
    to the deadline and reads as a timeout.
    """
    prompt = _make_project(tmp_path)
    report = _report_path(prompt)
    status = _status_path(prompt)

    rationale_report = COMPLETE_REPORT.replace(
        "## Ready for review\n\nyes\n",
        "## Ready to close\n\nno — closure is not mine to certify\n",
    )
    body = (
        f"cat > '{report}' <<'REPORT_EOF'\n"
        f"{rationale_report}"
        "REPORT_EOF\n"
        "sleep 25\n"
        "exit 0\n"
    )
    proc = _run_wrapper(wrapper, tool, prompt, body,
                        timeout_spec="30s", subprocess_timeout=20)

    assert proc.returncode == 0, (
        f"{wrapper}: rationale-suffixed readiness was not treated as a "
        f"finished report (rc={proc.returncode}); stderr={proc.stderr}"
    )
    fields = _parse_status(status.read_text(encoding="utf-8"))
    assert fields["reason"] == "clean", fields


# --- hermes exit contract: `2` (failed/partial, no text) is a failure --------


def test_hermes_exit_two_propagates_as_failure(tmp_path):
    """Hermes one-shot exits 2 when the run reports failed/partial with no
    text; the wrapper needs no mapping — the exit propagates verbatim and the
    status file records it as a crash signal for wait-handoff."""
    prompt = _make_project(tmp_path)
    proc = _run_wrapper("cartopian-hermes", "hermes", prompt, "exit 2")
    assert proc.returncode == 2, (
        f"hermes exit 2 must propagate verbatim. stderr={proc.stderr!r}"
    )
    status = _status_path(prompt)
    fields = _parse_status(status.read_text(encoding="utf-8"))
    assert fields["exit_code"] == "2", fields
    assert wait_handoff._status_exit_code(status) == 2
