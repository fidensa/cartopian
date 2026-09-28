"""Contract for decision proximity: the surface that names the locked decisions
a new ruling sits beside.

Every other Cartopian gate verifies the sources an author *declared*. This one
exists for the decision nobody thought to declare, so the tests here pin the
properties that make it trustworthy rather than merely present: liveness
follows the same rule the rest of the protocol uses, ranking survives the
identifiers and digests that two unrelated decisions routinely share, the
budget is fixed, and the surface is advisory in the strong sense that no
failure inside it can cost a caller a decision already written to disk.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path

from cli import decision_neighbors
from cli.commands import write_decision
from tests.scaffold import project_scaffold
from tests.test_trace_seams import SeamFixture


def decision(
    title: str,
    ruling: str,
    *,
    status: str = "locked",
    supersedes: str = "none",
    context: str = "Recorded during the phase.",
) -> str:
    return (
        f"# {title}\n\n"
        "Date: 2026-09-01\n"
        f"Status: {status}\n"
        f"Supersedes: {supersedes}\n\n"
        f"## Context\n\n{context}\n\n"
        f"## Decision\n\n{ruling}\n\n"
        "## Consequences\n\nThe phase proceeds under this ruling.\n"
    )


# Structurally the case this surface exists for: a later decision amends a
# demonstration's scope while two older locked decisions still carry the
# constraint it contradicts, and neither is named in the new body.
BOUNDARY = decision(
    "DEC-001: Launch under the contained-guest boundary with syscall evidence",
    "The contained guest boundary holds for the run. Genuine guest escape "
    "invalidates the evidence and the run is void.",
)
LIMITS = decision(
    "DEC-002: Accepted evidence-integrity limits and the witness boundary",
    "Genuine guest escape is excluded from scope. The host-held witness "
    "boundary and the oracle divergence check both stay mandatory.",
)
ROSTER = decision(
    "DEC-003: Weekly roster rotation for the on-call schedule",
    "The on-call roster rotates weekly at 09:00 local. Handover notes are "
    "posted to the shared calendar.",
)
INVOICING = decision(
    "DEC-004: Invoice numbering and the quarterly billing cutoff",
    "Invoice numbers are allocated per quarter. The billing cutoff falls on "
    "the final business day.",
)
AMENDMENT = decision(
    "DEC-005: Demonstration scope amended to permit contained guest escape",
    "Contained guest escape no longer voids the run by itself. The host-held "
    "witness boundary and the oracle divergence check are unchanged.",
)


class DecisionCorpusTest(unittest.TestCase):
    """Liveness must match the rule the rest of the protocol already applies."""

    def setUp(self) -> None:
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root

    def test_open_and_superseded_decisions_never_surface(self) -> None:
        self.scaffold.write("decisions/DEC-001.md", BOUNDARY)
        self.scaffold.write(
            "decisions/DEC-002.md", decision("DEC-002: Proposal", "Not yet ruled.", status="open")
        )
        self.scaffold.write("decisions/DEC-003.md", ROSTER)
        self.scaffold.write(
            "decisions/DEC-004.md",
            decision("DEC-004: Roster rotation restated", "Rotation moves to fortnightly.", supersedes="DEC-003"),
        )
        live = decision_neighbors.corpus(self.root)
        self.assertEqual(sorted(live), ["DEC-001", "DEC-004"])

    def test_missing_decisions_directory_is_not_an_error(self) -> None:
        empty = project_scaffold()
        self.addCleanup(empty.cleanup)
        (empty.project_root / "decisions").rmdir()
        self.assertEqual(decision_neighbors.corpus(empty.project_root), {})
        self.assertEqual(decision_neighbors.neighbors(empty.project_root, AMENDMENT), [])
        self.assertEqual(decision_neighbors.unreferenced_pairs(empty.project_root), [])


class NeighborRankingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        for name, body in (
            ("DEC-001", BOUNDARY), ("DEC-002", LIMITS),
            ("DEC-003", ROSTER), ("DEC-004", INVOICING),
        ):
            self.scaffold.write(f"decisions/{name}.md", body)

    def test_surfaces_the_constraints_the_amendment_contradicts(self) -> None:
        """The regression that motivates the surface.

        The amendment names none of the older decisions, exactly as the real
        miss did. Both decisions carrying the constraint it reverses must be
        named ahead of the unrelated ones.
        """
        rows = decision_neighbors.neighbors(self.root, AMENDMENT)
        surfaced = [row["id"] for row in rows]
        self.assertIn("DEC-001", surfaced)
        self.assertIn("DEC-002", surfaced)
        self.assertLess(surfaced.index("DEC-002"), surfaced.index("DEC-003") if "DEC-003" in surfaced else len(surfaced))

    def test_budget_is_fixed_and_scores_descend(self) -> None:
        rows = decision_neighbors.neighbors(self.root, AMENDMENT)
        self.assertLessEqual(len(rows), decision_neighbors.NEIGHBOR_BUDGET)
        self.assertEqual([r["score"] for r in rows], sorted((r["score"] for r in rows), reverse=True))
        for row in rows:
            self.assertLessEqual(len(row["shared_terms"]), decision_neighbors.SHARED_TERMS_PER_ROW)
            self.assertTrue(row["shared_terms"], "a surfaced row must say why it surfaced")

    def test_named_decisions_are_marked_not_dropped(self) -> None:
        """Naming a ruling is not reconciling with it.

        Dropping already-named decisions measurably worsened the real case, so
        the row stays and carries a flag instead.
        """
        body = AMENDMENT.replace("## Decision\n\n", "## Decision\n\nThis follows DEC-001. ")
        rows = decision_neighbors.neighbors(self.root, body)
        named = {row["id"]: row["named_in_body"] for row in rows}
        self.assertIn("DEC-001", named)
        self.assertTrue(named["DEC-001"])
        self.assertFalse(named.get("DEC-002", True))

    def test_exclude_id_drops_only_the_decision_being_written(self) -> None:
        self.scaffold.write("decisions/DEC-005.md", AMENDMENT)
        rows = decision_neighbors.neighbors(self.root, AMENDMENT, exclude_id="DEC-005")
        self.assertNotIn("DEC-005", [row["id"] for row in rows])

    def test_shared_identifiers_alone_never_make_a_neighbor(self) -> None:
        """Two decisions citing the same task are not thereby about one subject."""
        far = project_scaffold()
        self.addCleanup(far.cleanup)
        far.write("decisions/DEC-001.md", decision(
            "DEC-001: Roster rotation", "TASK-03-026 rotates the roster weekly.",
        ))
        body = decision(
            "DEC-002: Invoice cutoff", "TASK-03-026 sets the quarterly invoice cutoff.",
        )
        rows = decision_neighbors.neighbors(far.project_root, body)
        for row in rows:
            self.assertNotIn("task", " ".join(row["shared_terms"]))

    def test_first_decisions_of_a_project_still_rank(self) -> None:
        """Unsmoothed idf collapses to zero on a one-decision corpus.

        Every project passes through that state, so the smoothing is a
        correctness requirement rather than a refinement.
        """
        tiny = project_scaffold()
        self.addCleanup(tiny.cleanup)
        tiny.write("decisions/DEC-001.md", BOUNDARY)
        rows = decision_neighbors.neighbors(tiny.project_root, AMENDMENT)
        self.assertEqual([row["id"] for row in rows], ["DEC-001"])
        self.assertGreater(rows[0]["score"], 0.0)

    def test_titles_are_capped(self) -> None:
        long_title = "DEC-009: " + "boundary evidence " * 40
        self.scaffold.write("decisions/DEC-009.md", decision(long_title, "Contained guest escape is void."))
        rows = decision_neighbors.neighbors(self.root, AMENDMENT)
        for row in rows:
            self.assertLessEqual(len(row["title"]), decision_neighbors.TITLE_CAP)

    def test_ranking_is_deterministic(self) -> None:
        first = decision_neighbors.neighbors(self.root, AMENDMENT)
        second = decision_neighbors.neighbors(self.root, AMENDMENT)
        self.assertEqual(first, second)


class UnreferencedPairsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root

    def test_cross_referenced_pairs_are_already_considered(self) -> None:
        self.scaffold.write("decisions/DEC-001.md", BOUNDARY)
        self.scaffold.write("decisions/DEC-002.md", LIMITS)
        unreferenced = decision_neighbors.unreferenced_pairs(self.root)
        self.assertIn(["DEC-001", "DEC-002"], [pair["decisions"] for pair in unreferenced])

        linked = LIMITS.replace("## Decision\n\n", "## Decision\n\nUnder DEC-001. ")
        self.scaffold.write("decisions/DEC-002.md", linked)
        after = decision_neighbors.unreferenced_pairs(self.root)
        self.assertNotIn(["DEC-001", "DEC-002"], [pair["decisions"] for pair in after])

    def test_pair_list_is_capped_and_ordered(self) -> None:
        for index in range(1, 31):
            self.scaffold.write(
                f"decisions/DEC-{index:03d}.md",
                decision(
                    f"DEC-{index:03d}: Contained boundary ruling number {index}",
                    "Contained guest escape and the host-held witness boundary govern the run.",
                ),
            )
        pairs = decision_neighbors.unreferenced_pairs(self.root)
        self.assertLessEqual(len(pairs), decision_neighbors.REVIEW_PAIR_CAP)
        self.assertEqual([p["score"] for p in pairs], sorted((p["score"] for p in pairs), reverse=True))

    def test_single_decision_yields_no_pairs(self) -> None:
        self.scaffold.write("decisions/DEC-001.md", BOUNDARY)
        self.assertEqual(decision_neighbors.unreferenced_pairs(self.root), [])


class WriteDecisionSurfaceTest(unittest.TestCase):
    """The rows reach the PM through the record write-decision already emits."""

    def setUp(self) -> None:
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.scaffold.write("decisions/DEC-001.md", BOUNDARY)
        self.scaffold.write("decisions/DEC-002.md", LIMITS)
        self.scaffold.write("decisions/DEC-003.md", ROSTER)

    def _write(self, dec_id: str, body: str) -> tuple[int, dict]:
        source = self.scaffold.root / f"{dec_id}-body.md"
        source.write_text(body, encoding="utf-8")
        args = argparse.Namespace(
            project_root=str(self.root), dec_id=dec_id,
            title="Demonstration scope amended", date="2026-09-01",
            status="locked", supersedes="none",
            content=None, content_file=str(source),
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = write_decision.handler(args)
        self.assertEqual(code, 0, err.getvalue())
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def test_record_carries_the_neighbors(self) -> None:
        _, record = self._write("DEC-005", AMENDMENT)
        rows = record["details"]["neighbors"]
        surfaced = [row["id"] for row in rows]
        self.assertIn("DEC-001", surfaced)
        self.assertIn("DEC-002", surfaced)
        self.assertNotIn("DEC-005", surfaced, "a decision is never its own neighbor")

    def test_surface_is_bounded_in_bytes(self) -> None:
        _, record = self._write("DEC-005", AMENDMENT)
        payload = json.dumps(record["details"]["neighbors"])
        self.assertLess(len(payload.encode("utf-8")), 1200)

    def test_a_decision_this_ruling_supersedes_is_not_offered_to_reconcile(self) -> None:
        """Superseding DEC-001 retires it, so it is not a thing left to reconcile.

        Liveness is read from the decision body's ``Supersedes:`` header, which
        is the authority `trace_binding` already uses. The fixture therefore
        sets the header, not only the index flag.
        """
        body = AMENDMENT.replace("Supersedes: none", "Supersedes: DEC-001")
        source = self.scaffold.root / "body.md"
        source.write_text(body, encoding="utf-8")
        args = argparse.Namespace(
            project_root=str(self.root), dec_id="DEC-005",
            title="Demonstration scope amended", date="2026-09-01",
            status="locked", supersedes="DEC-001",
            content=None, content_file=str(source),
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(write_decision.handler(args), 0, err.getvalue())
        record = json.loads(out.getvalue().strip().splitlines()[-1])
        surfaced = [row["id"] for row in record["details"]["neighbors"]]
        self.assertNotIn("DEC-001", surfaced)
        self.assertIn("DEC-002", surfaced, "the constraint it did not retire still stands")

    def test_liveness_follows_the_body_header_not_the_index_flag(self) -> None:
        """Pins an existing seam this surface inherits rather than introduces.

        ``write-decision --supersedes`` populates the INDEX.md row; the body's
        own ``Supersedes:`` header is what every liveness reader consults. When
        a caller sets the flag alone the two disagree, and the named decision
        stays live here exactly as it stays live for `trace_binding`. Changing
        that is a write-decision guard question, not a proximity question.
        """
        source = self.scaffold.root / "flag-only.md"
        source.write_text(AMENDMENT, encoding="utf-8")
        args = argparse.Namespace(
            project_root=str(self.root), dec_id="DEC-005",
            title="Demonstration scope amended", date="2026-09-01",
            status="locked", supersedes="DEC-001",
            content=None, content_file=str(source),
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(write_decision.handler(args), 0, err.getvalue())
        record = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertIn("DEC-001", [row["id"] for row in record["details"]["neighbors"]])
        self.assertIn("DEC-001", decision_neighbors.corpus(self.root))

    def test_ranking_failure_never_costs_the_caller_the_decision(self) -> None:
        """The decision is on disk before ranking runs. It stays there."""
        original = decision_neighbors.neighbors

        def explode(*args, **kwargs):
            raise RuntimeError("ranking is broken")

        decision_neighbors.neighbors = explode
        self.addCleanup(lambda: setattr(decision_neighbors, "neighbors", original))
        _, record = self._write("DEC-006", AMENDMENT)
        self.assertEqual(record["details"]["neighbors"], [])
        self.assertTrue((self.root / "decisions" / "DEC-006.md").is_file())


class ReviewProjectionTest(SeamFixture):
    """Only the planning reviewer receives the pair list."""

    def seed_decisions(self) -> None:
        (self.root / "decisions/DEC-001.md").write_text(BOUNDARY, encoding="utf-8")
        (self.root / "decisions/DEC-002.md").write_text(LIMITS, encoding="utf-8")
        (self.root / "decisions/DEC-003.md").write_text(ROSTER, encoding="utf-8")

    def test_task_closure_review_receives_no_decision_set(self) -> None:
        self.seed_decisions()
        code, records, err = self.run_cli(
            "review-context", str(self.root), "--review-kind", "task-closure",
            "--task", str(self.task),
        )
        self.assertEqual(code, 0, err)
        self.assertIsNone(records[0]["decision_proximity"])

    def test_planning_review_receives_the_bounded_pair_list(self) -> None:
        self.seed_decisions()
        self.capture_planning("Plan the demonstration phase.")
        code, records, err = self.run_cli(
            "review-context", str(self.root), "--review-kind", "planning",
            "--checkpoint", "PLAN-001",
        )
        self.assertEqual(code, 0, err)
        projection = records[0]["decision_proximity"]
        self.assertEqual(projection["cap"], decision_neighbors.REVIEW_PAIR_CAP)
        self.assertEqual(projection["budget"], decision_neighbors.NEIGHBOR_BUDGET)
        self.assertIn(
            ["DEC-001", "DEC-002"], [pair["decisions"] for pair in projection["pairs"]]
        )
        self.assertLessEqual(
            len(json.dumps(projection).encode("utf-8")),
            9591,
            "the declared worst case in config-surfaces.json must hold",
        )

    def capture_planning(self, text: str) -> None:
        import argparse as _argparse
        import os as _os
        from unittest import mock as _mock
        from cli.commands import capture_request as _capture

        source = self.root / "planning-request.txt"
        source.write_text(text, encoding="utf-8")
        args = _argparse.Namespace(
            project_root=str(self.root), request_id="REQUEST-002",
            unit="planning:PLAN-001", content_file=str(source),
            correction_of=None, captured_at="2026-07-27T12:00:00Z",
        )
        env = {
            key: value for key, value in _os.environ.items()
            if key not in _capture.NON_OPERATOR_MARKERS
        }
        with (
            _mock.patch.dict(_os.environ, env, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(_capture.handler(args), 0)


if __name__ == "__main__":
    unittest.main()
