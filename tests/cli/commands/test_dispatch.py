"""Tests for `cartopian dispatch` — mediated handoff launch.

Evidence gate (red-before-green):

- RED: before this command existed, ``cartopian dispatch ...`` was an unknown
  subcommand — a contained PM (no shell / process-exec tool) had no route at
  all to launch a wrapper.
- GREEN (``TestDispatchPositive``): the mediated command launches a *stub*
  wrapper with the single absolute-prompt-path argv, ``CARTOPIAN_TIMEOUT``
  exported from resolved ``roles.<role>.timeout``, and cwd = the
  cartopian project root; the PM then observes completion via
  ``cartopian wait-handoff`` — never spawning a process itself.

Plus the fail-closed cases (unmapped / missing work root, missing role block,
missing prompt) and the no-raw-exec containment negative test asserting the
mediated command is the only process-launch route on the PM tool surface.

No real agent is ever spawned: every launch targets a fast, local stub
executable written into the test's temp tree.
"""
import argparse
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest

from cli import handoff_observer, host_capability, request_trace
from cli.commands import dispatch, report_action, wait_handoff
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, build_parser
from tests.scaffold import project_scaffold


@pytest.fixture(autouse=True)
def _direct_cli_host_environment(monkeypatch):
    """Most dispatch tests exercise direct CLI execution, not an MCP host."""
    for name in (
        host_capability.CONNECTED_ENV,
        host_capability.CLIENT_ENV,
        host_capability.CLIENT_VERSION_ENV,
        host_capability.CLIENT_TITLE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

# A minimal task-completion report the stub wrapper writes so `wait-handoff`
# observes a terminal `done`. Must satisfy parse_report's task-variant schema
# (all required sections + identity keys, Status: complete).
_STUB_REPORT = """# REPORT-{nn}

Status: complete

## Identity

- Task ID: TASK-{nn}
- Prompt path: {prompt}
- Task path: {task}
- Work root: n/a

## Files changed

- n/a — stub wrapper, no product changes

## Test evidence

- n/a — stub

## Commit / PR

- Commit SHA: n/a
- PR URL: n/a

## Remaining risks

none

## Ready for review

yes
"""

# A fake wrapper executable. It records the launch contract (argv, the
# CARTOPIAN_TIMEOUT it was handed, and its cwd) to STUB_CAPTURE, optionally
# sleeps (to prove dispatch does not block to completion), then writes the
# expected report so wait-handoff sees `done`. Pure stdlib; never a real agent.
_STUB_SOURCE = r'''#!/usr/bin/env python3
import json, os, sys, time

prompt = sys.argv[1] if len(sys.argv) > 1 else None
capture = os.environ.get("STUB_CAPTURE")
if capture:
    with open(capture, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "argv": sys.argv,
                "timeout": os.environ.get("CARTOPIAN_TIMEOUT"),
                "model": os.environ.get("CARTOPIAN_MODEL"),
                "effort": os.environ.get("CARTOPIAN_EFFORT"),
                "work_roots": os.environ.get("CARTOPIAN_WORK_ROOTS"),
                "mcp_connected": os.environ.get("CARTOPIAN_MCP_CONNECTED"),
                "mcp_client": os.environ.get("CARTOPIAN_MCP_CLIENT"),
                "mcp_client_version": os.environ.get("CARTOPIAN_MCP_CLIENT_VERSION"),
                "mcp_client_title": os.environ.get("CARTOPIAN_MCP_CLIENT_TITLE"),
                "mcp_tool_call": os.environ.get("CARTOPIAN_MCP_TOOL_CALL"),
                "mcp_host": os.environ.get("CARTOPIAN_MCP_HOST"),
                "hermes_home": os.environ.get("CARTOPIAN_HERMES_HOME"),
                "cwd": os.getcwd(),
            },
            fh,
        )

launch_log = os.environ.get("STUB_LAUNCH_LOG")
if launch_log:
    with open(launch_log, "a", encoding="utf-8") as fh:
        fh.write("%s\n" % os.getpid())

time.sleep(float(os.environ.get("STUB_SLEEP", "0")))

# Detached-stdio probe (both env-gated; default no-op). STUB_GATE parks the
# stub until the test signals that the dispatch caller and its captured pipes
# are gone; STUB_STDIO_PROBE then writes to stdout/stderr and reads stdin. If
# any stream were still an inherited caller-owned pipe, the flush would raise
# BrokenPipeError here and the report below would never be written.
gate = os.environ.get("STUB_GATE")
if gate:
    deadline = time.time() + 30.0
    while not os.path.exists(gate) and time.time() < deadline:
        time.sleep(0.05)

if os.environ.get("STUB_STDIO_PROBE") == "1":
    sys.stdout.write("stub-stdout-probe\n")
    sys.stdout.flush()
    sys.stderr.write("stub-stderr-probe\n")
    sys.stderr.flush()
    sys.stdin.read()

if prompt and os.environ.get("STUB_NO_REPORT") != "1":
    prompts_dir = os.path.dirname(os.path.abspath(prompt))
    root = os.path.dirname(prompts_dir)
    base = os.path.basename(prompt)           # PROMPT-NN-NNN.md
    nn = base[len("PROMPT-"):-len(".md")]
    report = os.path.join(root, "reports", "REPORT-%s.md" % nn)
    os.makedirs(os.path.dirname(report), exist_ok=True)
    task = os.path.join(root, "tasks", "in-progress", "TASK-%s.md" % nn)
    text = open(os.environ["STUB_REPORT_TEMPLATE"], encoding="utf-8").read()
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(text.format(nn=nn, prompt=prompt, task=task))
'''


def _make_stub(dir_path: Path) -> Path:
    """Write the stub wrapper executable into ``dir_path`` and chmod +x it."""
    stub = dir_path / "stub-wrapper"
    stub.write_text(_STUB_SOURCE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _toml(
    agent: str,
    *,
    work_roots: str = "",
    timeout: str = "30m",
    model: str = "",
    effort: str = "",
    auto_launch_tasks: bool = True,
    auto_launch_reviews: "bool | None" = None,
) -> str:
    wr = f'work_roots = [{work_roots}]\n' if work_roots else ""
    model_line = f'model = "{model}"\n' if model else ""
    effort_line = f'effort = "{effort}"\n' if effort else ""
    activities = []
    if auto_launch_tasks:
        activities.append("task_run")
    if auto_launch_reviews:
        activities.append("planning_review")
    auto_launch_line = (
        "auto_launch = ["
        + ", ".join(f'"{activity}"' for activity in activities)
        + "]\n"
    )
    review_policy = (
        "\n[reviews]\n"
        'planning = "required"\n'
        'planning_role = "coder"\n'
        if auto_launch_reviews
        else ""
    )
    return (
        "[project]\n"
        'id = "dispatch-proj"\n'
        'name = "Dispatch Project"\n'
        'project_schema_version = "v0.10.0"\n'
        f"{wr}"
        "\n"
        "[roles.coder]\n"
        'description = "Implements tasks per spec."\n'
        f"{auto_launch_line}"
        f'agent = "{agent}"\n'
        f"{model_line}"
        f"{effort_line}"
        f'timeout = "{timeout}"\n'
        f"{review_policy}"
    )


def _write_task_and_prompt(scaffold, nn_nnn: str = "01-004") -> Path:
    task_path = scaffold.write(
        f"tasks/in-progress/TASK-{nn_nnn}.md",
        (
            f"# TASK-{nn_nnn}: Mediated dispatch\n\n"
            "Phase: PHASE-01\n"
            "Work root: tool-repo\n"
            "Assignee: coder\n\n"
            "## Goal\n\nLaunch via mediated dispatch.\n"
        ),
    )
    scaffold.capture_request(
        request_id=f"REQUEST-{nn_nnn.replace('-', '')[-3:]}",
        unit=f"task:TASK-{nn_nnn}",
        text="Launch only the requested mediated task.",
    )
    context = request_trace.context_for_task_assignment(
        scaffold.project_root, task_path
    )
    scaffold.write(
        f"prompts/PROMPT-{nn_nnn}.md",
        request_trace.upsert_request_sections(
            f"# PROMPT-{nn_nnn}\n\n## Your task\n\nDo the work.\n",
            context.section,
        ),
    )
    return task_path


def _dispatch(task_path, role: str, fake_home: Path, prompt=None):
    """Invoke dispatch.handler with a fake HOME so the real global config can't leak."""
    args = argparse.Namespace(task_path=task_path, prompt=prompt, role=role)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch("cli.commands.dispatch.Path.home", return_value=fake_home):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dispatch.handler(args)
    return out.getvalue(), err.getvalue(), rc


class TestDispatchPositive(unittest.TestCase):
    """GREEN: dispatch launches the stub wrapper with the correct launch
    contract and the PM observes the result through wait-handoff."""

    def test_launches_wrapper_and_pm_observes_via_wait_handoff(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            template = tmp_path / "report-template.txt"
            template.write_text(_STUB_REPORT, encoding="utf-8")
            capture = tmp_path / "capture.json"

            work_root = scaffold.project_root / "tool-repo"
            docs_root = scaffold.project_root / "docs-repo"
            work_root.mkdir()
            docs_root.mkdir()
            scaffold.write(
                "cartopian.toml",
                _toml(
                    str(stub), work_roots='"tool-repo", "docs-repo"',
                    model="stub-model-x", effort="high",
                ),
            )
            scaffold.write(
                "cartopian.local.toml",
                (
                    f'[work_roots]\ntool-repo = "{work_root}"\n'
                    f'docs-repo = "{docs_root}"\n'
                ),
            )
            task_path = _write_task_and_prompt(scaffold)
            # dispatch resolves the project root and prompt path (symlinks
            # collapsed, e.g. /tmp -> /private/tmp on macOS); compare against
            # the same canonical form.
            project_root = scaffold.project_root.resolve()
            prompt_path = (scaffold.prompts / "PROMPT-01-004.md").resolve()

            fake_home = tmp_path / "home"
            fake_home.mkdir()

            env = {
                "STUB_CAPTURE": str(capture),
                "STUB_REPORT_TEMPLATE": str(template),
                "STUB_SLEEP": "0.6",
            }
            start = time.monotonic()
            with mock.patch.dict(os.environ, env, clear=False):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)
            elapsed = time.monotonic() - start

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")

            # NDJSON contract: exactly one record naming the dispatch.
            lines = [ln for ln in stdout.split("\n") if ln]
            self.assertEqual(len(lines), 1, msg=f"stdout={stdout!r}")
            rec = json.loads(lines[0])
            self.assertEqual(rec["status"], "dispatched")
            self.assertEqual(rec["role"], "coder")
            self.assertEqual(rec["launch"]["agent"], str(stub))
            self.assertEqual(rec["launch"]["model"], "stub-model-x")
            self.assertEqual(rec["launch"]["effort"], "high")
            self.assertEqual(rec["prompt_path"], str(prompt_path))
            self.assertEqual(rec["timeout"], "30m")
            # Neutral launcher: cwd is the cartopian project root (the agent
            # opens the prompt it is handed). The wrapper is not the security
            # boundary, but agent CLIs that self-sandbox at the launch cwd
            # need the resolved work roots to widen their sandbox — dispatch
            # exports them (CARTOPIAN_WORK_ROOTS) and records them.
            self.assertEqual(Path(rec["cwd"]).resolve(), project_root)
            self.assertTrue(rec["expected_report_path"].endswith("/reports/REPORT-01-004.md"))
            self.assertEqual(
                rec["work_roots"],
                [str(work_root), str(docs_root)],
            )
            self.assertNotIn("scope_dirs", rec)
            self.assertNotIn("recapture", rec)

            # Non-blocking: dispatch returned well before the stub's 0.6s sleep.
            self.assertLess(
                elapsed, 0.5,
                msg=f"dispatch blocked to completion ({elapsed:.3f}s); it must return at launch",
            )

            # The PM observes completion ONLY through wait-handoff (it never
            # spawned the process and never waits on it directly).
            wait_args = argparse.Namespace(
                task_path=str(task_path),
                role="coder",
                max_block="10s",
                poll_interval=0.05,
            )
            wout, werr = io.StringIO(), io.StringIO()
            with mock.patch("cli.commands.wait_handoff.Path.home", return_value=fake_home):
                with contextlib.redirect_stdout(wout), contextlib.redirect_stderr(werr):
                    wrc = wait_handoff.handler(wait_args)
            wrec = json.loads(wout.getvalue().strip())
            self.assertEqual(wrec["status"], "done", msg=f"wait stderr={werr.getvalue()!r}")
            self.assertEqual(wrc, EXIT_OK)

            # The stub recorded the exact launch contract dispatch handed it.
            self.assertTrue(capture.is_file(), "stub wrapper did not run")
            cap = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(cap["argv"], [str(stub), str(prompt_path)])
            self.assertEqual(cap["timeout"], "30m")
            self.assertEqual(cap["model"], "stub-model-x")
            self.assertEqual(cap["effort"], "high")
            self.assertEqual(
                cap["work_roots"],
                os.pathsep.join((str(work_root), str(docs_root))),
            )
            self.assertIsNone(cap["mcp_connected"])
            self.assertIsNone(cap["mcp_client"])
            self.assertIsNone(cap["mcp_client_version"])
            self.assertIsNone(cap["mcp_client_title"])
            self.assertIsNone(cap["mcp_tool_call"])
            # The wrapper actually ran with cwd = the cartopian project root.
            self.assertEqual(Path(cap["cwd"]).resolve(), project_root)

    def test_multiple_nonterminal_slices_use_one_launch_and_one_budget_unit(self) -> None:
        """A bounded wait slice stays inside one initiated automation run."""
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-multislice-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            template = tmp_path / "report-template.txt"
            template.write_text(_STUB_REPORT, encoding="utf-8")
            launch_log = tmp_path / "launches.log"

            scaffold.write(
                "cartopian.toml",
                _toml(str(stub), timeout="30s")
                + "\n[automation]\n"
                  'initiation = "auto"\n'
                  'confirmation = "until-blocked"\n'
                  "max_handoffs_per_run = 1\n",
            )
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-005.md",
                "# TASK-01-005: Multi-slice handoff\n\n"
                "Phase: PHASE-01\n"
                "Work root: n/a\n"
                "Assignee: coder\n\n"
                "## Goal\n\nExercise bounded wait slices.\n",
            )
            scaffold.capture_request(
                request_id="REQUEST-005",
                unit="task:TASK-01-005",
                text="Exercise the bounded wait slices.",
            )
            context = request_trace.context_for_task_assignment(
                scaffold.project_root, task_path
            )
            scaffold.write(
                "prompts/PROMPT-01-005.md",
                request_trace.upsert_request_sections(
                    "# PROMPT-01-005\n\n## Your task\n\nWrite the report.\n",
                    context.section,
                ),
            )
            fake_home = tmp_path / "home"
            fake_home.mkdir()

            launches = 0
            handoff_budget_used = 0
            operator_confirmations = 0
            nonterminal_observations = 0
            automatic_host_resumes = 0
            user_visible_context_messages = []
            nonterminal_message_counts = []
            terminal_record = None

            # One initiated run launches exactly once.  The stub deliberately
            # outlives two one-second observation slices before writing a
            # valid report.
            env = {
                "STUB_REPORT_TEMPLATE": str(template),
                "STUB_LAUNCH_LOG": str(launch_log),
                "STUB_SLEEP": "2.4",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                _, dispatch_stderr, dispatch_rc = _dispatch(
                    str(task_path), "coder", fake_home
                )
            self.assertEqual(dispatch_rc, EXIT_OK, msg=dispatch_stderr)
            launches += 1
            handoff_budget_used += 1

            while terminal_record is None:
                wait_args = argparse.Namespace(
                    task_path=str(task_path),
                    role="coder",
                    max_block="1s",
                    poll_interval=0.05,
                )
                wait_out, wait_err = io.StringIO(), io.StringIO()
                with mock.patch(
                    "cli.commands.wait_handoff.Path.home", return_value=fake_home
                ):
                    with contextlib.redirect_stdout(wait_out), \
                            contextlib.redirect_stderr(wait_err):
                        wait_rc = wait_handoff.handler(wait_args)
                wait_record = json.loads(wait_out.getvalue().strip())
                self.assertEqual(wait_rc, EXIT_OK, msg=wait_err.getvalue())
                if wait_record["status"] == "still-running":
                    nonterminal_observations += 1
                    automatic_host_resumes += 1
                    nonterminal_message_counts.append(
                        len(user_visible_context_messages)
                    )
                    # Internal observation boundary: re-wait directly and
                    # silently.  No context message, dispatch, budget
                    # increment, or operator confirmation.
                    continue
                terminal_record = wait_record

            report_path = Path(terminal_record["report_path"])
            action_args = argparse.Namespace(
                report_path=str(report_path), variant=None
            )
            action_out, action_err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(action_out), \
                    contextlib.redirect_stderr(action_err):
                action_rc = report_action.handler(action_args)
            action_record = json.loads(action_out.getvalue().strip())
            user_visible_context_messages.append(
                {
                    "status": terminal_record["status"],
                    "verdict": action_record["verdict"],
                }
            )

            self.assertGreaterEqual(nonterminal_observations, 2)
            self.assertEqual(automatic_host_resumes, nonterminal_observations)
            self.assertEqual(
                nonterminal_message_counts,
                [0] * nonterminal_observations,
                msg="routine nonterminal slices must not accumulate context",
            )
            self.assertEqual(
                user_visible_context_messages,
                [{"status": "done", "verdict": "accepted"}],
                msg="the first user-visible update must be the terminal result",
            )
            self.assertEqual(terminal_record["status"], "done")
            self.assertEqual(action_rc, EXIT_OK, msg=action_err.getvalue())
            self.assertEqual(action_record["verdict"], "accepted")
            self.assertFalse(action_record["path_mismatch"])
            self.assertEqual(launches, 1)
            self.assertEqual(handoff_budget_used, 1)
            self.assertEqual(operator_confirmations, 0)
            self.assertEqual(
                len(launch_log.read_text(encoding="utf-8").splitlines()), 1
            )

    def test_clears_stale_model_and_effort_when_handoff_sets_neither(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"

            # No model/effort in roles.coder — stale CARTOPIAN_MODEL /
            # CARTOPIAN_EFFORT inherited from the parent environment must NOT
            # leak into the wrapper.
            work_root = scaffold.project_root / "tool-repo"
            work_root.mkdir()
            scaffold.write("cartopian.toml", _toml(str(stub), work_roots='"tool-repo"'))
            scaffold.write(
                "cartopian.local.toml",
                f'[work_roots]\ntool-repo = "{work_root}"\n',
            )
            task_path = _write_task_and_prompt(scaffold)

            fake_home = tmp_path / "home"
            fake_home.mkdir()

            env = {
                "STUB_CAPTURE": str(capture),
                "STUB_NO_REPORT": "1",
                "CARTOPIAN_MODEL": "stale-model",
                "CARTOPIAN_EFFORT": "stale-effort",
                "CARTOPIAN_WORK_ROOTS": "/stale/work-root",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads(stdout.strip())
            self.assertIsNone(rec["launch"]["model"])
            self.assertIsNone(rec["launch"]["effort"])

            # dispatch is non-blocking; poll briefly for the detached stub's capture.
            cap = None
            deadline = time.monotonic() + 5.0
            while cap is None and time.monotonic() < deadline:
                try:
                    cap = json.loads(capture.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.05)
            self.assertIsNotNone(cap, "stub wrapper did not run")
            self.assertIsNone(cap["model"])
            self.assertIsNone(cap["effort"])
            # The stale inherited work-roots value is replaced by the resolved
            # grant, never passed through.
            self.assertEqual(cap["work_roots"], str(work_root))

    def test_clears_stale_work_roots_when_project_declares_none(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"

            # No [project].work_roots — a stale CARTOPIAN_WORK_ROOTS inherited
            # from the parent environment must NOT leak into the wrapper (it
            # would widen an agent CLI's sandbox to a path this project never
            # declared).
            scaffold.write("cartopian.toml", _toml(str(stub)))
            task_path = _write_task_and_prompt(scaffold)

            fake_home = tmp_path / "home"
            fake_home.mkdir()

            env = {
                "STUB_CAPTURE": str(capture),
                "STUB_NO_REPORT": "1",
                "CARTOPIAN_WORK_ROOTS": "/stale/work-root",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads(stdout.strip())
            self.assertEqual(rec["work_roots"], [])

            cap = None
            deadline = time.monotonic() + 5.0
            while cap is None and time.monotonic() < deadline:
                try:
                    cap = json.loads(capture.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.05)
            self.assertIsNotNone(cap, "stub wrapper did not run")
            self.assertIsNone(cap["work_roots"])


class TestDispatchDetachedStdio(unittest.TestCase):
    """Detached-stdio regression: the wrapper is detached (start_new_session)
    and outlives the short-lived dispatch invocation, so no child stream may
    be implicitly inherited from that caller.

    RED framing: with implicit Popen stdio the detached child inherited the
    dispatch caller's stdin/stdout/stderr. A captured CLI or MCP caller exits
    right after launch and its pipes die with it — the wrapper/agent's first
    write after that hits a closed pipe (EPIPE) and kills the handoff mid-run
    before any report exists (and an inherited stdout would interleave agent
    output into the MCP server's JSON-RPC stream). GREEN: dispatch pins an
    explicit stable stdio policy — stdin from the null device, stdout/stderr
    on POSIX to a dispatch-owned launch-log sidecar of the expected report
    (null-device fallback) — so the handoff reaches a terminal status
    regardless of the caller's lifetime, and wrapper/agent diagnostics
    survive in the log. Native Windows pins the same detachment with
    deliberate null-device output and no sidecar — see
    TestDispatchWindowsDevnullPolicy.
    """

    # Repo root, for running the real CLI as a short-lived captured caller.
    REPO_ROOT = Path(dispatch.__file__).resolve().parents[2]

    def _scaffolded_task(self, scaffold, stub: Path):
        scaffold.write("cartopian.toml", _toml(str(stub)))
        return _write_task_and_prompt(scaffold)

    @unittest.skipUnless(os.name == "posix", "the launch-log sidecar is POSIX-only policy")
    def test_launch_contract_pins_explicit_stable_stdio(self) -> None:
        # POSIX launch contract: the detached child's streams are set
        # explicitly at Popen time — stdin from the null device, stdout to
        # the dispatch-owned launch log (a sidecar of the expected report,
        # like the wrapper status file), stderr folded into stdout. Nothing
        # is left implicit/inherited. The native-Windows counterpart (same
        # explicit streams, null-device output, no sidecar) is pinned by
        # TestDispatchWindowsDevnullPolicy.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            task_path = self._scaffolded_task(scaffold, stub)
            fake_home = tmp_path / "home"
            fake_home.mkdir()

            captured: dict = {}

            class _FakeProc:
                pid = 4242

            def fake_popen(argv, **kwargs):
                captured["argv"] = argv
                captured.update(kwargs)
                return _FakeProc()

            with mock.patch(
                "cli.commands.dispatch.subprocess.Popen", side_effect=fake_popen
            ):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            self.assertIn(
                "stdin", captured,
                msg="detached child stdin left implicit — inherited from the caller",
            )
            self.assertIs(captured["stdin"], subprocess.DEVNULL)
            self.assertIn(
                "stdout", captured,
                msg="detached child stdout left implicit — inherited from the caller",
            )
            self.assertIs(captured["stderr"], subprocess.STDOUT)
            self.assertTrue(captured["start_new_session"])

            # Dispatch detaches the common supervisor on explicit null stdio.
            # The supervisor owns the wrapper pipe and atomically publishes
            # the independently bounded log named in its argv.
            self.assertIs(captured["stdout"], subprocess.DEVNULL)
            self.assertTrue(captured["argv"][1].endswith("cli/output_safety.py"))
            rec = json.loads(stdout.strip())
            self.assertIn("--log-path", captured["argv"])
            self.assertIn(rec["launch_log_path"], captured["argv"])
            self.assertEqual(
                rec["output_safety"]["guarantee_scope"],
                "retained-launch-log",
            )
            self.assertEqual(rec["output_safety"]["log_byte_limit"], 65536)
            self.assertEqual(rec["output_safety"]["log_line_limit"], 400)
            self.assertNotIn("stream_byte_limit", rec["output_safety"])
            self.assertNotIn("stream_line_limit", rec["output_safety"])
            self.assertNotIn("overflow_classification", rec["output_safety"])

    @unittest.skipUnless(os.name == "posix", "pipe-lifetime semantics are POSIX-specific here")
    def test_detached_child_survives_caller_and_captured_pipe_exit(self) -> None:
        # Behavioral regression, end to end: run the real `cartopian dispatch`
        # CLI as a short-lived caller with fully captured stdio (the shape of
        # both the observed CLI failure and the in-process MCP server, whose
        # fds are the harness's pipes). After the caller exits, close every
        # captured pipe — then let the detached stub write to stdout/stderr
        # and continue to its terminal status. With inherited stdio the probe
        # write raises BrokenPipeError and no report ever appears.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            template = tmp_path / "report-template.txt"
            template.write_text(_STUB_REPORT, encoding="utf-8")
            task_path = self._scaffolded_task(scaffold, stub)
            fake_home = tmp_path / "home"
            fake_home.mkdir()
            gate = tmp_path / "caller-and-pipes-gone"

            env = dict(os.environ)
            env.update(
                {
                    "HOME": str(fake_home),
                    "STUB_REPORT_TEMPLATE": str(template),
                    "STUB_GATE": str(gate),
                    "STUB_STDIO_PROBE": "1",
                }
            )
            env.pop("STUB_SLEEP", None)
            env.pop("STUB_NO_REPORT", None)

            caller = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "cli.main",
                    "dispatch",
                    str(task_path),
                    "--role",
                    "coder",
                ],
                cwd=str(self.REPO_ROOT),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # dispatch is non-blocking: the caller exits at launch. Its own
            # output (one NDJSON line) fits the pipe buffer, so wait() is safe.
            rc = caller.wait(timeout=30)
            if rc != 0:
                # The launch itself failed — pipes hold only the caller's
                # output now, so draining them cannot block.
                self.fail(
                    f"dispatch caller exited {rc}: "
                    f"stderr={caller.stderr.read().decode(errors='replace')!r}"
                )
            record_line = caller.stdout.readline().decode()
            rec = json.loads(record_line)
            self.assertEqual(rec["status"], "dispatched")

            # The caller is gone; now kill its captured pipes. Do NOT read to
            # EOF first — with inherited stdio the detached stub holds the
            # write ends and EOF would never come.
            caller.stdin.close()
            caller.stdout.close()
            caller.stderr.close()

            # Signal the detached stub to probe its stdio and finish.
            gate.write_text("go", encoding="utf-8")

            report = scaffold.reports / "REPORT-01-004.md"
            deadline = time.monotonic() + 20.0
            observation = handoff_observer.observe_once(
                report,
                expected_variant="task",
            )
            while not observation.terminal and time.monotonic() < deadline:
                time.sleep(0.05)
                observation = handoff_observer.observe_once(
                    report,
                    expected_variant="task",
                )
            self.assertTrue(
                report.is_file(),
                "detached handoff died after the caller and its captured "
                "pipes exited — it never reached a terminal status (no report)",
            )
            self.assertTrue(
                observation.terminal,
                "complete report became visible but the retained-log "
                "publication boundary never became observable",
            )

            # Diagnostics survived the caller: the probe output landed in the
            # dispatch-owned launch log before the canonical reader exposed
            # report completion.
            log_path = Path(rec["launch_log_path"])
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn("stub-stdout-probe", log_text)
            self.assertIn("stub-stderr-probe", log_text)

    @unittest.skipUnless(os.name == "posix", "the launch-log sidecar is POSIX-only policy")
    def test_unopenable_launch_log_falls_back_to_devnull(self) -> None:
        # Diagnostics are best-effort; detachment is not. If the launch log
        # cannot be opened, the launch proceeds with the null device and the
        # record says so (launch_log_path null) instead of failing the
        # handoff or falling back to inherited streams.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            task_path = self._scaffolded_task(scaffold, stub)
            fake_home = tmp_path / "home"
            fake_home.mkdir()
            # A directory squatting on the launch-log path makes open() fail.
            (scaffold.reports / "REPORT-01-004.md.launch.log").mkdir()

            env = {"STUB_CAPTURE": str(capture), "STUB_NO_REPORT": "1"}
            with mock.patch.dict(os.environ, env, clear=False):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads(stdout.strip())
            self.assertIsNone(rec["launch_log_path"])

            cap = None
            deadline = time.monotonic() + 5.0
            while cap is None and time.monotonic() < deadline:
                try:
                    cap = json.loads(capture.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.05)
            self.assertIsNotNone(cap, "stub wrapper did not run under the fallback")


@unittest.skipUnless(os.name == "posix", "the launch-log sidecar is POSIX-only policy")
class TestDispatchLaunchLogSink(unittest.TestCase):
    """Launch-log sidecar hardening (POSIX-only policy — native Windows
    dispatch deliberately writes no sidecar; see
    TestDispatchWindowsDevnullPolicy): each dispatch gets a fresh, safe,
    owner-only diagnostic sink, and a hostile or invalid pre-existing sink
    degrades to the null device without blocking or failing the handoff.

    RED framing: the first sidecar implementation opened
    ``<expected-report>.launch.log`` with a bare ``open(path, "ab")`` —

    - repeated dispatches appended separate runs into one file, so handoff
      boundaries were lost and a prior run's diagnostics read as this run's;
    - a planted leaf symlink was followed and its target received the child's
      diagnostics;
    - a FIFO at the predictable sidecar path blocked the ``open`` before
      ``Popen``, wedging dispatch (and the PM behind it) indefinitely;
    - a normal ``022`` umask created the log world-readable (``0644``);
    - a failed ``Popen`` left the just-created empty log orphaned.

    GREEN: dispatch opens the sink refusing symlinks and non-regular files
    (atomically via O_NOFOLLOW/O_NONBLOCK + fstat), truncates it fresh per
    dispatch, forces owner-only permissions whether the file is new or
    pre-existing, discards it again when the launch fails, and falls back to
    the null device for every unsafe or unopenable collision.
    """

    LOG_NAME = "REPORT-01-004.md.launch.log"

    def _scaffolded(self, scaffold, tmp_path: Path):
        stub = _make_stub(tmp_path)
        scaffold.write("cartopian.toml", _toml(str(stub)))
        task_path = _write_task_and_prompt(scaffold)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        return task_path, fake_home

    @staticmethod
    def _fake_popen(captured: dict):
        class _FakeProc:
            pid = 4242

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured.update(kwargs)
            return _FakeProc()

        return fake_popen

    def test_each_dispatch_starts_with_a_fresh_launch_log(self) -> None:
        # Slot reuse: a prior run's diagnostics must not leak into this
        # dispatch's sink — the log is truncated at launch, so its content
        # always belongs to exactly one handoff.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            task_path, fake_home = self._scaffolded(scaffold, tmp_path)
            log_path = scaffold.reports / self.LOG_NAME
            scaffold.reports.mkdir(parents=True, exist_ok=True)
            log_path.write_bytes(b"prior-run diagnostics\n")

            captured: dict = {}
            with mock.patch(
                "cli.commands.dispatch.subprocess.Popen",
                side_effect=self._fake_popen(captured),
            ):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads(stdout.strip())
            # dispatch records the resolved (symlink-collapsed) form, e.g.
            # /var -> /private/var on macOS; compare canonically.
            self.assertEqual(
                Path(rec["launch_log_path"]).resolve(), log_path.resolve()
            )
            self.assertFalse(
                log_path.exists(),
                msg="prior-run diagnostics survived before the supervisor "
                    "published this launch's bounded representation",
            )

    @unittest.skipUnless(os.name == "posix", "umask/mode semantics are POSIX-specific")
    def test_launch_log_created_owner_only_despite_umask(self) -> None:
        # A fresh log under a normal 022 umask must still be 0600: the sink
        # can carry agent/wrapper diagnostics (paths, env echoes, tracebacks)
        # that are no one else's business.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            task_path, fake_home = self._scaffolded(scaffold, tmp_path)
            log_path = scaffold.reports / self.LOG_NAME

            captured: dict = {}
            old_umask = os.umask(0o022)
            try:
                with mock.patch(
                    "cli.commands.dispatch.subprocess.Popen",
                    side_effect=self._fake_popen(captured),
                ):
                    stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)
            finally:
                os.umask(old_umask)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            self.assertFalse(
                log_path.exists(),
                msg="dispatch must not expose a log before bounded supervisor publication",
            )

    @unittest.skipUnless(os.name == "posix", "chmod semantics are POSIX-specific")
    def test_preexisting_launch_log_normalized_to_owner_only(self) -> None:
        # A pre-existing world-readable log from an earlier (unhardened) run
        # is normalized, not inherited: reuse truncates it AND forces 0600.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            task_path, fake_home = self._scaffolded(scaffold, tmp_path)
            log_path = scaffold.reports / self.LOG_NAME
            scaffold.reports.mkdir(parents=True, exist_ok=True)
            log_path.write_bytes(b"old\n")
            os.chmod(log_path, 0o644)

            captured: dict = {}
            with mock.patch(
                "cli.commands.dispatch.subprocess.Popen",
                side_effect=self._fake_popen(captured),
            ):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            self.assertFalse(
                log_path.exists(),
                msg="pre-existing log must be removed before bounded publication",
            )

    def test_symlink_launch_log_not_followed(self) -> None:
        # A leaf symlink planted at the predictable sidecar path must never
        # be followed: its target receives nothing, and the dispatch falls
        # back to the null device rather than failing or inheriting.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            task_path, fake_home = self._scaffolded(scaffold, tmp_path)
            target = tmp_path / "victim-file"
            target.write_bytes(b"victim content\n")
            scaffold.reports.mkdir(parents=True, exist_ok=True)
            log_path = scaffold.reports / self.LOG_NAME
            log_path.symlink_to(target)

            captured: dict = {}
            with mock.patch(
                "cli.commands.dispatch.subprocess.Popen",
                side_effect=self._fake_popen(captured),
            ):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads(stdout.strip())
            self.assertIsNone(
                rec["launch_log_path"],
                msg="symlinked sink was accepted — diagnostics would follow the link",
            )
            self.assertIs(captured["stdout"], subprocess.DEVNULL)
            self.assertEqual(
                target.read_bytes(), b"victim content\n",
                msg="symlink target was opened/truncated through the sidecar path",
            )
            self.assertTrue(log_path.is_symlink(), "planted symlink must be left in place")

    @unittest.skipUnless(os.name == "posix", "FIFOs are POSIX-specific")
    def test_fifo_launch_log_does_not_block_dispatch(self) -> None:
        # A FIFO squatting on the sidecar path must not wedge dispatch: a
        # blocking open(..., "ab") before Popen would hang the launch (and
        # the PM behind it) until a reader appeared. The sink open must be
        # non-blocking and reject the non-regular file.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            task_path, fake_home = self._scaffolded(scaffold, tmp_path)
            scaffold.reports.mkdir(parents=True, exist_ok=True)
            log_path = scaffold.reports / self.LOG_NAME
            os.mkfifo(log_path)

            # Rescue reader: if regressed code blocks opening the FIFO for
            # write, this unblocks it after 5s so the suite fails (on the
            # elapsed-time assertion) instead of hanging forever.
            def _rescue() -> None:
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    time.sleep(0.1)
                try:
                    fd = os.open(log_path, os.O_RDONLY | os.O_NONBLOCK)
                    os.close(fd)
                except OSError:
                    pass

            rescuer = threading.Thread(target=_rescue, daemon=True)
            rescuer.start()

            captured: dict = {}
            start = time.monotonic()
            with mock.patch(
                "cli.commands.dispatch.subprocess.Popen",
                side_effect=self._fake_popen(captured),
            ):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)
            elapsed = time.monotonic() - start

            self.assertLess(
                elapsed, 2.0,
                msg=f"dispatch blocked {elapsed:.1f}s opening a FIFO sink before Popen",
            )
            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads(stdout.strip())
            self.assertIsNone(rec["launch_log_path"])
            self.assertIs(captured["stdout"], subprocess.DEVNULL)
            self.assertTrue(
                stat.S_ISFIFO(os.lstat(log_path).st_mode),
                "FIFO must be left in place, never opened as the sink",
            )

    def test_failed_launch_does_not_orphan_launch_log(self) -> None:
        # Popen failure means no handoff exists: the just-created (empty)
        # sink must be discarded, not left as an orphan sidecar next to a
        # report that will never arrive.
        for exc in (FileNotFoundError(2, "gone", "stub"), OSError("boom")):
            with self.subTest(exc=type(exc).__name__), \
                    project_scaffold(cartopian_toml="") as scaffold, \
                    tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
                tmp_path = Path(tmp)
                task_path, fake_home = self._scaffolded(scaffold, tmp_path)
                log_path = scaffold.reports / self.LOG_NAME

                with mock.patch(
                    "cli.commands.dispatch.subprocess.Popen", side_effect=exc
                ):
                    stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

                self.assertEqual(rc, EXIT_FAIL)
                self.assertIn("failed to launch handoff agent", stderr)
                self.assertFalse(
                    log_path.exists(),
                    msg="failed launch left an empty .launch.log orphan behind",
                )


class TestDispatchWindowsDevnullPolicy(unittest.TestCase):
    """Native-Windows wrapper routing under the common bounded supervisor.

    Dispatch itself always detaches the supervisor on null stdio.  The
    supervisor captures the wrapper through its own pipe and publishes a
    closed, bounded file atomically, so the same safe retention contract now
    applies without renaming an open Windows descriptor.
    """

    LOG_NAME = "REPORT-01-004.md.launch.log"

    def _scaffolded(self, scaffold, tmp_path: Path):
        stub = _make_stub(tmp_path)
        scaffold.write("cartopian.toml", _toml(str(stub)))
        task_path = _write_task_and_prompt(scaffold)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        return task_path, fake_home

    @staticmethod
    def _fake_popen(captured: dict):
        class _FakeProc:
            pid = 4242

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured.update(kwargs)
            return _FakeProc()

        return fake_popen

    def test_nt_launch_uses_common_supervisor_and_records_bounded_log(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            task_path, fake_home = self._scaffolded(scaffold, tmp_path)
            scaffold.reports.mkdir(parents=True, exist_ok=True)
            planted = scaffold.reports / self.LOG_NAME
            planted.write_bytes(b"planted sidecar content\n")

            captured: dict = {}
            with mock.patch(
                        "cli.commands.dispatch._running_on_windows",
                        return_value=True,
                    ), \
                    mock.patch(
                        "cli.commands.dispatch.subprocess.Popen",
                        side_effect=self._fake_popen(captured),
                    ):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            self.assertIs(captured["stdin"], subprocess.DEVNULL)
            self.assertIs(captured["stdout"], subprocess.DEVNULL)
            self.assertIs(captured["stderr"], subprocess.STDOUT)
            self.assertTrue(captured["start_new_session"])

            rec = json.loads(stdout.strip())
            self.assertEqual(rec["launch_log_path"], str(planted.resolve()))
            self.assertFalse(
                planted.exists(),
                msg="prior retained bytes must be cleared before this launch",
            )
            self.assertIn("--log-path", captured["argv"])
            self.assertEqual(
                sorted(os.listdir(scaffold.reports)),
                ["REPORT-01-004.md.status"],
                msg="only the running status exists until supervisor publication",
            )
            status_text = (
                scaffold.reports / "REPORT-01-004.md.status"
            ).read_text(encoding="utf-8")
            self.assertIn("state=running", status_text)
            self.assertIn("expected_variant=task", status_text)

    @unittest.skipUnless(os.name == "posix", "planting a symlink needs POSIX")
    def test_nt_launch_leaves_planted_symlink_and_target_untouched(self) -> None:
        # No sink is ever derived from the sidecar path, so a planted symlink
        # cannot redirect anything: link and target both survive unmodified.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            task_path, fake_home = self._scaffolded(scaffold, tmp_path)
            target = tmp_path / "victim-file"
            target.write_bytes(b"victim content\n")
            scaffold.reports.mkdir(parents=True, exist_ok=True)
            link = scaffold.reports / self.LOG_NAME
            link.symlink_to(target)

            captured: dict = {}
            with mock.patch(
                        "cli.commands.dispatch._running_on_windows",
                        return_value=True,
                    ), \
                    mock.patch(
                        "cli.commands.dispatch.subprocess.Popen",
                        side_effect=self._fake_popen(captured),
                    ):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            self.assertIs(captured["stdout"], subprocess.DEVNULL)
            self.assertIsNone(json.loads(stdout.strip())["launch_log_path"])
            self.assertTrue(link.is_symlink(), "planted symlink must be left in place")
            self.assertEqual(target.read_bytes(), b"victim content\n")

    @unittest.skipUnless(os.name == "posix", "FIFO stand-in for any node at the path")
    def test_nt_launch_never_opens_sidecar_path(self) -> None:
        # A FIFO squatting on the sidecar path proves the nt branch performs
        # no open at all: a blocking writer-open would hang here, and even an
        # lstat-then-refuse policy would be more than this branch does.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            task_path, fake_home = self._scaffolded(scaffold, tmp_path)
            scaffold.reports.mkdir(parents=True, exist_ok=True)
            fifo = scaffold.reports / self.LOG_NAME
            os.mkfifo(fifo)

            captured: dict = {}
            start = time.monotonic()
            with mock.patch(
                        "cli.commands.dispatch._running_on_windows",
                        return_value=True,
                    ), \
                    mock.patch(
                        "cli.commands.dispatch.subprocess.Popen",
                        side_effect=self._fake_popen(captured),
                    ):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)
            elapsed = time.monotonic() - start

            self.assertLess(elapsed, 2.0, "nt branch opened (and blocked on) the sidecar path")
            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            self.assertIs(captured["stdout"], subprocess.DEVNULL)
            self.assertIsNone(json.loads(stdout.strip())["launch_log_path"])
            self.assertTrue(
                stat.S_ISFIFO(os.lstat(fifo).st_mode),
                "node at the sidecar path must be left exactly as planted",
            )

    def test_nt_failed_launch_leaves_no_stale_sidecar(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            task_path, fake_home = self._scaffolded(scaffold, tmp_path)
            scaffold.reports.mkdir(parents=True, exist_ok=True)
            planted = scaffold.reports / self.LOG_NAME
            planted.write_bytes(b"planted sidecar content\n")

            with mock.patch(
                        "cli.commands.dispatch._running_on_windows",
                        return_value=True,
                    ), \
                    mock.patch(
                        "cli.commands.dispatch.subprocess.Popen",
                        side_effect=OSError("boom"),
                    ):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_FAIL)
            self.assertIn("failed to launch handoff agent", stderr)
            self.assertFalse(
                planted.exists(),
                msg="failed launch must not leave a prior run's sidecar in the slot",
            )


class TestDispatchFailClosed(unittest.TestCase):
    """Fail-closed gates: every refusal returns non-zero and launches nothing."""

    def _fake_home(self, tmp_path: Path) -> Path:
        home = tmp_path / "home"
        home.mkdir()
        return home

    def test_missing_role_block_fails_closed(self) -> None:
        _TOML_NO_BLOCK = (
            "[project]\n"
            'id = "p"\n'
            'name = "P"\n'
            'project_schema_version = "v0.10.0"\n'
            "\n"
            "[roles.coder]\n"
            'description = "Implements tasks per spec."\n'
        )
        with project_scaffold(cartopian_toml=_TOML_NO_BLOCK) as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            task_path = _write_task_and_prompt(scaffold)

            stdout, stderr, rc = _dispatch(
                str(task_path), "coder", self._fake_home(tmp_path)
            )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[guard]", stderr)
            self.assertIn("roles.coder.agent", stderr)

    def test_task_dispatch_requires_auto_launch_permission(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write(
                "cartopian.toml",
                _toml(str(stub), auto_launch_tasks=False),
            )
            task_path = _write_task_and_prompt(scaffold)

            with mock.patch.dict(
                os.environ, {"STUB_CAPTURE": str(capture)}, clear=False
            ):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("roles.coder.auto_launch", stderr)
            self.assertFalse(capture.exists())

    def test_task_run_refuses_prompt_without_generated_request_binding(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub)))
            task_path = _write_task_and_prompt(scaffold)
            (scaffold.prompts / "PROMPT-01-004.md").write_text(
                "# Unbound prompt\n\nDo extra work.\n", encoding="utf-8"
            )

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("stale-request-context", stderr)
            self.assertFalse(capture.exists())

    def test_empty_model_fails_closed(self) -> None:
        # A set-but-empty model would diverge the record from the export
        # (record reports "", nothing exported) — refuse to launch instead.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            toml = _toml(str(stub)).replace(
                f'agent = "{stub}"\n',
                f'agent = "{stub}"\nmodel = ""\n',
            )
            scaffold.write("cartopian.toml", toml)
            task_path = _write_task_and_prompt(scaffold)

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[error]", stderr)
            self.assertIn("roles.coder.model", stderr)
            self.assertFalse(capture.exists(), "wrapper was launched despite fail-closed")

    def test_empty_effort_fails_closed(self) -> None:
        # Same guard as model: a set-but-empty effort would diverge the record
        # from the export (record reports "", nothing exported).
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            toml = _toml(str(stub)).replace(
                f'agent = "{stub}"\n',
                f'agent = "{stub}"\neffort = ""\n',
            )
            scaffold.write("cartopian.toml", toml)
            task_path = _write_task_and_prompt(scaffold)

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[error]", stderr)
            self.assertIn("roles.coder.effort", stderr)
            self.assertFalse(capture.exists(), "wrapper was launched despite fail-closed")

    def test_unmapped_work_root_fails_closed(self) -> None:
        # A declared work root with no cartopian.local.toml mapping cannot be
        # exported to the wrapper — the launched agent's work-root writes
        # would fail mid-run, so dispatch refuses to launch.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub), work_roots='"tool-repo"'))
            # No cartopian.local.toml — the declared root is unmapped.
            task_path = _write_task_and_prompt(scaffold)

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[error]", stderr)
            self.assertIn("work_roots.tool-repo", stderr)
            self.assertFalse(capture.exists(), "wrapper was launched despite fail-closed")

    def test_missing_work_root_dir_fails_closed(self) -> None:
        # A mapped work root whose path does not exist on this machine is a
        # stale local mapping — the grant would be meaningless, so dispatch
        # refuses to launch.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            missing_root = tmp_path / "no-such-repo"
            scaffold.write("cartopian.toml", _toml(str(stub), work_roots='"tool-repo"'))
            scaffold.write(
                "cartopian.local.toml",
                f'[work_roots]\ntool-repo = "{missing_root}"\n',
            )
            task_path = _write_task_and_prompt(scaffold)

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[guard]", stderr)
            self.assertIn(str(missing_root), stderr)
            self.assertFalse(capture.exists(), "wrapper was launched despite fail-closed")

    def test_missing_prompt_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub)))
            # Task present, but no matching prompt file written.
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-004.md",
                "# TASK-01-004: Mediated\n\nWork root: n/a\n",
            )

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[guard]", stderr)
            self.assertIn("prompt not found", stderr)
            self.assertFalse(capture.exists())


