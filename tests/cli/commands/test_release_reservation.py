"""Coverage for `cartopian release-reservation` — the bounded abandonment path.

Two claims carry this command, and both are asserted as measured negatives:
it never opens `CONTINUITY.md` on any path, and it releases only a reservation
whose non-commit is *proven* — by its failure marker, or by the missing
`archive/INDEX.md` reservation row a creation that never reached publication
leaves behind.
"""
from __future__ import annotations

import builtins
import io
import os
import unittest
from contextlib import contextmanager
from unittest import mock

from cli import continuity as cy, prompt_evidence, provenance
from tests.cli.test_continuity import SPECIMEN
from tests.continuity_support import (
    archive,
    continuity_scaffold,
    decision,
    release,
    run_cli,
    seed_evidence,
    write_continuity,
)

ARTIFACT_STATES = ("absent", "valid", "symlink", "non-utf8", "format-unrecognized")


@contextmanager
def path_trace():
    """Record every path the interpreter is asked to open, stat, or test.

    A positive control asserts the trace catches a deliberate read, so every
    "opens no CONTINUITY.md" assertion below is a measured negative rather than
    an absence of evidence.
    """
    seen = []
    real = {
        "open": builtins.open,
        "io_open": io.open,
        "os_open": os.open,
        "stat": os.stat,
        "lstat": os.lstat,
        "lexists": os.path.lexists,
        "exists": os.path.exists,
        "listdir": os.listdir,
    }

    def record(path):
        try:
            seen.append(os.fspath(path))
        except TypeError:
            pass
        return path

    def wrap(name, func, index=0):
        def wrapper(*args, **kwargs):
            if args:
                record(args[index])
            return func(*args, **kwargs)

        return wrapper

    with (
        mock.patch.object(builtins, "open", wrap("open", real["open"])),
        mock.patch.object(io, "open", wrap("io.open", real["io_open"])),
        mock.patch.object(os, "open", wrap("os.open", real["os_open"])),
        mock.patch.object(os, "stat", wrap("os.stat", real["stat"])),
        mock.patch.object(os, "lstat", wrap("os.lstat", real["lstat"])),
        mock.patch.object(os.path, "lexists", wrap("lexists", real["lexists"])),
        mock.patch.object(os.path, "exists", wrap("exists", real["exists"])),
        mock.patch.object(os, "listdir", wrap("listdir", real["listdir"])),
    ):
        yield seen


