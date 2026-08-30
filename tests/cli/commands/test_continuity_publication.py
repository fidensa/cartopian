"""Commit-aware publication, at the command level.

The primitive's classification is covered in ``tests.cli.test_continuity``.
What this module asserts is what the *commands* built on it do to the
reservation, the index, the evidence window, and the artifact — including the
one rule the accepted design states once and in one form: a `published` result
whose residue collapsed is a successful pass, and
``continuity-publication-cleanup-failed`` names exactly one condition.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from cli import atomic_write, continuity as cy, prompt_evidence
from tests.cli.test_continuity import _Injector
from tests.continuity_support import (
    archive,
    continuity_scaffold,
    decision,
    release,
    run_cli,
    seed_evidence,
    write_continuity,
)


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.artifact = self.root / "CONTINUITY.md"
        decision(self.scaffold, "DEC-014", scope="intake",
                 ruling="Every intake record names its requester.")
        seed_evidence(self.scaffold, "PLAN-001")
        self.reservation = self.root / "archive" / "PLAN-001"

    def details(self, records):
        self.assertTrue(records, "no record emitted")
        return records[0]["details"]

    def inject(self, boundary, *, plan=None):
        """Run one write with ``boundary`` failing at the *artifact* publication.

        Scoped to the `continuity` destination so the reservation's own
        publications, which run at step 4, are untouched.
        """
        from cli.commands import write_continuity as command

        injector = _Injector(boundary)
        real_write = command.mediated_write

        def scoped(root, dest_kind, target, content, **kwargs):
            if dest_kind != "continuity":
                return real_write(root, dest_kind, target, content, **kwargs)
            real_os = atomic_write.os
            atomic_write.os = injector
            try:
                return real_write(root, dest_kind, target, content, **kwargs)
            finally:
                atomic_write.os = real_os

        with mock.patch.object(command, "mediated_write", scoped):
            return write_continuity(self.scaffold, "ledger", "2026-08-14", plan=plan)


def _block_residue_collapse():
    """Make every ``.cartmp.`` removal fail, leaving a hardlinked sibling."""
    real = cy.mediated_unlink

    def blocked(project_root, path, *, directory=False):
        if ".cartmp." in os.fspath(path):
            raise OSError(1, "injected: collapse blocked")
        return real(project_root, path, directory=directory)

    return mock.patch.object(cy, "mediated_unlink", blocked)


class TestArtifactPostCommitCleanup(_Fixture):
    """One outcome, stated once: a collapsed residue is a successful pass."""

    def test_a_collapsed_residue_exits_zero_and_still_closes_the_window(self):
        code, records, err = self.inject("post-link-unlink")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["publication"], "published")
        self.assertTrue(detail["publication_residue_collapsed"])
        self.assertEqual(self.artifact.stat().st_nlink, 1)
        # Step 10 ran: the evidence close is reached on a successful pass.
        self.assertTrue(detail["effectiveness_closeout"]["log_deleted"])
        # No marker: the publication committed.
        self.assertFalse((self.reservation / cy.LEDGER_FAILED_BASENAME).exists())
        self.assertEqual(
            cy.classify_reservation(self.root, "PLAN-001").shape, cy.SHAPE_UNMARKED
        )

    def test_a_surviving_residue_refuses_by_name_and_still_writes_no_marker(self):
        with _block_residue_collapse():
            code, records, err = self.inject("post-link-unlink")
        self.assertEqual(code, 1)
        self.assertIn("continuity-publication-cleanup-failed", err)
        self.assertEqual(records, [])
        # The plan *is* recorded, so nothing may be released.
        self.assertEqual(self.artifact.stat().st_nlink, 2)
        self.assertFalse((self.reservation / cy.LEDGER_FAILED_BASENAME).exists())
        self.assertEqual(
            cy.classify_reservation(self.root, "PLAN-001").shape, cy.SHAPE_UNMARKED
        )
        code, _, err = release(self.scaffold, "PLAN-001")
        self.assertEqual(code, 1)
        self.assertIn("continuity-reservation-unresolved", err)

    def test_the_pre_read_collapse_makes_that_state_recoverable(self):
        with _block_residue_collapse():
            self.inject("post-link-unlink")
        self.assertEqual(self.artifact.stat().st_nlink, 2)
        # Both guards refuse while the residue survives.
        with self.assertRaises(cy.ContinuityRefusal) as caught:
            cy.read_artifact(self.root)
        self.assertEqual(caught.exception.rule, "continuity-unsafe-artifact")
        # Re-running the same command collapses it before the read and succeeds.
        code, records, err = write_continuity(
            self.scaffold, "ledger", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertTrue(detail["already_written"])
        self.assertTrue(detail["publication_residue_collapsed"])
        self.assertEqual(self.artifact.stat().st_nlink, 1)

    def test_a_still_blocked_collapse_repeats_the_same_refusal(self):
        with _block_residue_collapse():
            self.inject("post-link-unlink")
            code, _, err = self.inject(None, plan="PLAN-001")
        self.assertEqual(code, 1)
        self.assertIn("continuity-publication-cleanup-failed", err)
        self.assertNotIn("continuity-unsafe-artifact", err)
        self.assertNotIn("hardlink", err)

    def test_the_old_rule_would_have_mis_marked_a_recorded_plan(self):
        # Regression guard: the pre-commit boundaries mark, the post-commit one
        # does not — and a rule keyed on "a refusal was raised" cannot tell them
        # apart, because both raise.
        code, _, err = self.inject("link")
        self.assertEqual(code, 1)
        self.assertTrue((self.reservation / cy.LEDGER_FAILED_BASENAME).is_file())
        self.assertFalse(self.artifact.exists())

        self.setUp()
        code, records, err = self.inject("post-link-unlink")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.reservation / cy.LEDGER_FAILED_BASENAME).exists())
        self.assertTrue(self.artifact.is_file())


class TestArtifactPreCommitBoundaries(_Fixture):
    def test_every_pre_commit_boundary_marks_and_leaves_the_artifact_absent(self):
        for boundary in ("tmp-open", "tmp-write", "tmp-fsync", "link", "link-eexist"):
            with self.subTest(boundary=boundary):
                self.setUp()
                log_before = prompt_evidence.log_path(self.root).read_bytes()
                code, records, err = self.inject(boundary)
                self.assertEqual(code, 1)
                self.assertEqual(records, [])
                self.assertFalse(self.artifact.exists())
                marker = self.reservation / cy.LEDGER_FAILED_BASENAME
                self.assertTrue(marker.is_file())
                self.assertEqual(len(marker.read_bytes()), 334)
                self.assertIn("PLAN-001", marker.read_text(encoding="utf-8"))
                # The evidence close is unreachable, so the log survives.
                self.assertEqual(
                    prompt_evidence.log_path(self.root).read_bytes(), log_before
                )
                # The marked reservation is releasable.
                self.assertTrue(
                    cy.classify_reservation(self.root, "PLAN-001").releasable
                )

    def test_a_swallowed_directory_fsync_is_an_ordinary_success(self):
        code, records, err = self.inject("dir-fsync")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.details(records)["publication"], "published")
        self.assertFalse((self.reservation / cy.LEDGER_FAILED_BASENAME).exists())


class TestReservationPublicationBoundaries(_Fixture):
    """The two reservation files publish through the same commit-aware step."""

    def _inject_at_sentinel(self, boundary):
        """Fail one boundary of the *sentinel's* publication only."""
        injector = _Injector(boundary)
        real = atomic_write.os
        original = cy._atomic_publish_file

        def guarded(real_root, directory, name, data, *, mode=0o644):
            if name == cy.NOT_ARCHIVED_BASENAME:
                atomic_write.os = injector
                try:
                    return original(real_root, directory, name, data, mode=mode)
                finally:
                    atomic_write.os = real
            return original(real_root, directory, name, data, mode=mode)

        with mock.patch.object(cy, "_atomic_publish_file", guarded):
            return write_continuity(self.scaffold, "ledger", "2026-08-14")

    def test_a_pre_commit_sentinel_failure_leaves_an_empty_releasable_reservation(self):
        for boundary in ("tmp-open", "tmp-write", "tmp-fsync", "link", "link-eexist"):
            with self.subTest(boundary=boundary):
                self.setUp()
                code, _, err = self._inject_at_sentinel(boundary)
                self.assertEqual(code, 1)
                self.assertIn("continuity-reservation-write-failed", err)
                state = cy.classify_reservation(self.root, "PLAN-001")
                self.assertEqual(state.shape, cy.SHAPE_EMPTY)
                self.assertTrue(state.releasable)
                self.assertFalse(cy.has_reservation_row(self.root, "PLAN-001"))
                # No marker: a step-4 failure leaves a self-proving prefix.
                self.assertFalse((self.reservation / cy.LEDGER_FAILED_BASENAME).exists())
                self.assertFalse(self.artifact.exists())

    def test_a_post_commit_sentinel_cleanup_failure_is_collapsed_by_the_next_pass(self):
        with _block_residue_collapse():
            code, _, err = self._inject_at_sentinel("post-link-unlink")
        self.assertEqual(code, 1)
        self.assertIn("continuity-reservation-cleanup-failed", err)
        sentinel = self.reservation / cy.NOT_ARCHIVED_BASENAME
        self.assertEqual(sentinel.stat().st_nlink, 2)
        self.assertEqual(len(sentinel.read_bytes()), 245)
        # The next pass collapses the residue first and completes.
        code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertIn(cy.REPAIR_RESIDUE, detail["reservation_repaired"])
        self.assertEqual(sentinel.stat().st_nlink, 1)

    def test_a_marker_whose_publication_fails_leaves_an_unmarked_reservation(self):
        decision(self.scaffold, "DEC-002", scope="a", ruling="r",
                 supersedes="PLAN-009/DEC-001")

        def failing_marker(project_root, plan_id):
            return "write-failed"

        with mock.patch.object(cy, "write_failure_marker", failing_marker):
            code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 1)
        self.assertIn("continuity-reference-unresolved", err)
        self.assertIn("marker: write-failed", err)
        self.assertEqual(
            cy.classify_reservation(self.root, "PLAN-001").shape, cy.SHAPE_UNMARKED
        )
        code, _, err = release(self.scaffold, "PLAN-001")
        self.assertEqual(code, 1)
        self.assertIn("continuity-reservation-unresolved", err)


