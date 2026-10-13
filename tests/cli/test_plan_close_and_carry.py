"""The operator ends a plan early and chooses what outlives it, in one ruling each.

RED: a plan whose work had drifted could close only once every task was done
or individually removed, every delivery obligation met, and every operator
request claimed or dispositioned; and ``reset-plan`` cleared every decision,
so a standing ruling either vanished with the plan or kept the whole pile of
plan-scoped routing alive with it. Legacy decisions without a ``Ruling:``
line indexed with an empty ruling cell.
GREEN: ``Closes plan: early`` in an operator decision turns unfinished work
into ``left_unfinished`` instead of blockers, stops dispatch, and routes
startup to closeout. ``Carries forward:`` in an operator decision names the
standing rulings ``reset-plan --carry`` keeps; plan-scoped rulings refuse to
carry. The index shows a derived first sentence where ``Ruling:`` is absent.
"""
from __future__ import annotations

from cli import trace_binding
from cli.commands import (
    close_audit,
    dispatch,
    next_action,
    reset_plan,
    write_continuity,
)
from tests.cli.test_decision_authority import AuthorityFixture, body
from tests.cli.test_fail_open_defects import invoke

LEGACY = (
    "# {id}: Legacy ruling\n\nDate: 2026-07-05\nStatus: locked\nSupersedes: none\n\n"
    "## Context\n\nWhy.\n\n## Decision\n\nOperator ruling, 2026-07-05:\n\n"
    "- **Fidensa is not open source.** All product source code is proprietary. More.\n"
)


class Fixture(AuthorityFixture):
    def task(self, task_id: str = "TASK-01-001", title: str = "Ship the exporter") -> None:
        (self.root / "tasks/open" / f"{task_id}.md").write_text(
            f"# {task_id}: {title}\n\nWork root: n/a\n", encoding="utf-8"
        )

    def index(self) -> str:
        return (self.root / "decisions/INDEX.md").read_text(encoding="utf-8")

    def reset(self, *carry: str):
        return invoke(
            reset_plan.handler, project_root=str(self.root), carry_standards=True,
            carry=list(carry),
        )

    def kept(self) -> list:
        return sorted(p.stem for p in (self.root / "decisions").glob("DEC-*.md"))


class RulingFallbackTests(Fixture):
    def test_a_decision_without_a_ruling_line_indexes_its_first_sentence(self) -> None:
        (self.root / "decisions/DEC-001.md").write_text(LEGACY.format(id="DEC-001"), encoding="utf-8")
        self.assertEqual(self.write("DEC-002", body("DEC-002"))[0], 0)
        row = next(line for line in self.index().splitlines() if "[DEC-001]" in line)
        self.assertIn(
            "| Fidensa is not open source: All product source code is proprietary. |", row
        )

    def test_the_stand_in_skips_markers_and_is_bounded(self) -> None:
        text = (
            "# DEC-001: X\n\n## Decision\n\n> quoted operator text\n\n"
            "Operator request evidence for: project:project: cs-0123456789abcdef/turn-1\n\n"
            + "word " * 80 + "\n"
        )
        line = trace_binding.ruling_line(text)
        self.assertTrue(line.startswith("word word"))
        self.assertLessEqual(len(line), trace_binding.DERIVED_RULING_CAP)
        self.assertTrue(line.endswith("..."))
        self.assertEqual(trace_binding.ruling_line("# DEC-001: X\n\nRuling: Stated.\n"), "Stated.")