class _Fixture(unittest.TestCase):
    """A project holding a marked `PLAN-002` reservation and one `PLAN-002` record.

    The base state mirrors the accepted design's own fixture: `archive/PLAN-001`
    is a real archive, `archive/INDEX.md` carries its row, and the window in
    flight is `PLAN-002`, so the witness names it.
    """

    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.artifact = self.root / "CONTINUITY.md"
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        archive(self.scaffold, "2026-08-14")
        self.reservation = self.root / "archive" / "PLAN-002"
        seed_evidence(self.scaffold, "PLAN-002")

    def make(self, shape, *, closed="2026-08-26"):
        self.reservation.mkdir(parents=True, exist_ok=True)
        if shape in (cy.SHAPE_MARKED, cy.SHAPE_UNMARKED, cy.SHAPE_INCOMPLETE):
            (self.reservation / cy.NOT_ARCHIVED_BASENAME).write_text(
                cy.not_archived_body("PLAN-002"), encoding="utf-8"
            )
        if shape in (cy.SHAPE_MARKED, cy.SHAPE_MARKER_ONLY):
            (self.reservation / cy.LEDGER_FAILED_BASENAME).write_text(
                cy.ledger_failed_body("PLAN-002"), encoding="utf-8"
            )
        if shape != cy.SHAPE_INCOMPLETE:
            cy.ensure_reservation_row(self.root, "PLAN-002", closed)
        self.assertEqual(cy.classify_reservation(self.root, "PLAN-002").shape, shape)

    def set_artifact(self, state):
        if state == "absent":
            return None
        if state == "valid":
            self.artifact.write_text(SPECIMEN, encoding="utf-8")
        elif state == "symlink":
            target = self.root / "elsewhere.md"
            target.write_text("x", encoding="utf-8")
            os.symlink(target, self.artifact)
            return None
        elif state == "non-utf8":
            self.artifact.write_bytes(b"# Continuity\n\n\xff\xfe\n")
        elif state == "format-unrecognized":
            self.artifact.write_text("# Continuity\n\nFormat: v9\n", encoding="utf-8")
        return self.artifact.read_bytes()

    def details(self, records):
        self.assertTrue(records, "no record emitted")
        return records[0]["details"]

    def release_prefix(self, stop_after):
        """Perform the first ``stop_after + 1`` release mutations by hand.

        The command's own order: sentinel, marker, directory, index row — so
        the proof of non-commit survives to the last file removed.
        """
        steps = [
            lambda: cy.mediated_unlink(
                self.root, self.reservation / cy.NOT_ARCHIVED_BASENAME
            ),
            lambda: cy.mediated_unlink(
                self.root, self.reservation / cy.LEDGER_FAILED_BASENAME
            ),
            lambda: cy.mediated_unlink(self.root, self.reservation, directory=True),
            lambda: cy.drop_reservation_row(self.root, "PLAN-002"),
        ]
        for step in steps[: stop_after + 1]:
            step()

    def mediated_deletes(self, fragment):
        journal = self.root / provenance.LOG_RELPATH
        if not journal.is_file():
            return 0
        return sum(
            1
            for line in journal.read_text(encoding="utf-8").splitlines()
            if '"action":"mediated-delete"' in line.replace(" ", "") and fragment in line
        )


class TestTraceControl(_Fixture):
    def test_the_trace_catches_a_deliberate_artifact_read(self):
        # The control is the read the release must never perform: if
        # `release-reservation` opened the artifact the way `read_artifact`
        # does, the trace below would name it.
        self.artifact.write_text(SPECIMEN, encoding="utf-8")
        with path_trace() as seen:
            cy.read_artifact(self.root)
        self.assertTrue(any(str(path).endswith("CONTINUITY.md") for path in seen))
        with path_trace() as seen:
            self.artifact.read_text(encoding="utf-8")
        self.assertTrue(any(str(path).endswith("CONTINUITY.md") for path in seen))


class TestDiscriminator(_Fixture):
    """The `archive/INDEX.md` row is what separates `incomplete` from `unmarked`."""

    def test_the_row_absent_copy_releases_and_the_row_present_copy_refuses(self):
        for state in ARTIFACT_STATES:
            with self.subTest(artifact=state, shape="incomplete"):
                self.setUp()
                self.make(cy.SHAPE_INCOMPLETE)
                before = self.set_artifact(state)
                with path_trace() as seen:
                    code, records, err = release(self.scaffold, "PLAN-002")
                self.assertEqual(code, 0, err)
                detail = self.details(records)
                self.assertEqual(detail["reservation_shape"], "incomplete")
                self.assertEqual(detail["removed"], ["NOT-ARCHIVED.md", "directory"])
                self.assertFalse(detail["already_released"])
                self.assertFalse(self.reservation.exists())
                self.assertFalse(any(str(p).endswith("CONTINUITY.md") for p in seen))
                if before is not None:
                    self.assertEqual(self.artifact.read_bytes(), before)

            with self.subTest(artifact=state, shape="unmarked"):
                self.setUp()
                self.make(cy.SHAPE_UNMARKED)
                before = self.set_artifact(state)
                with path_trace() as seen:
                    code, records, err = release(self.scaffold, "PLAN-002")
                self.assertEqual(code, 1)
                self.assertIn("continuity-reservation-unresolved", err)
                self.assertIn("write-continuity --mode ledger --plan PLAN-002", err)
                self.assertEqual(records, [])
                self.assertTrue(
                    (self.reservation / cy.NOT_ARCHIVED_BASENAME).is_file()
                )
                self.assertTrue(cy.has_reservation_row(self.root, "PLAN-002"))
                self.assertFalse(any(str(p).endswith("CONTINUITY.md") for p in seen))
                if before is not None:
                    self.assertEqual(self.artifact.read_bytes(), before)

    def test_an_adoption_repairs_the_row_alone_on_the_incomplete_copy(self):
        self.make(cy.SHAPE_INCOMPLETE)
        decision(self.scaffold, "DEC-014", scope="intake", ruling="Intake names a requester.")
        code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-26")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plan"], "PLAN-002")
        self.assertEqual(detail["plan_source"], "reservation-adopted")
        self.assertEqual(detail["reservation_repaired"], ["archive/INDEX.md row"])

    def test_no_release_prefix_can_manufacture_an_incomplete_from_an_unmarked(self):
        # The sentinel is removed first and the row last, so every prefix of a
        # release from a marked reservation lands on a shape that is not
        # `unmarked` — a release can never produce the discriminator it reads.
        self.make(cy.SHAPE_MARKED)
        observed = []
        for stop_after in range(4):
            self.setUp()
            self.make(cy.SHAPE_MARKED)
            self.release_prefix(stop_after)
            observed.append(cy.classify_reservation(self.root, "PLAN-002").shape)
        self.assertNotIn(cy.SHAPE_UNMARKED, observed)
        self.assertEqual(
            observed,
            [cy.SHAPE_MARKER_ONLY, cy.SHAPE_EMPTY, cy.SHAPE_ABSENT, cy.SHAPE_ABSENT],
        )


