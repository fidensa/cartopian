"""Keystone acceptance for lifecycle completeness.

A scripted *contained* PM run of **plan → assign → review → close** completes
using only Cartopian commands — the structured authoring commands plus the
pre-existing tool surface — with **zero** steps requiring a raw write /
dir-op / exec.

Fail-closed red→green in one module:

- **RED** — the identical lifecycle is driven against the *pre-extension* tool
  surface (the new verbs pruned from the dispatcher). The very first authoring
  step (``write-requirements``) hits an unknown-subcommand wall: the contained
  PM, lacking raw ``Write``, deadlocks. This proves the gap is real, so green
  cannot be reached on a stale/empty surface.
- **GREEN** — the full surface drives every step to exit 0 and the artifacts
  land on disk. Every PM step is a Cartopian verb; the only direct file writes
  are the assignee REPORT and reviewer REVIEW, which stand in for the
  dispatched coder/reviewer agents (G20, out of scope here) — explicitly *not*
  PM operations.
"""
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import cli.main as cli_main

# The new structured-authoring verbs. Pruning them reproduces the
# pre-extension contained-PM surface for the red capture.
NEW_VERBS = (
    "write-requirements", "write-plan", "write-standards",
    "write-phase", "write-task", "write-spec", "write-prompt", "write-decision",
    "write-state", "reset-plan",
)


