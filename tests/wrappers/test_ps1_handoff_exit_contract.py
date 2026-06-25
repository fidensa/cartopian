"""PowerShell handoff exit-contract parity.

The bash wrappers route every launch through ``cartopian_run_supervised``
(``bin/_cartopian-status.sh``) so a *finished* assignee — one that has written
its authoritative report and then lingers — exits ``0``/``reason=clean``
promptly instead of idling to the ``CARTOPIAN_TIMEOUT`` deadline (false
``124``/``reason=timeout``). This module pins the PowerShell mirror:
``Invoke-CartopianSupervisedRun`` / ``Test-CartopianReportComplete`` in
``ps1/CartopianStatus.ps1`` and the four ``.ps1`` wrappers routed through it.

Two layers (the project's standing posture for PS1 coverage — see
``test_ps1_model_flag.py`` / ``test_ps1_work_root_guard.py``):

* **Static parity** — always runs. Asserts the helper functions exist with the
  contract-bearing shapes (report regex byte-parity with the bash helper;
  deadline computed exactly once from ``-TimeoutSec``; report-authoritative
  final check) and that every wrapper derives the watched report path from the
  status path, calls the supervisor, and no longer owns a second inline
  ``Start-Process``/``WaitForExit`` deadline (the only remaining occurrences
  live in the helper-absent fallback stub).
* **Behavioral** — runs only where ``pwsh`` is available (skipped otherwise;
  ``pwsh`` is not installed on the primary dev host). Exercises the real
  ``.ps1`` wrappers against a fake assignee: report-then-linger exits clean
  before the deadline; a genuine hang still times out (124); the status-file
  bytes feed back through the real consumer. **Windows-host execution evidence
  remains open** — running these on a genuine Windows host is the
  remaining behavioral evidence gate.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PS1_DIR = REPO_ROOT / "wrappers" / "ps1"
BIN_DIR = REPO_ROOT / "wrappers" / "bin"
HELPER = PS1_DIR / "CartopianStatus.ps1"
BASH_HELPER = BIN_DIR / "_cartopian-status.sh"

PS1_WRAPPERS = [
    ("cartopian-claude.ps1", "claude"),
    ("cartopian-codex.ps1", "codex"),
    ("cartopian-gemini.ps1", "gemini"),
    ("cartopian-devin.ps1", "devin"),
]

PWSH = shutil.which("pwsh")

# The two report-completion patterns that MUST stay semantically identical:
# a top-level `Status: <complete|blocked|failed>` line, case-insensitive,
# rejecting the unfilled template placeholder (`Status: <complete | ...>`).
PS1_REPORT_PATTERN = r"'^(?i)Status:\s*(complete|blocked|failed)(\s|$)'"
BASH_REPORT_PATTERN = r"'^Status:[[:space:]]*(complete|blocked|failed)([[:space:]]|$)'"

COMPLETE_REPORT = """# REPORT-01-007

Status: complete

## Identity

- Task ID: TASK-01-007

## Ready for review

yes
"""


def _helper_text() -> str:
    return HELPER.read_text(encoding="utf-8")


def _supervisor_body(text: str) -> str:
    """The body of Invoke-CartopianSupervisedRun (brace-matched)."""
    start = text.index("function Invoke-CartopianSupervisedRun")
    open_idx = text.index("{", start)
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError("unbalanced braces in Invoke-CartopianSupervisedRun")


# --- static parity: the shared helper --------------------------------------


class TestHelperStatic:
    def test_helper_defines_supervisor_functions(self):
        text = _helper_text()
        assert "function Test-CartopianReportComplete" in text
        assert "function Invoke-CartopianSupervisedRun" in text

    def test_report_complete_regex_parity_with_bash(self):
        """The producer-side completion proxy must stay byte-parallel between
        the bash and PowerShell helpers: same Status values, same top-level
        line anchor, same case-insensitivity, same placeholder rejection."""
        assert PS1_REPORT_PATTERN in _helper_text(), (
            "PS1 report-complete pattern drifted from the pinned shape"
        )
        bash_text = BASH_HELPER.read_text(encoding="utf-8")
        assert BASH_REPORT_PATTERN in bash_text, (
            "bash report-complete pattern drifted; update BOTH helpers and BOTH pins together"
        )

    def test_single_deadline_no_second_timer(self):
        """CARTOPIAN_TIMEOUT SSOT: the supervisor computes its deadline exactly
        once from -TimeoutSec and never extends it. The report watch reuses the
        same wait loop (poll-sized WaitForExit slices) — no second clock."""
        body = _supervisor_body(_helper_text())
        assert body.count("AddSeconds($TimeoutSec)") == 1, (
            "the supervisor must compute the SSOT deadline exactly once"
        )
        assert "AddSeconds" not in body.replace("AddSeconds($TimeoutSec)", "", 1), (
            "no second deadline may be derived inside the supervisor"
        )

    def test_report_is_authoritative_after_the_run(self):
        """A complete report yields ExitCode 0 regardless of how the lingering
        child was reaped (even when the reap raced the deadline) — mirroring
        the bash helper's final cartopian_report_complete override."""
        body = _supervisor_body(_helper_text())
        tail = body[body.rindex("finally"):]
        assert "Test-CartopianReportComplete $ReportPath" in tail
        assert "ExitCode = 0" in tail
        assert "ExitCode = 124" in tail

    def test_supervisor_closes_stdin(self):
        """stdin must be redirected (immediate EOF) so the child can never
        block on inherited terminal input — one of the lingering modes."""
        body = _supervisor_body(_helper_text())
        assert "RedirectStandardInput" in body

    def test_supervisor_honors_poll_and_grace_tunables(self):
        body = _supervisor_body(_helper_text())
        assert "CARTOPIAN_REPORT_POLL" in body
        assert "CARTOPIAN_REPORT_GRACE_POLLS" in body