class TestDispatchHostWaitBudgetGate(unittest.TestCase):
    """The host must be able to wait out the handoff, or nothing is launched.

    Dispatch is only half a handoff: the PM has to still be waiting when the
    report lands. Every MCP host caps a single ``tools/call``, and on some
    hosts that cap is below the protocol's default 60m role timeout — so a
    launch that clears every other gate still produces an orphaned assignee and
    a killed wait. An unlaunched handoff is recoverable; an orphaned one is
    not, so the mismatch is refused here rather than discovered mid-wait.
    """

    def _fake_home(self, tmp_path: Path) -> Path:
        home = tmp_path / "home"
        home.mkdir()
        return home

    def _codex_home(self, tmp_path: Path, body: str = "") -> Path:
        home = tmp_path / "codex"
        home.mkdir()
        (home / "config.toml").write_text(body, encoding="utf-8")
        return home

    def test_role_timeout_over_host_ceiling_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            # 60m role timeout against Codex's 300s default tools/call ceiling.
            scaffold.write("cartopian.toml", _toml(str(stub), timeout="60m"))
            task_path = _write_task_and_prompt(scaffold)

            env = {
                "STUB_CAPTURE": str(capture),
                "CARTOPIAN_MCP_CONNECTED": "1",
                "CARTOPIAN_MCP_CLIENT": "codex-mcp-client",
                "CODEX_HOME": str(self._codex_home(tmp_path)),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[guard]", stderr)
            self.assertIn("tool_timeout_sec", stderr)
            # Nothing was launched: the stub never ran.
            self.assertFalse(capture.exists())

    def test_raised_host_ceiling_permits_the_launch(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub), timeout="60m"))
            task_path = _write_task_and_prompt(scaffold)
            codex_home = self._codex_home(
                tmp_path,
                '[mcp_servers.cartopian]\n'
                'command = "/x/cartopian-mcp"\n'
                "tool_timeout_sec = 3900\n",
            )

            env = {
                "STUB_CAPTURE": str(capture),
                "CARTOPIAN_MCP_CONNECTED": "1",
                "CARTOPIAN_MCP_CLIENT": "codex-mcp-client",
                "CODEX_HOME": str(codex_home),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads([ln for ln in stdout.split("\n") if ln][0])
            self.assertEqual(rec["status"], "dispatched")
            # The launch record carries the budget it was cleared against.
            budget = rec["host_wait_budget"]
            self.assertEqual(budget["host"], "codex")
            self.assertEqual(budget["effective_wait_budget_seconds"], 3900)

    def test_unrecognized_host_fails_closed(self) -> None:
        """An unreadable ceiling is not an unbounded one."""
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub), timeout="1m"))
            task_path = _write_task_and_prompt(scaffold)

            env = {
                "STUB_CAPTURE": str(capture),
                "CARTOPIAN_MCP_CONNECTED": "1",
                "CARTOPIAN_MCP_CLIENT": "some-brand-new-ide",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[guard]", stderr)
            self.assertIn("manually", stderr)
            self.assertFalse(capture.exists())

    def test_hermes_host_markers_do_not_leak_into_the_assignee(self) -> None:
        """The trusted CARTOPIAN_MCP_HOST marker overrides clientInfo, and
        CARTOPIAN_HERMES_HOME redirects Hermes config reads — inherited by a
        detached assignee, the pair would misclassify nested Cartopian
        processes and restart context as the launching host. Dispatch must
        strip both, like every other connected-host identity variable."""
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub), timeout="30m"))
            task_path = _write_task_and_prompt(scaffold)

            # A stub `hermes` first on PATH answers the host-budget read with
            # a ceiling above the role timeout, so the gate clears.
            marker_home = tmp_path / "hermes-home"
            marker_home.mkdir()
            hermes_bin = tmp_path / "hermesbin"
            hermes_bin.mkdir()
            entry = json.dumps(
                {"command": "/x/cartopian-mcp", "enabled": True, "timeout": 3900}
            )
            hermes = hermes_bin / "hermes"
            hermes.write_text(f"#!/bin/sh\necho '{entry}'\n", encoding="utf-8")
            hermes.chmod(
                hermes.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )

            env = {
                "STUB_CAPTURE": str(capture),
                "STUB_NO_REPORT": "1",
                "PATH": os.pathsep.join(
                    [str(hermes_bin), os.environ.get("PATH", "")]
                ),
                host_capability.CONNECTED_ENV: "1",
                host_capability.CLIENT_ENV: "mcp",
                host_capability.HOST_MARKER_ENV: "hermes",
                host_capability.HERMES_HOME_ENV: str(marker_home),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads([ln for ln in stdout.split("\n") if ln][0])
            self.assertEqual(rec["host_wait_budget"]["host"], "hermes")

            cap = None
            deadline = time.monotonic() + 5.0
            while cap is None and time.monotonic() < deadline:
                try:
                    cap = json.loads(capture.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.05)
            self.assertIsNotNone(cap, "stub wrapper did not run")
            self.assertIsNone(cap["mcp_connected"])
            self.assertIsNone(cap["mcp_client"])
            self.assertIsNone(cap["mcp_host"])
            self.assertIsNone(cap["hermes_home"])

    def test_no_mcp_host_means_no_gate(self) -> None:
        """A plain shell invocation has no tools/call ceiling to violate."""
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub), timeout="60m"))
            task_path = _write_task_and_prompt(scaffold)

            env = {"STUB_CAPTURE": str(capture)}
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("CARTOPIAN_MCP_CONNECTED", None)
                os.environ.pop("CARTOPIAN_MCP_CLIENT", None)
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads([ln for ln in stdout.split("\n") if ln][0])
            self.assertIsNone(rec["host_wait_budget"])