class TestIndexPublication(_Fixture):
    """`archive/INDEX.md` commits with `os.replace` and can leave no residue."""

    def _strays(self, index):
        return [
            name
            for name in os.listdir(index.parent)
            if name.startswith(".INDEX.md.tmp-")
        ]

    def test_a_create_leaves_no_stray_and_one_link(self):
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 0, err)
        index = self.root / "archive" / "INDEX.md"
        self.assertEqual(index.stat().st_nlink, 1)
        self.assertEqual(self._strays(index), [])
        self.assertIn(
            cy.reservation_row("PLAN-001", "2026-08-14"),
            index.read_text(encoding="utf-8"),
        )

    def test_a_rewrite_leaves_no_stray_and_one_link(self):
        # A *marked* reservation is the releasable state, so the row drop —
        # the index rewrite — is reachable.
        reservation = self.root / "archive" / "PLAN-001"
        reservation.mkdir(parents=True)
        for name, body in (
            (cy.NOT_ARCHIVED_BASENAME, cy.not_archived_body("PLAN-001")),
            (cy.LEDGER_FAILED_BASENAME, cy.ledger_failed_body("PLAN-001")),
        ):
            (reservation / name).write_text(body, encoding="utf-8")
        cy.ensure_reservation_row(self.root, "PLAN-001", "2026-08-14")
        index = self.root / "archive" / "INDEX.md"
        code, _, err = release(self.scaffold, "PLAN-001")
        self.assertEqual(code, 0, err)
        self.assertEqual(index.stat().st_nlink, 1)
        self.assertEqual(self._strays(index), [])
        self.assertNotIn(cy.RESERVATION_SUMMARY, index.read_text(encoding="utf-8"))

    def test_an_index_left_hardlinked_would_break_archival_itself(self):
        # Why `_atomic_text`'s replace-only commit is load-bearing rather than
        # incidental: `_existing_archives` refuses at st_nlink > 1.
        archive(self.scaffold, "2026-08-14")
        index = self.root / "archive" / "INDEX.md"
        os.link(index, index.parent / "INDEX.md.alias")
        code, _, err = run_cli(
            "archive-plan", str(self.root), "--closed", "2026-08-19",
            "--summary", "s", "--content", "# c\n",
        )
        self.assertEqual(code, 1)
        self.assertIn("archive-index", err)
        self.assertIn("hardlinked file is not allowed", err)

    def test_a_stray_index_temp_is_invisible_to_the_allocator_and_the_shape_test(self):
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 0, err)
        (self.root / "archive" / ".INDEX.md.tmp-abcdef").write_text("x", encoding="utf-8")
        self.assertEqual(cy.existing_archive_numbers(self.root), [1])
        self.assertEqual(prompt_evidence.current_plan_id(self.root), "PLAN-002")
        self.assertEqual(
            cy.classify_reservation(self.root, "PLAN-001").shape, cy.SHAPE_UNMARKED
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