# --- static parity: every wrapper is routed through the supervisor ----------


class TestWrapperRouting:
    @pytest.mark.parametrize("wrapper,_tool", PS1_WRAPPERS)
    def test_wrapper_derives_report_path_via_shared_helper(self, wrapper, _tool):
        """The .status→report suffix mapping has ONE home (Get-CartopianReportPath
        in CartopianStatus.ps1); wrappers call it instead of inlining copies."""
        text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
        assert "$ReportPath = Get-CartopianReportPath $StatusPath" in text, (
            f"{wrapper}: must derive the watched report path via the shared helper"
        )
        helper = _helper_text()
        assert "function Get-CartopianReportPath" in helper
        assert "-replace '\\.status$', ''" in helper, (
            "the suffix mapping must live in the shared helper"
        )

    @pytest.mark.parametrize("wrapper,tool", PS1_WRAPPERS)
    def test_wrapper_calls_supervisor_with_ssot_timeout(self, wrapper, tool):
        text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
        call = (
            f"Invoke-CartopianSupervisedRun -ReportPath $ReportPath "
            f"-FilePath {tool} -ArgumentList $Args -TimeoutSec $TimeoutSec"
        )
        assert call in text, f"{wrapper}: not routed through the supervisor"
        # $TimeoutSec must still be derived from the CARTOPIAN_TIMEOUT SSOT.
        assert "$TimeoutSpec = if ($env:CARTOPIAN_TIMEOUT)" in text

    @pytest.mark.parametrize("wrapper,_tool", PS1_WRAPPERS)
    def test_wrapper_owns_no_second_inline_deadline(self, wrapper, _tool):
        """The wrapper must never spawn or deadline-wait the ASSIGNEE itself:
        no `Start-Process ... -ArgumentList $Args` launch may exist (the
        supervisor owns the assignee spawn and the single SSOT deadline). The
        only permitted spawns are the fallback stub's generic
        `-ArgumentList $ArgumentList` run and, in cartopian-devin.ps1, the
        bounded 10s surface PROBE (not an assignee deadline)."""
        text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
        assert not re.search(r"Start-Process[^\n]*-ArgumentList \$Args\b", text), (
            f"{wrapper}: inline assignee launch found outside the supervisor"
        )
        expected = 2 if wrapper == "cartopian-devin.ps1" else 1
        assert text.count("Start-Process") == expected, (
            f"{wrapper}: unexpected Start-Process count "
            f"(stub{' + surface probe' if expected == 2 else ''} only)"
        )
        assert text.count("WaitForExit") == expected, (
            f"{wrapper}: unexpected WaitForExit count"
        )
        stub = text[text.index("function Invoke-CartopianSupervisedRun"):]
        stub = stub[:stub.index("\n}") + 2]
        assert "Start-Process" in stub and "WaitForExit" in stub, (
            f"{wrapper}: the fallback stub must own its spawn/wait"
        )

    @pytest.mark.parametrize("wrapper,_tool", PS1_WRAPPERS)
    def test_wrapper_status_file_reflects_supervised_outcome(self, wrapper, _tool):
        text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
        assert (
            "Write-CartopianStatus -StatusPath $StatusPath "
            "-ExitCode $run.ExitCode -TimedOut $run.TimedOut" in text
        ), f"{wrapper}: status file must capture the supervised outcome"
        assert "exit $run.ExitCode" in text


# --- behavioral (pwsh required; Windows-host evidence remains open) ---------

pwsh_required = pytest.mark.skipif(
    PWSH is None,
    reason="pwsh not available on this host (behavioral parity runs on "
    "pwsh hosts; Windows-host evidence gate)",
)


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


def _fake_tool(dir_path: Path, name: str, sh_body: str) -> None:
    """A fake assignee CLI: POSIX sh shim plus a .cmd twin for Windows hosts."""
    dir_path.mkdir(parents=True, exist_ok=True)
    sh = dir_path / name
    sh.write_text("#!/bin/sh\n" + sh_body + "\n", encoding="utf-8")
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    if os.name == "nt":  # pragma: no cover - exercised on Windows hosts only
        (dir_path / f"{name}.cmd").write_text(
            "@echo off\r\nsh %~dp0" + name + " %*\r\n", encoding="utf-8"
        )