class TestReleaseStateMachine(_Fixture):
    def test_a_marked_reservation_releases_and_closes_its_own_window(self):
        self.make(cy.SHAPE_MARKED)
        self.artifact.write_text(SPECIMEN, encoding="utf-8")
        before = self.artifact.read_bytes()
        code, records, err = release(self.scaffold, "PLAN-002")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["reservation_shape"], "marked")
        self.assertEqual(
            detail["removed"],
            ["NOT-ARCHIVED.md", "LEDGER-FAILED.md", "directory", "archive/INDEX.md row"],
        )
        self.assertEqual(detail["window_witness"], "PLAN-002")
        closeout = detail["effectiveness_closeout"]
        self.assertEqual(closeout["plan"], "PLAN-002")
        self.assertEqual(closeout["ledger_errors"], [])
        self.assertTrue(closeout["log_deleted"])
        self.assertEqual(closeout["closing_projection"]["matched"], 1)
        self.assertFalse(self.reservation.exists())
        self.assertFalse(cy.has_reservation_row(self.root, "PLAN-002"))
        self.assertEqual(self.artifact.read_bytes(), before)

    def test_the_closing_plan_keeps_its_own_id_afterwards(self):
        self.make(cy.SHAPE_MARKED)
        self.assertEqual(prompt_evidence.current_plan_id(self.root), "PLAN-003")
        release(self.scaffold, "PLAN-002")
        self.assertEqual(prompt_evidence.current_plan_id(self.root), "PLAN-002")
        self.assertEqual(archive(self.scaffold, "2026-08-26"), "PLAN-002")
        index = (self.root / "archive/INDEX.md").read_text(encoding="utf-8")
        self.assertNotIn(cy.RESERVATION_SUMMARY, index)
        self.assertIn("| `PLAN-002` | 2026-08-26 | fixture |", index)
        self.assertTrue((self.root / "archive/PLAN-002/CLOSEOUT.md").is_file())

    def test_without_the_release_the_closing_plan_loses_its_id_and_its_evidence(self):
        # The measured negative, asserted *not* to be the shipped sequence.
        self.make(cy.SHAPE_MARKED)
        code, records, err = run_cli(
            "archive-plan", str(self.root), "--closed", "2026-08-26",
            "--summary", "s", "--content", "# c\n",
        )
        self.assertEqual(code, 0, err)
        detail = records[0]["details"]
        self.assertEqual(detail["archive_name"], "PLAN-003")
        closeout = detail["effectiveness_closeout"]
        self.assertEqual(closeout["plan"], "PLAN-003")
        self.assertEqual(
            [entry["rule"] for entry in closeout["ledger_errors"]],
            ["foreign-plan-window"],
        )
        self.assertEqual(closeout["closing_projection"]["matched"], 0)
        self.assertEqual(
            closeout["closing_projection"]["unavailable_units"], ["TASK-02-001"]
        )
        self.assertTrue(closeout["log_deleted"])

    def test_the_release_completes_from_every_prefix_as_one_logical_release(self):
        for stop_after in range(4):
            with self.subTest(prefix=stop_after):
                self.setUp()
                self.make(cy.SHAPE_MARKED)
                self.release_prefix(stop_after)
                code, records, err = release(self.scaffold, "PLAN-002")
                self.assertEqual(code, 0, err)
                detail = self.details(records)
                self.assertFalse(self.reservation.exists())
                self.assertFalse(cy.has_reservation_row(self.root, "PLAN-002"))
                closeout = detail["effectiveness_closeout"]
                self.assertEqual(closeout["plan"], "PLAN-002")
                self.assertEqual(closeout.get("ledger_errors", []), [])
                # Exactly one mediated delete per removed path across the whole
                # interrupted-plus-resumed sequence.
                self.assertEqual(self.mediated_deletes("archive/PLAN-002"), 3)

    def test_an_incomplete_release_records_two_deletes_across_its_prefixes(self):
        for stop_after in range(2):
            with self.subTest(prefix=stop_after):
                self.setUp()
                self.make(cy.SHAPE_INCOMPLETE)
                if stop_after >= 0:
                    cy.mediated_unlink(
                        self.root, self.reservation / cy.NOT_ARCHIVED_BASENAME
                    )
                if stop_after >= 1:
                    cy.mediated_unlink(self.root, self.reservation, directory=True)
                code, records, err = release(self.scaffold, "PLAN-002")
                self.assertEqual(code, 0, err)
                self.assertFalse(self.reservation.exists())
                self.assertEqual(self.mediated_deletes("archive/PLAN-002"), 2)

    def test_a_second_release_reports_already_released_and_removes_nothing(self):
        self.make(cy.SHAPE_MARKED)
        release(self.scaffold, "PLAN-002")
        code, records, err = release(self.scaffold, "PLAN-002")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertTrue(detail["already_released"])
        self.assertEqual(detail["removed"], [])
        self.assertEqual(detail["reservation_shape"], "absent")
        self.assertTrue(detail["effectiveness_closeout"]["already_closed"])

    def test_a_stale_row_under_a_real_archive_is_reconciled_and_the_archive_kept(self):
        # PLAN-001's window closed with its archive; the log now carries the
        # window in flight, so it is removed to leave the witness silent.
        prompt_evidence.log_path(self.root).unlink()
        cy.ensure_reservation_row(self.root, "PLAN-001", "2026-08-14")
        code, records, err = release(self.scaffold, "PLAN-001")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["reservation_shape"], "archive")
        self.assertEqual(detail["removed"], ["archive/INDEX.md row"])
        self.assertEqual(detail["index_rows_kept"], ["| `PLAN-001` | 2026-08-14 | fixture |"])
        self.assertTrue((self.root / "archive/PLAN-001/CLOSEOUT.md").is_file())
        self.assertIn(
            "| `PLAN-001` | 2026-08-14 | fixture |",
            (self.root / "archive/INDEX.md").read_text(encoding="utf-8"),
        )

    def test_a_real_archives_own_entries_are_never_removed(self):
        # § 11.6 step 1: a real archive has no directory to remove and the
        # command proceeds to the index and window steps. Even a sentinel left
        # inside one out of band is an archive's entry, not a reservation's, and
        # the release removes nothing under it.
        prompt_evidence.log_path(self.root).unlink()
        stray = self.root / "archive/PLAN-001" / cy.NOT_ARCHIVED_BASENAME
        stray.write_text(cy.not_archived_body("PLAN-001"), encoding="utf-8")
        cy.ensure_reservation_row(self.root, "PLAN-001", "2026-08-14")
        code, records, err = release(self.scaffold, "PLAN-001")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["reservation_shape"], "archive")
        self.assertEqual(detail["removed"], ["archive/INDEX.md row"])
        self.assertTrue(stray.is_file())
        self.assertTrue((self.root / "archive/PLAN-001/CLOSEOUT.md").is_file())

    def test_publication_residue_is_collapsed_rather_than_called_malformed(self):
        self.make(cy.SHAPE_MARKED)
        sentinel = self.reservation / cy.NOT_ARCHIVED_BASENAME
        os.link(sentinel, self.reservation / f"{cy.NOT_ARCHIVED_BASENAME}.cartmp.1.abc")
        code, records, err = release(self.scaffold, "PLAN-002")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["reservation_shape"], "marked")
        self.assertEqual(len(detail["residue_collapsed"]), 1)
        self.assertIn("(residue)", detail["residue_collapsed"][0])
        self.assertFalse(self.reservation.exists())