def _drive(parser, verb, *args):
    """Run one CLI step against ``parser`` in-process; return (code, records, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            ns = parser.parse_args([verb, *args])
            handler = getattr(ns, "_handler", None)
            code = handler(ns) if handler is not None else 2
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return code, records, err.getvalue()


def _pruned_parser():
    """Build a dispatcher with the new verbs removed (pre-extension surface)."""
    original = cli_main.SUBCOMMANDS
    cli_main.SUBCOMMANDS = [v for v in original if v not in NEW_VERBS]
    try:
        return cli_main.build_parser()
    finally:
        cli_main.SUBCOMMANDS = original


class TestRedMissingCommandDeadlock(unittest.TestCase):
    def test_pre_fr005_surface_deadlocks_at_first_authoring_step(self):
        with TemporaryDirectory() as tmp:
            proj = str(Path(tmp) / "proj")
            parser = _pruned_parser()
            # Bring-up succeeds on the pre-existing surface.
            code, _, err = _drive(parser, "scaffold-project", proj)
            self.assertEqual(code, 0, msg=err)
            code, _, err = _drive(
                parser, "generate-config", proj, "--name", "Demo", "--id", "demo",
            )
            self.assertEqual(code, 0, msg=err)
            # The first plan authoring step has no command → deadlock.
            code, recs, err = _drive(parser, "write-requirements", proj, "--content", "x")
            self.assertNotEqual(code, 0, "red: write-requirements must be unreachable pre-FR-005")
            self.assertEqual(recs, [])

    def test_every_fr005_verb_is_unknown_on_pruned_surface(self):
        parser = _pruned_parser()
        for verb in NEW_VERBS:
            code, recs, _ = _drive(parser, verb, "/tmp/whatever", "--content", "x")
            self.assertNotEqual(code, 0, msg=f"{verb} should be unknown on pruned surface")
            self.assertEqual(recs, [])


class TestGreenLifecycleCompletes(unittest.TestCase):
    def _run(self, verb, *args):
        code, recs, err = _drive(self.parser, verb, *args)
        self.assertEqual(code, 0, msg=f"step {verb} failed (exit {code}): {err}")
        return recs

    def test_plan_assign_review_close_with_only_cartopian_commands(self):
        self.parser = cli_main.build_parser()
        with TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            ps = str(proj)
            home = Path(tmp) / "home"
            home.mkdir()
            env = {"HOME": str(home), "USERPROFILE": str(home)}

            with mock.patch.dict(os.environ, env):
                # --- bring-up ---
                self._run("scaffold-project", ps)
                self._run(
                    "generate-config", ps,
                    "--name", "Lifecycle Demo", "--id", "lifecycle-demo",
                    "--role", "reviewer=Reviews completed outcomes.",
                    "--review-planning", "off",
                    "--review-task-closure", "required",
                    "--review-task-role", "reviewer",
                )
                # Register so the registry-scoped delete-prompt/delete-report
                # (pre-existing surface) can resolve the project at close.
                self._run("register-project", ps)
                self._lifecycle(proj, ps)

    def _lifecycle(self, proj, ps):
        # --- PLAN (G1–G7) ---
        self._run("write-requirements", ps, "--content", "# Requirements\n\nFR-1\n")
        self._run("write-plan", ps, "--content", "# Implementation Plan\n\nP01-BUILD-001\n")
        self._run("write-standards", ps, "--content", "# Standards\n")
        self._run("write-phase", ps, "--phase-id", "PHASE-01-core", "--content",
                  "# PHASE-01-core: Core\n")
        self._run("write-spec", ps, "--spec-id", "SPEC-01-001", "--slug", "thing",
                  "--content", "# SPEC-01-001\n")
        self._run("write-task", ps, "--task-id", "TASK-01-001", "--slug", "do-thing",
                  "--content",
                  "# TASK-01-001: do thing\n\nPhase: PHASE-01-core\nPlan ref: P01-BUILD-001\n"
                  "Evidence gate: n/a\n\n## Acceptance\n\n- [ ] done\n")
        self._run("write-prompt", ps, "--prompt-id", "PROMPT-01-001",
                  "--content", "# PROMPT-01-001\n")

        task_path = proj / "tasks" / "open" / "TASK-01-001-do-thing.md"
        self.assertTrue(task_path.is_file())

        # --- ASSIGN (G7 prompt already written) ---
        self._run("move-task", str(task_path), "in-progress")
        task_path = proj / "tasks" / "in-progress" / "TASK-01-001-do-thing.md"
        self.assertTrue(task_path.is_file())

        # Dispatched coder produces the report (out of scope here):
        # NOT a PM operation — stands in for the assignee agent.
        (proj / "reports" / "REPORT-01-001.md").write_text(
            "# REPORT-01-001\n\nStatus: complete\n\n## Identity\n\n"
            "- Task ID: TASK-01-001\n", encoding="utf-8",
        )

        # --- REVIEW (G9, G10, G11) ---
        self._run("move-task", str(task_path), "in-review")
        task_path = proj / "tasks" / "in-review" / "TASK-01-001-do-thing.md"
        self.assertTrue(task_path.is_file())

        # Dispatched reviewer produces the review verdict (out of scope).
        (proj / "reviews" / "REVIEW-01-001.md").write_text(
            "# REVIEW-01-001\n\nVerdict: approve\n", encoding="utf-8",
        )

        # Record a decision + index in one command (G9 + G10).
        self._run("write-decision", ps, "--dec-id", "DEC-001", "--slug", "approach",
                  "--title", "Chosen approach", "--date", "2026-06-01",
                  "--content", "# DEC-001\n")
        self.assertTrue((proj / "decisions" / "DEC-001-approach.md").is_file())
        self.assertIn("[DEC-001]", (proj / "decisions" / "INDEX.md").read_text(encoding="utf-8"))

        # Persist STATE.md; write-state composes the canonical body itself
        # (G11) — a PM-authored body is refused while plan artifacts exist.
        recs = self._run("compose-state", ps)
        rendered = recs[0]["rendered_body"]
        self.assertIsNotNone(rendered, "active plan should render a STATE body")
        self._run("write-state", ps)
        state_text = (proj / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("Lifecycle Demo", state_text)
        self.assertEqual(state_text.rstrip("\n"), rendered.rstrip("\n"))

        # Approve → done.
        self._run("move-task", str(task_path), "done")
        self.assertTrue((proj / "tasks" / "done" / "TASK-01-001-do-thing.md").is_file())

        # --- CLOSE (G13, G14, G15) ---
        self._run("delete-prompt", str(proj / "prompts" / "PROMPT-01-001.md"))
        self._run("delete-report", str(proj / "reports" / "REPORT-01-001.md"))
        self._run("reset-plan", ps)

        # Live surface is empty; compose-state returns the no-plan shape.
        recs = self._run("compose-state", ps)
        self.assertIsNone(recs[0]["rendered_body"], "post-reset must be no-plan")
        self.assertFalse((proj / "REQUIREMENTS.md").exists())
        self.assertFalse((proj / "tasks" / "done" / "TASK-01-001-do-thing.md").exists())

        # Author the closeout STATE directly (G11, no-plan variant), under 5KB.
        self._run("write-state", ps, "--content",
                  "# Lifecycle Demo - State\n\n## Current phase\n\nNo active plan.\n")
        self.assertLess((proj / "STATE.md").stat().st_size, 5 * 1024)


if __name__ == "__main__":
    unittest.main()
