"""Tests for `cartopian dispatch` — mediated handoff launch (FR-006, G20, TASK-01-004).

Evidence gate (red-before-green):

- RED (captured in REPORT-01-004): before this command existed,
  ``cartopian dispatch ...`` was an unknown subcommand — a contained PM (no
  shell / process-exec tool, FR-002) had no route at all to launch a wrapper.
- GREEN (``TestDispatchPositive``): the mediated command launches a *stub*
  wrapper with the single absolute-prompt-path argv, ``CARTOPIAN_TIMEOUT``
  exported from the resolved ``[handoffs.<role>].timeout``, and cwd = the
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
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cli.commands import dispatch, wait_handoff
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, build_parser
from tests.scaffold import project_scaffold

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
                "cwd": os.getcwd(),
            },
            fh,
        )

time.sleep(float(os.environ.get("STUB_SLEEP", "0")))

if prompt and os.environ.get("STUB_NO_REPORT") != "1":
    prompts_dir = os.path.dirname(os.path.abspath(prompt))
    root = os.path.dirname(prompts_dir)
    base = os.path.basename(prompt)           # PROMPT-NN-NNN.md
    nn = base[len("PROMPT-"):-len(".md")]
    report = os.path.join(root, "reports", "REPORT-%s.md" % nn)
    os.makedirs(os.path.dirname(report), exist_ok=True)
    task = os.path.join(root, "tasks", "in-progress", "TASK-%s-x.md" % nn)
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
    code_comments: str = "",
) -> str:
    wr = f'work_roots = [{work_roots}]\n' if work_roots else ""
    model_line = f'model = "{model}"\n' if model else ""
    cc_line = f'code_comments = "{code_comments}"\n' if code_comments else ""
    return (
        "[project]\n"
        'id = "dispatch-proj"\n'
        'name = "Dispatch Project"\n'
        'protocol_version = "v0.3.0"\n'
        f"{wr}"
        "\n"
        "[roles]\n"
        'coder = "Implements tasks per spec."\n'
        "\n"
        "[handoffs.coder]\n"
        f'agent = "{agent}"\n'
        f"{model_line}"
        f"{cc_line}"
        "auto_start = true\n"
        f'timeout = "{timeout}"\n'
    )


def _write_task_and_prompt(scaffold, nn_nnn: str = "01-004") -> Path:
    task_path = scaffold.write(
        f"tasks/in-progress/TASK-{nn_nnn}-mediated.md",
        (
            f"# TASK-{nn_nnn}: Mediated dispatch\n\n"
            "Phase: PHASE-01-x\n"
            "Work root: tool-repo\n"
            "Assignee: coder\n\n"
            "## Goal\n\nLaunch via mediated dispatch.\n"
        ),
    )
    scaffold.write(
        f"prompts/PROMPT-{nn_nnn}.md",
        f"# PROMPT-{nn_nnn}\n\n## Your task\n\nDo the work.\n",
    )
    return task_path


def _dispatch(task_path: str, role: str, fake_home: Path):
    """Invoke dispatch.handler with a fake HOME so the real global config can't leak."""
    args = argparse.Namespace(task_path=task_path, role=role)
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
            work_root.mkdir()
            scaffold.write(
                "cartopian.toml",
                _toml(str(stub), work_roots='"tool-repo"', model="stub-model-x"),
            )
            scaffold.write(
                "cartopian.local.toml",
                f'[work_roots]\ntool-repo = "{work_root}"\n',
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
            self.assertEqual(rec["handoff_target"], str(stub))
            self.assertEqual(rec["model"], "stub-model-x")
            self.assertEqual(rec["prompt_path"], str(prompt_path))
            self.assertEqual(rec["timeout"], "30m")
            # DEC-011: cwd is the primary work root, never the governing project.
            self.assertEqual(rec["cwd"], rec["work_roots"][0]["absolute_path"])
            self.assertEqual(Path(rec["cwd"]).name, "tool-repo")
            self.assertNotEqual(Path(rec["cwd"]).resolve(), project_root)
            self.assertTrue(rec["expected_report_path"].endswith("/reports/REPORT-01-004.md"))
            # Scope = work roots + only the report dir; the project root is absent.
            scope_resolved = [str(Path(d).resolve()) for d in rec["scope_dirs"]]
            self.assertIn(str((project_root / "reports").resolve()), scope_resolved)
            self.assertNotIn(str(project_root), scope_resolved)
            self.assertEqual(rec["code_comments"], "minimal")

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
            # DEC-011: the wrapper actually ran with cwd = the work root.
            self.assertEqual(Path(cap["cwd"]).name, "tool-repo")
            self.assertNotEqual(os.path.realpath(cap["cwd"]), str(project_root))

    def test_clears_stale_model_when_handoff_has_no_model(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"

            # No model in [handoffs.coder] — a stale CARTOPIAN_MODEL inherited
            # from the parent environment must NOT leak into the wrapper.
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
            }
            with mock.patch.dict(os.environ, env, clear=False):
                stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

            self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
            rec = json.loads(stdout.strip())
            self.assertIsNone(rec["model"])

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


    def test_code_comments_resolves_and_fails_closed_to_minimal(self) -> None:
        # DEC-011 §4: the resolved code_comments level is emitted and exported;
        # an unknown value fails closed to `minimal`, a valid one is honored.
        for configured, expected in (("none", "none"), ("bogus", "minimal"), ("", "minimal")):
            with self.subTest(configured=configured), \
                    project_scaffold(cartopian_toml="") as scaffold, \
                    tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
                tmp_path = Path(tmp)
                stub = _make_stub(tmp_path)
                work_root = scaffold.project_root / "tool-repo"
                work_root.mkdir()
                scaffold.write(
                    "cartopian.toml",
                    _toml(str(stub), work_roots='"tool-repo"', code_comments=configured),
                )
                scaffold.write(
                    "cartopian.local.toml",
                    f'[work_roots]\ntool-repo = "{work_root}"\n',
                )
                task_path = _write_task_and_prompt(scaffold)
                fake_home = tmp_path / "home"
                fake_home.mkdir()

                env = {"STUB_CAPTURE": str(tmp_path / "capture.json"), "STUB_NO_REPORT": "1"}
                with mock.patch.dict(os.environ, env, clear=False):
                    stdout, stderr, rc = _dispatch(str(task_path), "coder", fake_home)

                self.assertEqual(rc, EXIT_OK, msg=f"stderr={stderr!r}")
                rec = json.loads(stdout.strip())
                self.assertEqual(rec["code_comments"], expected)


class TestDispatchFailClosed(unittest.TestCase):
    """Fail-closed gates: every refusal returns non-zero and launches nothing."""

    def _fake_home(self, tmp_path: Path) -> Path:
        home = tmp_path / "home"
        home.mkdir()
        return home

    def test_unmapped_work_root_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            # Declare a work root but provide NO cartopian.local.toml mapping.
            scaffold.write("cartopian.toml", _toml(str(stub), work_roots='"tool-repo"'))
            task_path = _write_task_and_prompt(scaffold)

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[work-root]", stderr)
            self.assertFalse(capture.exists(), "wrapper was launched despite fail-closed")

    def test_missing_work_root_path_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub), work_roots='"tool-repo"'))
            # Map the work root to a path that does not exist on disk.
            scaffold.write(
                "cartopian.local.toml",
                f'[work_roots]\ntool-repo = "{tmp_path / "nope"}"\n',
            )
            task_path = _write_task_and_prompt(scaffold)

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertIn("[work-root]", stderr)
            self.assertIn("does not exist", stderr)
            self.assertFalse(capture.exists())

    def test_no_work_root_declared_fails_closed(self) -> None:
        # An assignee must run inside a work root, never the governing project.
        # A project that declares no work root is refused, not launched in the
        # project root (which would expose its management artifacts).
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub)))  # no work_roots
            task_path = _write_task_and_prompt(scaffold)

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("no work root", stderr)
            self.assertFalse(capture.exists(), "wrapper launched despite no work root")

    def test_work_root_equal_to_project_root_fails_closed(self) -> None:
        # Defense in depth: a work root mapped onto the governing project root
        # (or an ancestor) would expose the project's management artifacts.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub), work_roots='"tool-repo"'))
            scaffold.write(
                "cartopian.local.toml",
                f'[work_roots]\ntool-repo = "{scaffold.project_root}"\n',
            )
            task_path = _write_task_and_prompt(scaffold)

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertIn("[work-root]", stderr)
            self.assertIn("governing project root", stderr)
            self.assertFalse(capture.exists())

    def test_missing_role_block_fails_closed(self) -> None:
        _TOML_NO_BLOCK = (
            "[project]\n"
            'id = "p"\n'
            'name = "P"\n'
            'protocol_version = "v0.3.0"\n'
            "\n"
            "[roles]\n"
            'coder = "Implements tasks per spec."\n'
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
            self.assertIn("[handoffs.coder]", stderr)

    def test_empty_model_fails_closed(self) -> None:
        # A set-but-empty model would diverge the record from the export
        # (record reports "", nothing exported) — refuse to launch instead.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            toml = _toml(str(stub)).replace(
                "auto_start = true\n", 'model = ""\nauto_start = true\n'
            )
            scaffold.write("cartopian.toml", toml)
            task_path = _write_task_and_prompt(scaffold)

            with mock.patch.dict(os.environ, {"STUB_CAPTURE": str(capture)}, clear=False):
                stdout, stderr, rc = _dispatch(
                    str(task_path), "coder", self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[guard]", stderr)
            self.assertIn("[handoffs.coder].model", stderr)
            self.assertFalse(capture.exists(), "wrapper was launched despite fail-closed")

    def test_missing_prompt_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory(prefix="cartopian-stub-") as tmp:
            tmp_path = Path(tmp)
            stub = _make_stub(tmp_path)
            capture = tmp_path / "capture.json"
            scaffold.write("cartopian.toml", _toml(str(stub)))
            # Task present, but no prompts/PROMPT-01-004.md written.
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-004-mediated.md",
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


class TestDispatchNoRawExec(unittest.TestCase):
    """Containment negative test: the mediated, config-bound dispatch is the
    ONLY process-launch route on the PM tool surface.

    RED framing: before containment a PM with a shell could run the wrapper
    directly (`subprocess.Popen([wrapper, prompt])` — a raw exec path). Under
    the FR-002 floor that capability is removed, so the only reachable launch
    is `dispatch`, whose executable is sourced from operator config and which
    exposes no argument for injecting an arbitrary command.
    """

    def test_dispatch_exposes_no_arbitrary_executable_argument(self) -> None:
        # The dispatch subparser must accept only `task_path` + `--role`.
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
            parser.parse_args(["dispatch", "/abs/TASK-01-004-x.md", "--role", "coder", "--agent", "/bin/sh"])
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
        # the launched program is the resolved [handoffs.<role>].agent.
        dispatch_src = Path(dispatch.__file__).read_text(encoding="utf-8")
        self.assertIn('agent = role_handoff.get("agent")', dispatch_src)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