class EarlyCloseTests(Fixture):
    def close(self, dec_id: str = "DEC-001") -> None:
        self.operator_ruling(dec_id, headers="Closes plan: early\n")

    def test_only_the_operator_closes_a_plan_early(self) -> None:
        cases = {
            "decision-authority-insufficient": body("DEC-001", headers="Closes plan: early\n"),
            "decision-closes-plan-invalid": body(
                "DEC-001", headers="Closes plan: now\n", decision=self.evidence(self.turn())
            ),
        }
        for code_name, content in cases.items():
            with self.subTest(code_name):
                code, _records, err = self.write("DEC-001", content)
                self.assertEqual(code, 1)
                self.assertIn(code_name, err)
        code, _records, err = self.write(
            "DEC-001", body("DEC-001", headers="Status: open\nCloses plan: early\n")
        )
        self.assertEqual(code, 0, err)
        self.assertIsNone(trace_binding.early_close_decision(self.root))

    def test_unfinished_work_stops_blocking_and_is_listed(self) -> None:
        self.task()
        record = close_audit.audit_project(self.root)
        self.assertFalse(record["closable"])
        self.assertEqual(record["left_unfinished"], [])

        self.close()
        record = close_audit.audit_project(self.root)
        self.assertTrue(record["closable"], record["blocking_reasons"])
        self.assertEqual(record["early_close"], "decisions/DEC-001.md")
        self.assertIn("TASK-01-001 (open): Ship the exporter", record["left_unfinished"])

    def test_the_project_summary_may_be_written_after_an_early_close(self) -> None:
        self.task()
        args = dict(project_root=str(self.root), content="Ended early.\n", content_file=None)
        code, _records, err = invoke(write_continuity.handler, **args)
        self.assertEqual(code, 1)
        self.assertIn("continuity-plan-not-closable", err)
        self.close()
        code, _records, err = invoke(write_continuity.handler, **args)
        self.assertEqual(code, 0, err)

    def test_no_task_is_dispatched_and_startup_routes_to_closeout(self) -> None:
        self.task()
        self.close()
        code, _records, err = invoke(
            dispatch.handler, task_path=str(self.root / "tasks/open/TASK-01-001.md"),
            prompt=None, role="coder", interactive=False,
        )
        self.assertEqual(code, 1)
        self.assertIn("plan-closed-early", err)
        verdict = next_action._startup_verdict(  # noqa: SLF001
            self.root, blocker_records=[], active_task={"id": "TASK-01-001", "status": "in-progress"},
            next_open_task=None, planning={"complete": True, "checkpoint": None},
            plan_complete=False, resolved={},
        )
        self.assertEqual(verdict["verdict"], "plan-complete")
        self.assertEqual(verdict["early_close"], "decisions/DEC-001.md")

    def test_a_superseded_early_close_resumes_the_plan(self) -> None:
        self.close()
        self.operator_ruling("DEC-002", headers="Supersedes: DEC-001\n")
        self.assertIsNone(trace_binding.early_close_decision(self.root))


