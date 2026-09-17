"""Static regression tests for the session-boundary request class.

Guards the closeout contract in protocol and skill prose:

- "let's continue in a new session" names a *session* boundary, not an
  execution directive. The one authorized operation is session closeout.
- Closeout is `STATE.md` plus the configured git behavior. Persisted project
  state is the whole continuity mechanism, so no task, plan edit, or handoff
  document is created to carry context into the next session.
- A bare "continue" that names no boundary stays an execution directive, so
  the disambiguation cannot swallow ordinary resumption.

These are text-surface checks. The regression they guard (a PM inventing a
carry-forward task or writing session handoff instructions into
`IMPLEMENTATION_PLAN.md`) reproduces on any host and fails no code-level test.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONVENTIONS = REPO_ROOT / "protocol" / "CONVENTIONS.md"
SKILLS_DIR = REPO_ROOT / "skills"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _request_intent_section(text: str) -> str:
    start = text.index("## Request Intent")
    end = text.index("\n## ", start + 1)
    return text[start:end]


class ConventionsSessionBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = _read(CONVENTIONS)
        self.section = _request_intent_section(self.text)

    def test_request_intent_enumerates_four_classes(self) -> None:
        self.assertIn("Operator requests fall into four classes.", self.section)
        for label in (
            "- **Execution directives**",
            "- **Informational requests**",
            "- **Scoped directives**",
            "- **Session-boundary requests**",
        ):
            self.assertIn(label, self.section, msg=f"intent class missing: {label}")

    def test_session_boundary_bullet_carries_its_examples(self) -> None:
        m = re.search(r"- \*\*Session-boundary requests\*\* — (.+)", self.section)
        self.assertIsNotNone(m, msg="Session-boundary requests bullet missing")
        bullet = m.group(1)
        self.assertIn('"let\'s continue in a new session"', bullet)
        self.assertIn('"pick this up in a new chat"', bullet)

    def test_session_boundary_authorizes_only_closeout(self) -> None:
        m = re.search(r"- \*\*Session-boundary requests\*\* — (.+)", self.section)
        bullet = m.group(1)
        self.assertIn("The one authorized operation is session closeout", bullet)
        self.assertIn("`skills/run-task.md` § Stage 8", bullet)

    def test_closeout_creates_no_carry_forward_artifact(self) -> None:
        m = re.search(r"- \*\*Session-boundary requests\*\* — (.+)", self.section)
        bullet = m.group(1)
        # The load-bearing prohibition: the two observed regressions are a new
        # task and a plan edit carrying session handoff instructions.
        self.assertIn("no carry-forward artifact of any kind", bullet)
        for artifact in ("no new task", "no plan edit", "no handoff document"):
            self.assertIn(artifact, bullet, msg=f"prohibition missing: {artifact}")
        self.assertIn(
            "the next session resumes through registry selection and the "
            "`next-action` startup verdict",
            bullet,
        )

    def test_bare_continue_remains_an_execution_directive(self) -> None:
        # Without this clause the disambiguation would swallow plain "continue",
        # which Execution directives still owns.
        m = re.search(r"- \*\*Session-boundary requests\*\* — (.+)", self.section)
        self.assertIn(
            'a bare "continue" naming no boundary stays an execution directive',
            m.group(1),
        )

    def test_literal_authorization_covers_every_class(self) -> None:
        # The artifact-invention prohibition is not scoped-directive-only.
        self.assertIn(
            "Authorization is literal. A directive of any class does not "
            "implicitly authorize creating a task, plan item, decision, prompt, "
            "request capture, review, handoff, or other governance artifact",
            self.section,
        )


class SkillSessionCloseoutTest(unittest.TestCase):
    def test_run_task_stage_8_handles_an_operator_session_boundary(self) -> None:
        text = _read(SKILLS_DIR / "run-task.md")
        stage = text[text.index("## Stage 8 - Close Session"):]
        self.assertIn('"let\'s continue in a new session"', stage)
        self.assertIn("ends the run under every `run_boundary`", stage)
        self.assertIn("The refreshed `STATE.md` *is* the handoff.", stage)
        self.assertIn(
            "Do not write or edit a task, plan, phase, spec, prompt, report, "
            "decision, or any other artifact to carry context into the next session",
            stage,
        )
        # An interrupted task is not evidence that a handoff record is needed.
        self.assertIn("stays in `tasks/in-progress/`", stage)

    def test_start_session_stage_3_routes_the_boundary_request(self) -> None:
        text = _read(SKILLS_DIR / "start-session.md")
        stage = text[text.index("## Stage 3 - Take The Next Action"):]
        self.assertIn("- **Session-boundary request**:", stage)
        self.assertIn("`run task` § Stage 8", stage)
        self.assertIn("no carry-forward artifact", stage)


class StartupSliceCarriesTheRuleTest(unittest.TestCase):
    def test_session_boundary_class_reaches_the_startup_slice(self) -> None:
        # Classification happens before any deferred slice is read, so the rule
        # is only effective if it ships in the startup payload itself.
        from mcp_server import server

        result = server.call_tool(
            "read_context", {"uri": "cartopian://protocol/CONVENTIONS/startup"}
        )
        self.assertFalse(result["isError"], msg=result)
        slice_text = "".join(block["text"] for block in result["content"])
        self.assertIn("- **Session-boundary requests** —", slice_text)
        self.assertIn("no carry-forward artifact of any kind", slice_text)


if __name__ == "__main__":
    unittest.main()