class TestDispatchPromptKeyed(unittest.TestCase):
    """Prompt-keyed (report-path-only) dispatch for planning-checkpoint reviews.

    RED framing: before this mode existed, a planning checkpoint with a
    configured automatic-launch reviewer still fell back to an
    operator-performed launch — `dispatch` was keyed exclusively on a task
    path, and planning reviews have no task file. GREEN: `--prompt` launches
    the config-bound wrapper for an allowlisted `PROMPT-PLAN-*` slot, gated
    fail-closed on `roles.<role>.auto_launch` planning permission (default off — a
    role's task automation never silently extends to planning reviews).
    """

    PLAN_PROMPT = "PROMPT-PLAN-001.md"

    def _fake_home(self, tmp_path: Path) -> Path:
        home = tmp_path / "home"
        home.mkdir()
        return home

    def test_launches_wrapper_for_planning_prompt(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub), auto_launch_reviews=True))
            scaffold.capture_request(
                request_id="REQUEST-001",
                unit="project",
                text="Review the project plan.",
            )
            # A planning review prompt carries the generated, bound
            # `## Operator intent` section; dispatch recomputes it. Build it
            # through the real generator so the fixture cannot drift from the
            # contract dispatch enforces.
            context = request_trace.context_for_checkpoint(
                scaffold.project_root, "PLAN-001"
            )
            prompt_path = scaffold.write(
                f"prompts/{self.PLAN_PROMPT}",
                request_trace.upsert_request_sections(
                    "# PROMPT-PLAN-001\n\n## Your task\n\nReview the requirements.\n",
                    context.section,
                ),
            )
            project_root = scaffold.project_root.resolve()
            resolved_prompt = Path(prompt_path).resolve()

            env = {"STUB_CAPTURE": str(capture), "STUB_NO_REPORT": "1"}
            with mock.patch.dict(os.environ, env, clear=False):
                stdout, stderr, rc = _dispatch(
                    None, "coder", self._fake_home(tmp_path), prompt=str(prompt_path)
                )

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads(stdout.strip())
            self.assertEqual(rec["status"], "dispatched")
            self.assertIsNone(rec["task_id"])
            self.assertEqual(
                rec["prompt_id"], "PROMPT-PLAN-001"
            )
            self.assertEqual(rec["prompt_path"], str(resolved_prompt))
            self.assertTrue(
                rec["expected_report_path"].endswith(
                    "/reports/REPORT-PLAN-001.md"
                ),
                msg=rec["expected_report_path"],
            )
            self.assertEqual(Path(rec["cwd"]).resolve(), project_root)

            # dispatch is non-blocking; poll briefly for the detached stub's capture.
            cap = None
            deadline = time.monotonic() + 5.0
            while cap is None and time.monotonic() < deadline:
                try:
                    cap = json.loads(capture.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.05)
            self.assertIsNotNone(cap, "stub wrapper did not run")
            self.assertEqual(cap["argv"], [str(stub), str(resolved_prompt)])
            self.assertEqual(Path(cap["cwd"]).resolve(), project_root)

    def test_planning_auto_launch_unset_or_false_fails_closed(self) -> None:
        # Default-off gate: neither an absent key nor an explicit false may
        # launch — planning-review automation is a per-role opt-in.
        for auto_launch_reviews in (None, False):
            with self.subTest(auto_launch_reviews=auto_launch_reviews), \
                    project_scaffold(cartopian_toml="") as scaffold, \
                    tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
                tmp_path = Path(tmp)
                stub = _make_stub(tmp_path)
                capture = tmp_path / "capture.json"
                scaffold.write(
                    "cartopian.toml",
                    _toml(str(stub), auto_launch_reviews=auto_launch_reviews),
                )
                prompt_path = scaffold.write(f"prompts/{self.PLAN_PROMPT}", "# P\n")

                with mock.patch.dict(
                    os.environ, {"STUB_CAPTURE": str(capture)}, clear=False
                ):
                    stdout, stderr, rc = _dispatch(
                        None, "coder", self._fake_home(tmp_path), prompt=str(prompt_path)
                    )

                self.assertEqual(rc, EXIT_FAIL)
                self.assertEqual(stdout, "")
                self.assertIn("[guard]", stderr)
                self.assertIn("planning_review", stderr)
                self.assertFalse(
                    capture.exists(), "wrapper launched despite fail-closed gate"
                )

    def test_task_prompt_id_rejected(self) -> None:
        # PROMPT-NN-NNN dispatches by task path (which enforces task/prompt/
        # report agreement); --prompt refuses it even with the gate enabled,
        # so no second, weaker route to a task launch exists.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub), auto_launch_reviews=True))
            prompt_path = scaffold.write("prompts/PROMPT-01-004.md", "# P\n")

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    None, "coder", self._fake_home(tmp_path), prompt=str(prompt_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[guard]", stderr)
            self.assertIn("planning-checkpoint prompt slot", stderr)
            self.assertFalse(capture.exists())

    def test_prompt_outside_prompts_dir_rejected(self) -> None:
        # A well-named file outside <project-root>/prompts/ is not an
        # allowlisted slot.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub), auto_launch_reviews=True))
            stray = scaffold.write("PROMPT-PLAN-001.md", "# P\n")

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    None, "coder", self._fake_home(tmp_path), prompt=str(stray)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[guard]", stderr)
            self.assertIn("planning-checkpoint prompt slot", stderr)
            self.assertFalse(capture.exists())

    def test_missing_prompt_file_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            scaffold.write("cartopian.toml", _toml(str(stub), auto_launch_reviews=True))
            missing = scaffold.prompts / self.PLAN_PROMPT

            stdout, stderr, rc = _dispatch(
                None, "coder", self._fake_home(tmp_path), prompt=str(missing)
            )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[guard]", stderr)
            self.assertIn("prompt not found", stderr)

    def test_both_or_neither_keys_are_usage_errors(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            scaffold.write("cartopian.toml", _toml(str(stub), auto_launch_reviews=True))
            task_path = _write_task_and_prompt(scaffold)
            prompt_path = scaffold.write(f"prompts/{self.PLAN_PROMPT}", "# P\n")
            fake_home = self._fake_home(tmp_path)

            for task_arg, prompt_arg in (
                (str(task_path), str(prompt_path)),  # both
                (None, None),  # neither
            ):
                with self.subTest(task=task_arg, prompt=prompt_arg):
                    stdout, stderr, rc = _dispatch(
                        task_arg, "coder", fake_home, prompt=prompt_arg
                    )
                    self.assertEqual(rc, EXIT_USAGE)
                    self.assertEqual(stdout, "")
                    self.assertIn("exactly one of", stderr)


class TestDispatchNoRawExec(unittest.TestCase):
    """Containment negative test: the mediated, config-bound dispatch is the
    ONLY process-launch route on the PM tool surface.

    RED framing: before containment a PM with a shell could run the wrapper
    directly (`subprocess.Popen([wrapper, prompt])` — a raw exec path). Under
    containment that capability is removed, so the only reachable launch is
    `dispatch`, whose executable is sourced from operator config and which
    exposes no argument for injecting an arbitrary command.
    """

    def test_dispatch_exposes_no_arbitrary_executable_argument(self) -> None:
        # The dispatch subparser must accept only `task_path` / `--prompt`
        # (an allowlisted prompt slot, never an executable) + `--role`.
        # No flag may let the caller name an executable/command to run.
        parser = build_parser()
        sub = parser._subparsers._group_actions[0].choices["dispatch"]  # type: ignore[attr-defined]
        option_strings = {opt for a in sub._actions for opt in a.option_strings}
        for forbidden in ("--agent", "--command", "--cmd", "--exec", "--executable", "--shell"):
            self.assertNotIn(
                forbidden, option_strings,
                msg=f"dispatch must not expose {forbidden}: it would be a raw-exec injection vector",
            )
        # An injected executable flag is rejected outright (argparse usage error).
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["dispatch", "/abs/TASK-01-004.md", "--role", "coder", "--agent", "/bin/sh"])
        self.assertEqual(ctx.exception.code, EXIT_USAGE)

    def test_dispatch_is_the_only_wrapper_launcher_on_the_pm_surface(self) -> None:
        # Scan every PM-facing CLI command module: only `dispatch` may launch a
        # process whose program is caller/config-derived. Any other such
        # launcher would be a second exec path a contained PM could reach.
        #
        # `plan_audit` is allowlisted: its only subprocess call is a fixed,
        # read-only `["git", "status", ...]` provenance probe — a constant
        # binary with constant arguments, not a delegation/exec surface the PM
        # can point at a wrapper or arbitrary command.
        ALLOWLIST = {
            "dispatch.py": "the mediated launch route under test",
            "plan_audit.py": "fixed read-only `git status` provenance probe",
        }
        commands_dir = Path(dispatch.__file__).parent
        launch_tokens = (
            "subprocess.Popen",
            "subprocess.run",
            "subprocess.call",
            "subprocess.check_",
            "os.system",
            "os.exec",
            "os.posix_spawn",
            "os.spawn",
        )
        offenders = []
        for path in sorted(commands_dir.glob("*.py")):
            if path.name in ALLOWLIST:
                continue
            src = path.read_text(encoding="utf-8")
            if any(tok in src for tok in launch_tokens):
                offenders.append(path.name)
        self.assertEqual(
            offenders, [],
            msg=f"non-dispatch PM commands contain a raw process-launch path: {offenders}",
        )

        # Guard the allowlist itself: plan_audit must remain a fixed `git
        # status` probe — if it grows a caller/config-derived exec, this test
        # must fail so the containment claim is re-examined.
        plan_audit_src = (commands_dir / "plan_audit.py").read_text(encoding="utf-8")
        self.assertIn('["git", "status", "--porcelain", "--untracked-files=all"]', plan_audit_src)

        # And dispatch itself sources its executable from config, never argv:
        # the launched program is the resolved roles.<role>.agent.
        dispatch_src = Path(dispatch.__file__).read_text(encoding="utf-8")
        self.assertIn('agent = launch.get("agent")', dispatch_src)


class TestDispatchAgentResolution(unittest.TestCase):
    """The agent is resolved to a full path via `shutil.which` before launch, so
    a Windows `.cmd` shim (which CreateProcess cannot find via a bare name) is
    located, and an un-resolvable agent fails closed rather than dying with a
    cryptic FileNotFoundError at Popen time."""

    def test_agent_not_on_path_fails_closed(self):
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            work_root = scaffold.project_root / "tool-repo"
            work_root.mkdir()
            scaffold.write(
                "cartopian.toml",
                _toml("cartopian-claude-does-not-exist-xyz", work_roots='"tool-repo"'),
            )
            scaffold.write(
                "cartopian.local.toml", f'[work_roots]\ntool-repo = "{work_root}"\n'
            )
            task_path = _write_task_and_prompt(scaffold)
            fake_home = tmp_path / "home"
            fake_home.mkdir()
            stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)
            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("handoff agent not found on PATH", stderr)

    def test_build_launch_argv_windows_cmd_routes_through_comspec(self):
        cmd = r"C:\cartopian\wrappers\ps1\cartopian-claude.cmd"
        prompt = r"C:\proj\prompts\PROMPT-01-001.md"
        with mock.patch.dict(
            "cli.commands.dispatch.os.environ",
            {"COMSPEC": r"C:\Windows\System32\cmd.exe"},
            clear=False,
        ):
            argv = dispatch._build_launch_argv(cmd, prompt, is_windows=True)
        self.assertEqual(argv, [r"C:\Windows\System32\cmd.exe", "/c", cmd, prompt])

    def test_build_launch_argv_windows_cmd_absent_comspec_uses_system_root(self):
        # A curated process environment (e.g. the MCP server the harness spawns,
        # in which dispatch runs in-process) can drop COMSPEC. The interpreter
        # must still resolve to an absolute cmd.exe via %SystemRoot% rather than
        # a fragile bare name.
        cmd = r"C:\cartopian\wrappers\ps1\cartopian-claude.cmd"
        prompt = r"C:\proj\prompts\PROMPT-01-001.md"
        with mock.patch.dict(
            "cli.commands.dispatch.os.environ",
            {"SystemRoot": r"C:\Windows"},
            clear=True,
        ), mock.patch("cli.commands.dispatch.os.path.isfile", return_value=True):
            argv = dispatch._build_launch_argv(cmd, prompt, is_windows=True)
        expected_comspec = os.path.join(r"C:\Windows", "System32", "cmd.exe")
        self.assertEqual(argv, [expected_comspec, "/c", cmd, prompt])

    def test_resolve_comspec_prefers_comspec_then_system_root_then_which(self):
        # COMSPEC wins when set.
        with mock.patch.dict(
            "cli.commands.dispatch.os.environ",
            {"COMSPEC": r"C:\Windows\System32\cmd.exe"},
            clear=True,
        ):
            self.assertEqual(dispatch._resolve_comspec(), r"C:\Windows\System32\cmd.exe")
        # No COMSPEC, no SystemRoot, no PATH hit → last-resort bare name.
        with mock.patch.dict("cli.commands.dispatch.os.environ", {}, clear=True), mock.patch(
            "cli.commands.dispatch.shutil.which", return_value=None
        ):
            self.assertEqual(dispatch._resolve_comspec(), "cmd.exe")

    def test_build_launch_argv_posix_launches_directly(self):
        argv = dispatch._build_launch_argv(
            "/usr/local/bin/cartopian-claude", "/proj/prompts/PROMPT-01-001.md", is_windows=False
        )
        self.assertEqual(argv, ["/usr/local/bin/cartopian-claude", "/proj/prompts/PROMPT-01-001.md"])

    def test_build_launch_argv_windows_non_cmd_launches_directly(self):
        # A resolved `.exe` (or anything not .cmd/.bat) runs directly even on Windows.
        argv = dispatch._build_launch_argv(r"C:\tools\agent.exe", r"C:\p\PROMPT-01-001.md", is_windows=True)
        self.assertEqual(argv, [r"C:\tools\agent.exe", r"C:\p\PROMPT-01-001.md"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