class CarryForwardTests(Fixture):
    def test_carrying_ratifies_and_reset_keeps_only_what_is_carried(self) -> None:
        self.assertEqual(self.write("DEC-001", body("DEC-001", ruling="Proprietary."))[0], 0)
        self.assertEqual(self.write("DEC-002", body("DEC-002", ruling="Plan routing."))[0], 0)
        self.assertEqual(
            self.write("DEC-003", body("DEC-003", headers="Restates: DEC-001\nSupersedes: DEC-001\n"))[0], 0
        )
        self.operator_ruling("DEC-004", headers="Carries forward: DEC-003\n")
        self.assertIn("| pm, ratified by DEC-004 |", self.index())

        code, records, err = self.reset("DEC-004")
        self.assertEqual(code, 0, err)
        # DEC-001 stays as the pinned text DEC-003 restates; DEC-002 is gone.
        self.assertEqual(self.kept(), ["DEC-001", "DEC-003", "DEC-004"])
        self.assertEqual(records[0]["details"]["carried_decisions"], ["DEC-001", "DEC-003", "DEC-004"])
        index = self.index()
        self.assertIn("DEC-003 title", index)
        self.assertNotIn("DEC-002", index)
        self.assertEqual(trace_binding.authority_findings(self.root)["unratified"], [])

    def test_carried_rulings_alone_are_not_a_plan(self) -> None:
        from cli.commands import compose_state

        self.operator_ruling("DEC-001")
        self.operator_ruling("DEC-002", headers="Carries forward: DEC-001\n")
        self.assertFalse(compose_state._has_plan_artifacts(self.root))  # noqa: SLF001
        self.task()
        self.assertTrue(compose_state._has_plan_artifacts(self.root))  # noqa: SLF001
        self.assertEqual(self.reset("DEC-002")[0], 0)
        self.assertFalse(compose_state._has_plan_artifacts(self.root))  # noqa: SLF001
        self.assertIsNone(close_audit.audit_project(self.root)["closable"])
        # The next plan's first decision makes it a plan again.
        self.assertEqual(self.write("DEC-003", body("DEC-003"))[0], 0)
        self.assertTrue(compose_state._has_plan_artifacts(self.root))  # noqa: SLF001

    def test_reset_without_a_carry_clears_every_decision(self) -> None:
        self.operator_ruling("DEC-001")
        code, _records, err = self.reset()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.kept(), [])
        self.assertFalse((self.root / "decisions/INDEX.md").exists())

    def test_plan_scoped_rulings_do_not_carry(self) -> None:
        bound = body("DEC-001", decision=self.evidence(self.turn(), unit="task:TASK-01-001"))
        self.assertEqual(self.write("DEC-001", bound)[0], 0)
        self.operator_ruling("DEC-002", headers="Closes plan: early\n")
        self.assertEqual(self.write("DEC-003", body("DEC-003"))[0], 0)
        self.assertEqual(self.write("DEC-004", body("DEC-004", headers="Supersedes: DEC-003\n"))[0], 0)
        self.assertEqual(self.write("DEC-005", body("DEC-005"))[0], 0)
        self.assertEqual(self.write("DEC-006", body("DEC-006", headers="Amends: DEC-005\n"))[0], 0)
        cases = {
            "DEC-001": "binds evidence to this plan's task:TASK-01-001",
            "DEC-002": "it closes the plan",
            "DEC-003": "not a ruling in force",
            "DEC-005": "amendment, DEC-006, that is not carried",
            "DEC-006": "amends DEC-005, which is not carried",
            "DEC-009": "not a recorded decision",
        }
        for target, reason in cases.items():
            with self.subTest(target):
                content = body("DEC-007", headers=f"Carries forward: {target}\n",
                               decision=self.evidence(self.turn()))
                code, _records, err = self.write("DEC-007", content)
                self.assertEqual(code, 1)
                self.assertIn("decision-carries-invalid", err)
                self.assertIn(reason, err)
        self.operator_ruling("DEC-007", headers="Carries forward: DEC-005, DEC-006\n")

    def test_only_the_operator_carries_and_reset_rechecks(self) -> None:
        self.assertEqual(self.write("DEC-001", body("DEC-001"))[0], 0)
        code, _records, err = self.write("DEC-002", body("DEC-002", headers="Carries forward: DEC-001\n"))
        self.assertEqual(code, 1)
        self.assertIn("decision-authority-insufficient", err)
        code, _records, err = self.write(
            "DEC-002", body("DEC-002", headers="Carries forward: DEC-001\nCloses plan: early\n",
                            decision=self.evidence(self.turn()))
        )
        self.assertEqual(code, 1)
        self.assertIn("decision-closes-plan-invalid", err)

        # A carry that no longer holds refuses the whole reset; nothing is removed.
        self.operator_ruling("DEC-002", headers="Carries forward: DEC-001\n")
        self.operator_ruling("DEC-003", headers="Supersedes: DEC-001\n")
        code, _records, err = self.reset("DEC-002")
        self.assertEqual(code, 1)
        self.assertIn("carry-invalid", err)
        self.assertEqual(self.kept(), ["DEC-001", "DEC-002", "DEC-003"])
        code, _records, err = self.reset("DEC-003")
        self.assertEqual(code, 1)
        self.assertIn("has no `Carries forward:` header", err)

    def test_a_new_decision_cannot_take_an_id_a_carried_one_names(self) -> None:
        self.assertEqual(self.write("DEC-001", body("DEC-001"))[0], 0)
        self.assertEqual(self.write("DEC-002", body("DEC-002", headers="Supersedes: DEC-001\n"))[0], 0)
        self.operator_ruling("DEC-003", headers="Carries forward: DEC-002\n")
        self.assertEqual(self.reset("DEC-003")[0], 0)
        code, _records, err = self.write("DEC-001", body("DEC-001"))
        self.assertEqual(code, 1)
        self.assertIn("decision-id-reserved", err)
        self.assertEqual(self.write("DEC-004", body("DEC-004"))[0], 0)