class TestReleaseRefusals(_Fixture):
    def test_a_marker_naming_another_plan_refuses(self):
        self.make(cy.SHAPE_MARKED)
        (self.reservation / cy.LEDGER_FAILED_BASENAME).write_text(
            cy.ledger_failed_body("PLAN-007"), encoding="utf-8"
        )
        code, records, err = release(self.scaffold, "PLAN-002")
        self.assertEqual(code, 1)
        self.assertIn("continuity-reservation-marker-mismatch", err)
        self.assertTrue((self.reservation / cy.NOT_ARCHIVED_BASENAME).is_file())

    def test_a_non_highest_entry_refuses(self):
        self.make(cy.SHAPE_MARKED)
        (self.root / "archive" / "PLAN-003").mkdir()
        code, _, err = release(self.scaffold, "PLAN-002")
        self.assertEqual(code, 1)
        self.assertIn("continuity-reservation-not-current", err)
        self.assertTrue((self.reservation / cy.NOT_ARCHIVED_BASENAME).is_file())

    def test_a_log_carrying_another_window_refuses_and_leaves_it_intact(self):
        self.make(cy.SHAPE_MARKED)
        seed_evidence(self.scaffold, "PLAN-003", unit="TASK-03-001")
        log_before = prompt_evidence.log_path(self.root).read_bytes()
        code, _, err = release(self.scaffold, "PLAN-002")
        self.assertEqual(code, 1)
        self.assertIn("continuity-reservation-window-foreign", err)
        self.assertEqual(prompt_evidence.log_path(self.root).read_bytes(), log_before)
        self.assertTrue((self.reservation / cy.NOT_ARCHIVED_BASENAME).is_file())

    def test_a_stray_entry_refuses_malformed(self):
        self.make(cy.SHAPE_MARKED)
        (self.reservation / "STRAY.md").write_text("x", encoding="utf-8")
        code, _, err = release(self.scaffold, "PLAN-002")
        self.assertEqual(code, 1)
        self.assertIn("continuity-reservation-malformed", err)

    def test_the_unmarked_state_is_resolved_only_through_the_enabled_path(self):
        self.make(cy.SHAPE_UNMARKED)
        code, _, err = release(self.scaffold, "PLAN-002")
        self.assertEqual(code, 1)
        self.assertIn("continuity-reservation-unresolved", err)
        # The remedy the refusal names resolves it, and then there is nothing
        # left to release.
        decision(self.scaffold, "DEC-014", scope="intake", ruling="Intake names a requester.")
        code, records, err = write_continuity(
            self.scaffold, "ledger", "2026-08-26", plan="PLAN-002"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.details(records)["plan"], "PLAN-002")
        self.assertEqual(cy.read_artifact(self.root).highest, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
