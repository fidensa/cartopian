"""`cartopian dispatch --recapture` — opt-in, evidence-gated reviewer recapture.

The agent-neutral launch policy half of reviewer recapture (the wrapper-enforcement half
lives in tests/wrappers/test_reviewer_recapture.py). dispatch is the mediated,
role-from-config launcher; it exports the agent-neutral role-level signal
``CARTOPIAN_REVIEW_RECAPTURE=1`` to the wrapper ONLY when recapture is opted in
AND the task declares live/harness evidence (``Evidence gate: required``):

* opt-in: ``--recapture`` is off by default, so an ordinary review exports nothing.
* evidence-gated / domain-neutral: ``--recapture`` on a task with no such gate
  (Evidence gate: n/a or absent — the shape of research / ops / creative reviews)
  is refused fail-closed; nothing launches and no signal is exported.
* role-level / agent-neutral: the role is resolved from ``[handoffs.<role>]``; the
  exported env var carries no agent name and no coder/coding assumption.

Popen is mocked so the exact env handed to the wrapper is inspected race-free.
"""
import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.commands import dispatch
from cli.main import EXIT_FAIL, EXIT_OK
from tests.scaffold import project_scaffold

RECAPTURE_ENV = "CARTOPIAN_REVIEW_RECAPTURE"


def _toml(agent: str, *, role: str = "reviewer") -> str:
    return (
        "[project]\n"
        'id = "recap-proj"\n'
        'name = "Recapture Project"\n'
        'protocol_version = "v0.3.0"\n'
        'work_roots = ["tool-repo"]\n'
        "\n"
        "[roles]\n"
        f'{role} = "Reviews per acceptance evidence."\n'
        "\n"
        f"[handoffs.{role}]\n"
        f'agent = "{agent}"\n'
        "auto_start = false\n"
        'timeout = "30m"\n'
    )


def _write_task_and_prompt(scaffold, evidence_gate: str, nn_nnn: str = "03-007") -> Path:
    gate_line = f"Evidence gate: {evidence_gate}\n" if evidence_gate else ""
    task_path = scaffold.write(
        f"tasks/in-review/TASK-{nn_nnn}-recap.md",
        (
            f"# TASK-{nn_nnn}: Recapture review\n\n"
            "Phase: PHASE-03-x\n"
            "Work root: tool-repo\n"
            "Assignee: reviewer\n"
            f"{gate_line}\n"
            "## Goal\n\nReview the evidence-gated task.\n"
        ),
    )
    scaffold.write(
        f"prompts/PROMPT-{nn_nnn}.md",
        f"# PROMPT-{nn_nnn}\n\n## Your task\n\nReview.\n",
    )
    return task_path


def _map_work_root(scaffold) -> None:
    """Declare + map the `tool-repo` work root so dispatch (which now fails closed
    on no work root) can resolve a contained launch cwd."""
    wr = scaffold.project_root / "tool-repo"
    wr.mkdir(exist_ok=True)
    scaffold.write("cartopian.local.toml", f'[work_roots]\ntool-repo = "{wr}"\n')


class _FakeProc:
    pid = 4321


def _dispatch_capturing_env(task_path: str, role: str, recapture: bool, fake_home: Path):
    """Run dispatch.handler with Popen mocked; return (rc, stdout, stderr, captured_env).

    captured_env is None when no launch occurred (a fail-closed refusal)."""
    args = argparse.Namespace(task_path=task_path, role=role, recapture=recapture)
    captured = {}

    def fake_popen(argv, cwd=None, env=None, start_new_session=None):
        captured["env"] = env
        captured["argv"] = argv
        return _FakeProc()

    out, err = io.StringIO(), io.StringIO()
    with mock.patch("cli.commands.dispatch.subprocess.Popen", side_effect=fake_popen), \
            mock.patch("cli.commands.dispatch.shutil.which", side_effect=lambda cmd: cmd), \
            mock.patch("cli.commands.dispatch.Path.home", return_value=fake_home):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dispatch.handler(args)
    return rc, out.getvalue(), err.getvalue(), captured.get("env")


class TestRecaptureDispatch(unittest.TestCase):
    def _fake_home(self, tmp: Path) -> Path:
        home = tmp / "home"
        home.mkdir()
        return home

    def test_recapture_on_evidence_gated_task_exports_signal(self):
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scaffold.write("cartopian.toml", _toml("/bin/true"))
            _map_work_root(scaffold)
            task_path = _write_task_and_prompt(scaffold, "required")

            rc, out, err, env = _dispatch_capturing_env(
                str(task_path), "reviewer", True, self._fake_home(tmp_path)
            )

            self.assertEqual(rc, EXIT_OK, msg=err)
            self.assertIsNotNone(env, "wrapper was not launched")
            self.assertEqual(env.get(RECAPTURE_ENV), "1",
                             "agent-neutral recapture signal not exported")

    def test_recapture_on_non_evidence_task_is_refused_fail_closed(self):
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scaffold.write("cartopian.toml", _toml("/bin/true"))
            _map_work_root(scaffold)
            task_path = _write_task_and_prompt(scaffold, "n/a")

            rc, out, err, env = _dispatch_capturing_env(
                str(task_path), "reviewer", True, self._fake_home(tmp_path)
            )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertIn("[guard]", err)
            self.assertIn("live/harness evidence", err)
            self.assertIsNone(env, "wrapper launched despite a non-evidence-gated task")
            self.assertEqual(out, "")

    def test_recapture_on_task_without_gate_header_is_refused(self):
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scaffold.write("cartopian.toml", _toml("/bin/true"))
            _map_work_root(scaffold)
            task_path = _write_task_and_prompt(scaffold, "")  # no Evidence gate line

            rc, out, err, env = _dispatch_capturing_env(
                str(task_path), "reviewer", True, self._fake_home(tmp_path)
            )

            self.assertEqual(rc, EXIT_FAIL)
            self.assertIsNone(env)

    def test_no_recapture_flag_exports_no_signal(self):
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scaffold.write("cartopian.toml", _toml("/bin/true"))
            _map_work_root(scaffold)
            # Even an evidence-gated task gets no signal unless explicitly opted in.
            task_path = _write_task_and_prompt(scaffold, "required")

            rc, out, err, env = _dispatch_capturing_env(
                str(task_path), "reviewer", False, self._fake_home(tmp_path)
            )

            self.assertEqual(rc, EXIT_OK, msg=err)
            self.assertIsNotNone(env)
            self.assertNotIn(RECAPTURE_ENV, env, "signal exported without opt-in")

    def test_stale_recapture_env_is_cleared_when_off(self):
        # A value inherited from the parent environment must not leak through.
        with project_scaffold(cartopian_toml="") as scaffold, \
                tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scaffold.write("cartopian.toml", _toml("/bin/true"))
            _map_work_root(scaffold)
            task_path = _write_task_and_prompt(scaffold, "required")

            with mock.patch.dict("os.environ", {RECAPTURE_ENV: "1"}, clear=False):
                rc, out, err, env = _dispatch_capturing_env(
                    str(task_path), "reviewer", False, self._fake_home(tmp_path)
                )

            self.assertEqual(rc, EXIT_OK, msg=err)
            self.assertNotIn(RECAPTURE_ENV, env, "stale signal leaked when recapture off")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