def _run_ps1_wrapper(wrapper: str, tool: str, prompt: Path, fake_body: str, *,
                     timeout_spec="30s", subprocess_timeout=60):
    fakebin = prompt.parent.parent.parent / "fakebin"
    _fake_tool(fakebin, tool, fake_body)
    env = {
        "PATH": os.pathsep.join([str(fakebin), os.environ.get("PATH", "")]),
        "HOME": os.environ.get("HOME", "/tmp"),
        "CARTOPIAN_TIMEOUT": timeout_spec,
        "CARTOPIAN_REPORT_POLL": "0.2",
        "CARTOPIAN_REPORT_GRACE_POLLS": "2",
    }
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(PS1_DIR / wrapper), str(prompt)],
        env=env, capture_output=True, text=True, timeout=subprocess_timeout,
    )


def _parse_status(text: str) -> dict:
    fields = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.strip().split("=", 1)
            fields[k] = v
    return fields


# claude/codex carry no surface probe; devin's pwsh path needs a help-aware
# fake, so the behavioral matrix mirrors the bash contract tests' wrapper set.
BEHAVIORAL = [("cartopian-claude.ps1", "claude"), ("cartopian-codex.ps1", "codex")]


@pwsh_required
@pytest.mark.parametrize("wrapper,tool", BEHAVIORAL)
def test_ps1_report_complete_then_linger_exits_clean(tmp_path, wrapper, tool):
    """An assignee that writes a valid report then lingers must exit 0/clean
    promptly — not 124/timeout at the deadline."""
    prompt = _make_project(tmp_path)
    report = _report_path(prompt)
    status = _status_path(prompt)
    body = (
        f"cat > '{report}' <<'REPORT_EOF'\n{COMPLETE_REPORT}REPORT_EOF\n"
        "sleep 25\nexit 0"
    )
    proc = _run_ps1_wrapper(wrapper, tool, prompt, body,
                            timeout_spec="30s", subprocess_timeout=25)
    assert proc.returncode == 0, (
        f"{wrapper}: completed handoff did not exit clean "
        f"(rc={proc.returncode}); stderr={proc.stderr}"
    )
    fields = _parse_status(status.read_text(encoding="utf-8"))
    assert fields["state"] == "exited"
    assert fields["exit_code"] == "0", fields
    assert fields["reason"] == "clean", fields
    from cli.commands import wait_handoff
    assert wait_handoff._status_exit_code(status) is None


@pwsh_required
@pytest.mark.parametrize("wrapper,tool", BEHAVIORAL)
def test_ps1_genuine_hang_no_report_still_times_out(tmp_path, wrapper, tool):
    """A genuine hang (no report) must still hit the CARTOPIAN_TIMEOUT
    deadline: exit 124, reason=timeout — the supervisor masks nothing."""
    prompt = _make_project(tmp_path)
    status = _status_path(prompt)
    proc = _run_ps1_wrapper(wrapper, tool, prompt, "sleep 30\nexit 0",
                            timeout_spec="2s", subprocess_timeout=30)
    assert proc.returncode == 124, (
        f"{wrapper}: genuine hang was not killed at the deadline "
        f"(rc={proc.returncode}); stderr={proc.stderr}"
    )
    fields = _parse_status(status.read_text(encoding="utf-8"))
    assert fields["exit_code"] == "124", fields
    assert fields["reason"] == "timeout", fields
    from cli.commands import wait_handoff
    assert wait_handoff._status_exit_code(status) == 124


@pwsh_required
@pytest.mark.parametrize("wrapper,tool", BEHAVIORAL)
def test_ps1_report_complete_and_self_exit_is_clean(tmp_path, wrapper, tool):
    prompt = _make_project(tmp_path)
    report = _report_path(prompt)
    status = _status_path(prompt)
    body = f"cat > '{report}' <<'REPORT_EOF'\n{COMPLETE_REPORT}REPORT_EOF\nexit 0"
    proc = _run_ps1_wrapper(wrapper, tool, prompt, body, timeout_spec="30s")
    assert proc.returncode == 0, proc.stderr
    fields = _parse_status(status.read_text(encoding="utf-8"))
    assert fields["exit_code"] == "0", fields
    assert fields["reason"] == "clean", fields


@pwsh_required
def test_ps1_helper_report_complete_semantics(tmp_path):
    """Direct pwsh check of Test-CartopianReportComplete: complete/blocked/
    failed match; the unfilled template placeholder and an empty file do not."""
    cases = [
        ("Status: complete\n", True),
        ("Status: blocked\n", True),
        ("Status: FAILED\n", True),
        ("Status: <complete | blocked | failed>\n", False),
        ("", False),
        ("status of the work: complete\n", False),
    ]
    script_lines = [f". '{HELPER}'"]
    for i, (content, _expected) in enumerate(cases):
        p = tmp_path / f"r{i}.md"
        p.write_text(content, encoding="utf-8")
        script_lines.append(f"Write-Output ([bool](Test-CartopianReportComplete '{p}'))")
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-Command", "; ".join(script_lines)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    got = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    expected = ["True" if e else "False" for _c, e in cases]
    assert got == expected, f"got={got} expected={expected}"


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
