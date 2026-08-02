"""Prospective plan/task suffix-alignment contract (`cli.numbering_contract`).

The corrected contract is prospective only: it activates through the
reviewed-tag/installed/fresh-runtime boundary — never from source, a dirty
install, a stale process, or caller claims — and it governs only tasks the
mediated writer creates while active. Every pre-activation artifact remains
valid unchanged, with no migration, inventory, receipt, or reclassification.

`write-task`, `validate-task-readiness`, `task-bundle`, and `plan-audit` (and
the MCP tools dispatching into them in-process) all resolve through the one
module patched here, so the activation seam exercised by these tests is the
same seam every surface consumes — CLI and MCP verdicts cannot drift.
"""
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import numbering_contract as nc
from cli.commands import task_bundle, write_task
from cli.commands import validate_task_readiness as readiness_command
from cli.commands.plan_audit import _check_numbering_contract
from cli.commands.validate_task_readiness import (
    _check_plan_ref_aligned,
    _parse_headers,
)
from mcp_server import server as mcp_server
from tests.scaffold import project_scaffold

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64

_KNOWN_RELEASE = {"value": "v9.9.9", "state": "known"}
_UNKNOWN_RELEASE = {"value": None, "state": "unknown"}
_VERIFIED_INSTALL = {
    "materialization": "copy",
    "verification": "verified",
    "mcp_identity": _DIGEST_A,
}


def _active_state():
    state = nc.evaluate_activation(
        _KNOWN_RELEASE, _VERIFIED_INSTALL, None, False
    )
    assert state["active"]
    return state


def _inactive_state():
    state = nc.evaluate_activation(
        _UNKNOWN_RELEASE,
        {"materialization": "source-checkout", "verification": "dirty"},
        None,
        False,
    )
    assert not state["active"]
    return state


def _task_body(plan_ref, phase="PHASE-01-fixture"):
    return (
        "# Task fixture\n"
        "\n"
        f"Phase: {phase}\n"
        f"Plan ref: {plan_ref}\n"
        "Evidence gate: n/a\n"
        "\n"
        "## Goal\n"
        "\n"
        "Do the fixture work.\n"
        "\n"
        "## Acceptance\n"
        "\n"
        "- [ ] Done.\n"
    )


def _make_project(tmp: Path) -> Path:
    project = tmp / "project"
    for status in ("open", "in-progress", "in-review", "done"):
        (project / "tasks" / status).mkdir(parents=True)
    return project


def _write_task(project: Path, task_id: str, slug: str, body: str):
    args = argparse.Namespace(
        project_root=str(project),
        task_id=task_id,
        slug=slug,
        content=body,
        content_file=None,
        source=None,
    )
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
        stderr
    ):
        code = write_task.handler(args)
    return code, stderr.getvalue()


def _readiness_check(project: Path, task_path: Path):
    content = task_path.read_text(encoding="utf-8")
    headers, _presence = _parse_headers(content)
    return _check_plan_ref_aligned(project, task_path, headers)


def _write_phase_file(project: Path, refs, phase="PHASE-01-fixture"):
    phases = project / "phases"
    phases.mkdir(exist_ok=True)
    body = "# Phase fixture\n\n" + "".join(f"- `{ref}` — item\n" for ref in refs)
    (phases / f"{phase}.md").write_text(body, encoding="utf-8")


class TestGrammar(unittest.TestCase):
    def test_plan_ref_grammar(self):
        ref = nc.parse_plan_ref("P03-RESEARCH-010")
        self.assertEqual(ref["phase"], "03")
        self.assertEqual(ref["kind"], "RESEARCH")
        self.assertEqual(ref["suffix"], "010")
        self.assertTrue(ref["kind_supported"])
        self.assertFalse(nc.parse_plan_ref("P03-SMOKE-010")["kind_supported"])
        for bad in ("P3-BUILD-010", "P03-BUILD-10", "TASK-03-010", "", "P03"):
            self.assertIsNone(nc.parse_plan_ref(bad))

    def test_task_id_grammar(self):
        task = nc.parse_task_id("TASK-03-010")
        self.assertEqual(task, {"phase": "03", "suffix": "010"})
        for bad in ("TASK-3-010", "TASK-03-10", "P03-BUILD-010", ""):
            self.assertIsNone(nc.parse_task_id(bad))

    def test_plan_ref_header_window_ends_at_first_section(self):
        body = "# T\n\nPlan ref: P01-BUILD-001\n\n## Notes\nPlan ref: X\n"
        self.assertEqual(nc.plan_ref_header_value(body), "P01-BUILD-001")
        self.assertIsNone(nc.plan_ref_header_value("## Goal\nPlan ref: X\n"))


class TestClassifyBinding(unittest.TestCase):
    def test_every_supported_kind_aligns_from_one_phase_wide_sequence(self):
        # Mixed kinds share one sequence: kind never owns a counter, so any
        # kind may carry any suffix as long as it equals the task's.
        for index, kind in enumerate(nc.SUPPORTED_KINDS, start=1):
            suffix = f"{index:03d}"
            verdict = nc.classify_binding(
                f"TASK-01-{suffix}", f"P01-{kind}-{suffix}"
            )
            self.assertEqual(verdict["classification"], "aligned")
            self.assertFalse(verdict["blocking"])

    def test_suffix_mismatch_blocks_with_expected_and_observed(self):
        verdict = nc.classify_binding("TASK-01-004", "P01-RESEARCH-001")
        self.assertEqual(verdict["classification"], "plan-task-suffix-mismatch")
        self.assertTrue(verdict["blocking"])
        self.assertEqual(verdict["expected_suffix"], "004")
        self.assertEqual(verdict["observed_suffix"], "001")

    def test_structural_failures_fail_closed(self):
        cases = {
            "plan-ref-missing": ("TASK-01-004", ""),
            "plan-ref-malformed": ("TASK-01-004", "n/a"),
            "plan-ref-kind-unsupported": ("TASK-01-004", "P01-SMOKE-004"),
            "plan-ref-phase-mismatch": ("TASK-01-004", "P02-BUILD-004"),
            "task-id-malformed": ("TASK-1-4", "P01-BUILD-004"),
        }
        for expected, (task_id, plan_ref) in cases.items():
            verdict = nc.classify_binding(task_id, plan_ref)
            self.assertEqual(verdict["classification"], expected)
            self.assertTrue(verdict["blocking"])

    def test_phase_header_is_compared_with_task_and_plan_ref_phase(self):
        # The three identities — task id, plan ref, declared Phase: header —
        # must name one phase for newly governed work.
        aligned = nc.classify_binding(
            "TASK-01-002", "P01-BUILD-002", "PHASE-01-fixture"
        )
        self.assertEqual(aligned["classification"], "aligned")
        self.assertFalse(aligned["blocking"])
        mismatch = nc.classify_binding(
            "TASK-01-002", "P01-BUILD-002", "PHASE-02-other"
        )
        self.assertEqual(mismatch["classification"], "phase-header-mismatch")
        self.assertTrue(mismatch["blocking"])
        self.assertIn("PHASE-02-other", mismatch["detail"])
        malformed = nc.classify_binding(
            "TASK-01-002", "P01-BUILD-002", "Phase One"
        )
        self.assertEqual(malformed["classification"], "phase-header-malformed")
        self.assertTrue(malformed["blocking"])
        missing = nc.classify_binding("TASK-01-002", "P01-BUILD-002", "")
        self.assertEqual(missing["classification"], "phase-header-missing")
        self.assertTrue(missing["blocking"])
        # A caller with no task body in hand skips the comparison.
        no_header = nc.classify_binding("TASK-01-002", "P01-BUILD-002", None)
        self.assertEqual(no_header["classification"], "aligned")

    def test_duplicate_binding_names_the_prior_owner(self):
        existing = {"TASK-01-003": "P01-BUILD-003", "TASK-01-004": ""}
        detail = nc.duplicate_binding(
            "TASK-01-005", "P01-BUILD-003", existing
        )
        self.assertIn("TASK-01-003", detail)
        self.assertIsNone(
            nc.duplicate_binding("TASK-01-003", "P01-BUILD-003", existing)
        )
        self.assertIsNone(nc.duplicate_binding("TASK-01-005", "", existing))


class TestActivationBoundary(unittest.TestCase):
    """The reviewed-tag/installed/fresh-runtime boundary, fact by fact."""

    def test_unknown_release_stays_legacy(self):
        state = nc.evaluate_activation(
            _UNKNOWN_RELEASE, _VERIFIED_INSTALL, None, False
        )
        self.assertFalse(state["active"])
        self.assertEqual(state["contract"], nc.CONTRACT_LEGACY)
        self.assertEqual(state["reason"], "release-unknown")

    def test_source_checkout_never_activates_even_with_release_metadata(self):
        state = nc.evaluate_activation(
            _KNOWN_RELEASE,
            {"materialization": "source-checkout", "verification": "verified"},
            None,
            False,
        )
        self.assertFalse(state["active"])
        self.assertEqual(state["reason"], "source-checkout")

    def test_unverified_or_dirty_install_stays_legacy(self):
        for verification in ("dirty", "unverified", None):
            state = nc.evaluate_activation(
                _KNOWN_RELEASE,
                {"materialization": "copy", "verification": verification},
                None,
                False,
            )
            self.assertFalse(state["active"])
            self.assertTrue(
                state["reason"].startswith("installed-content-"),
                state["reason"],
            )

    def test_installed_verified_release_activates_for_cli(self):
        state = _active_state()
        self.assertEqual(state["contract"], nc.CONTRACT_ALIGNED)
        self.assertEqual(state["reason"], "active")
        self.assertEqual(state["boundary"], nc.ACTIVATION_BOUNDARY)

    def test_mcp_activates_only_with_bound_fresh_process_proof(self):
        running = {
            "loaded_content": {
                "mcp_identity": _DIGEST_A,
                "mcp_verification": "verified",
            }
        }
        state = nc.evaluate_activation(
            _KNOWN_RELEASE, _VERIFIED_INSTALL, running, True
        )
        self.assertTrue(state["active"])
        self.assertEqual(
            state["running_process"]["fresh_process"], "proven"
        )

    def test_newly_installed_files_without_fresh_process_stay_legacy(self):
        # The connected server still holds the previously loaded content:
        # its identity does not bind to the newly installed MCP content.
        running = {
            "loaded_content": {
                "mcp_identity": _DIGEST_B,
                "mcp_verification": "verified",
            }
        }
        state = nc.evaluate_activation(
            _KNOWN_RELEASE, _VERIFIED_INSTALL, running, True
        )
        self.assertFalse(state["active"])
        self.assertEqual(state["reason"], "fresh-process-unproven")

    def test_mcp_context_without_a_readable_fact_fails_closed(self):
        state = nc.evaluate_activation(
            _KNOWN_RELEASE, _VERIFIED_INSTALL, None, True
        )
        self.assertFalse(state["active"])
        self.assertEqual(state["reason"], "fresh-process-unproven")

    def test_this_source_tree_observes_an_inactive_runtime(self):
        # The tests execute from a source tree, never an installed release:
        # the real observed boundary must report the legacy contract, so
        # source changes alone can never activate the corrected behavior.
        state = nc.activation_state()
        self.assertFalse(state["active"])
        self.assertEqual(state["contract"], nc.CONTRACT_LEGACY)


class TestMediatedAuthoring(unittest.TestCase):
    """`write-task` through the shared resolver seam."""

    def test_inactive_runtime_keeps_old_contract_for_new_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp))
            with mock.patch.object(
                nc, "activation_state", return_value=_inactive_state()
            ):
                code, stderr = _write_task(
                    project,
                    "TASK-01-004",
                    "old-style",
                    _task_body("P01-RESEARCH-001"),
                )
            self.assertEqual(code, 0, stderr)
            task = project / "tasks" / "open" / "TASK-01-004-old-style.md"
            self.assertTrue(task.is_file())
            self.assertEqual(nc.governed_task_ids(project), frozenset())

    def test_active_creation_accepts_alignment_and_records_governance(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp))
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                code, stderr = _write_task(
                    project,
                    "TASK-01-002",
                    "aligned",
                    _task_body("P01-RESEARCH-002"),
                )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(
                nc.governed_task_ids(project), frozenset({"TASK-01-002"})
            )

    def test_active_creation_refuses_a_mismatched_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp))
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                code, stderr = _write_task(
                    project,
                    "TASK-01-004",
                    "mismatched",
                    _task_body("P01-RESEARCH-001"),
                )
            self.assertEqual(code, 1)
            self.assertIn("plan-task-suffix-mismatch", stderr)
            self.assertIn("004", stderr)
            self.assertIn("001", stderr)
            self.assertEqual(
                list((project / "tasks" / "open").iterdir()), []
            )
            self.assertEqual(nc.governed_task_ids(project), frozenset())

    def test_active_creation_refuses_reusing_any_bound_plan_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp))
            # A pre-activation, hand-preserved task already owns the ref.
            (project / "tasks" / "done" / "TASK-01-009-old.md").write_text(
                _task_body("P01-BUILD-004"), encoding="utf-8"
            )
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                code, stderr = _write_task(
                    project,
                    "TASK-01-004",
                    "reuse",
                    _task_body("P01-BUILD-004"),
                )
            self.assertEqual(code, 1)
            self.assertIn("plan-ref-reused", stderr)
            self.assertIn("TASK-01-009", stderr)

    def test_active_creation_refuses_a_wrong_phase_header(self):
        # An aligned suffix cannot smuggle in a foreign phase anchor: the
        # declared Phase: header must name the task's own phase.
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp))
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                code, stderr = _write_task(
                    project,
                    "TASK-01-004",
                    "foreign-phase",
                    _task_body("P01-BUILD-004", phase="PHASE-02-other"),
                )
            self.assertEqual(code, 1)
            self.assertIn("phase-header-mismatch", stderr)
            self.assertIn("PHASE-02-other", stderr)
            self.assertEqual(
                list((project / "tasks" / "open").iterdir()), []
            )
            self.assertEqual(nc.governed_task_ids(project), frozenset())

    def test_each_corrective_task_owns_a_distinct_plan_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp))
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                first, stderr = _write_task(
                    project,
                    "TASK-01-005",
                    "corrective-one",
                    _task_body("P01-CORRECTIVE-005"),
                )
                self.assertEqual(first, 0, stderr)
                second, stderr = _write_task(
                    project,
                    "TASK-01-006",
                    "corrective-two",
                    _task_body("P01-CORRECTIVE-006"),
                )
                self.assertEqual(second, 0, stderr)
                # A third corrective task trying to ride on the first one's
                # ref is refused: under phase-wide allocation a new task can
                # never share an already-allocated suffix, so each corrective
                # item necessarily receives its own distinct plan ref.
                reused, stderr = _write_task(
                    project,
                    "TASK-01-007",
                    "corrective-reuse",
                    _task_body("P01-CORRECTIVE-005"),
                )
            self.assertEqual(reused, 1)
            self.assertIn("plan-task-suffix-mismatch", stderr)

    def test_pre_activation_task_updates_pass_untouched(self):
        # An existing mismatched pair is preserved history, not new work:
        # updating its body under the active contract must not retrofit it.
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp))
            existing = (
                project / "tasks" / "in-progress" / "TASK-01-010-old.md"
            )
            existing.write_text(
                _task_body("P01-BUILD-007"), encoding="utf-8"
            )
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                code, stderr = _write_task(
                    project,
                    "TASK-01-010",
                    "old",
                    _task_body("P01-BUILD-007") + "\nUpdated.\n",
                )
            self.assertEqual(code, 0, stderr)
            self.assertIn(
                "Plan ref: P01-BUILD-007",
                existing.read_text(encoding="utf-8"),
            )
            self.assertEqual(nc.governed_task_ids(project), frozenset())

    def test_governed_task_updates_stay_governed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp))
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                code, stderr = _write_task(
                    project,
                    "TASK-01-002",
                    "aligned",
                    _task_body("P01-BUILD-002"),
                )
                self.assertEqual(code, 0, stderr)
                code, stderr = _write_task(
                    project,
                    "TASK-01-002",
                    "aligned",
                    _task_body("P01-RESEARCH-001"),
                )
            self.assertEqual(code, 1)
            self.assertIn("plan-task-suffix-mismatch", stderr)


class TestReadinessAndAudit(unittest.TestCase):
    """`plan-ref-aligned` and plan-audit findings for newly governed work."""

    def _governed_project(self, tmp: Path):
        project = _make_project(tmp)
        _write_phase_file(project, ["P01-RESEARCH-002"])
        with mock.patch.object(
            nc, "activation_state", return_value=_active_state()
        ):
            code, stderr = _write_task(
                project,
                "TASK-01-002",
                "aligned",
                _task_body("P01-RESEARCH-002"),
            )
        assert code == 0, stderr
        return project, project / "tasks" / "open" / "TASK-01-002-aligned.md"

    def test_governed_aligned_task_passes_readiness_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, task = self._governed_project(Path(tmp))
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                check = _readiness_check(project, task)
                blockers, state = _check_numbering_contract(project)
            self.assertTrue(check["pass"], check["reason"])
            self.assertEqual(blockers, [])
            self.assertTrue(state["active"])

    def test_readiness_accepts_a_matching_phase_anchor(self):
        # Positive anchor chain: the task id, plan ref, and Phase: header
        # name phase 01, and the declared phase file carries the plan ref.
        with tempfile.TemporaryDirectory() as tmp:
            project, task = self._governed_project(Path(tmp))
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                check = _readiness_check(project, task)
            self.assertTrue(check["pass"], check["reason"])

    def test_readiness_blocks_a_governed_task_declaring_a_foreign_phase(self):
        # F1 probe: TASK-01-002 / P01-RESEARCH-002 declaring an existing
        # PHASE-02-* file whose body never mentions the plan ref must fail —
        # suffix alignment alone is not a complete anchor chain.
        with tempfile.TemporaryDirectory() as tmp:
            project, task = self._governed_project(Path(tmp))
            _write_phase_file(
                project, ["P02-BUILD-001"], phase="PHASE-02-other"
            )
            task.write_text(
                _task_body("P01-RESEARCH-002", phase="PHASE-02-other"),
                encoding="utf-8",
            )
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                check = _readiness_check(project, task)
                blockers, _state = _check_numbering_contract(project)
            self.assertFalse(check["pass"])
            self.assertIn("PHASE-02-other", check["reason"])
            self.assertEqual(len(blockers), 1)
            self.assertEqual(blockers[0]["kind"], "phase-header-mismatch")

    def test_readiness_blocks_when_the_phase_file_lacks_the_plan_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, task = self._governed_project(Path(tmp))
            _write_phase_file(project, ["P01-BUILD-099"])
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                check = _readiness_check(project, task)
            self.assertFalse(check["pass"])
            self.assertIn("does not carry plan ref P01-RESEARCH-002", check["reason"])

    def test_readiness_blocks_when_the_declared_phase_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, task = self._governed_project(Path(tmp))
            (project / "phases" / "PHASE-01-fixture.md").unlink()
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                check = _readiness_check(project, task)
            self.assertFalse(check["pass"])
            self.assertIn("phases/PHASE-01-fixture.md", check["reason"])

    def test_rewritten_governed_task_blocks_readiness_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, task = self._governed_project(Path(tmp))
            # Hand-rewriting the governed binding out of band cannot escape
            # the contract the task was created under.
            task.write_text(
                _task_body("P01-RESEARCH-001"), encoding="utf-8"
            )
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                check = _readiness_check(project, task)
                blockers, _state = _check_numbering_contract(project)
            self.assertFalse(check["pass"])
            self.assertIn("expects plan-ref suffix 002", check["reason"])
            self.assertEqual(len(blockers), 1)
            self.assertEqual(blockers[0]["kind"], "plan-task-suffix-mismatch")
            self.assertEqual(blockers[0]["expected_suffix"], "002")
            self.assertEqual(blockers[0]["observed_suffix"], "001")

    def test_pre_activation_mismatches_are_never_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(Path(tmp))
            task = project / "tasks" / "in-progress" / "TASK-01-010-old.md"
            task.write_text(_task_body("P01-BUILD-007"), encoding="utf-8")
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                check = _readiness_check(project, task)
                blockers, state = _check_numbering_contract(project)
            self.assertTrue(check["pass"])
            self.assertEqual(blockers, [])
            self.assertTrue(state["active"])
            self.assertEqual(
                task.read_text(encoding="utf-8"),
                _task_body("P01-BUILD-007"),
            )

    def test_stale_runtime_keeps_every_check_inert(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, task = self._governed_project(Path(tmp))
            task.write_text(
                _task_body("P01-RESEARCH-001"), encoding="utf-8"
            )
            with mock.patch.object(
                nc, "activation_state", return_value=_inactive_state()
            ):
                check = _readiness_check(project, task)
                blockers, state = _check_numbering_contract(project)
            self.assertTrue(check["pass"])
            self.assertEqual(blockers, [])
            self.assertFalse(state["active"])
            self.assertEqual(state["contract"], nc.CONTRACT_LEGACY)

    def test_audit_reports_reuse_only_when_governed_work_is_involved(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, task = self._governed_project(Path(tmp))
            # A hand-written task binding the governed task's ref.
            (
                project / "tasks" / "done" / "TASK-01-011-dupe.md"
            ).write_text(_task_body("P01-RESEARCH-002"), encoding="utf-8")
            # Two purely pre-activation tasks sharing a ref stay history.
            for name in ("TASK-01-012-a.md", "TASK-01-013-b.md"):
                (project / "tasks" / "done" / name).write_text(
                    _task_body("P01-BUILD-020"), encoding="utf-8"
                )
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                blockers, _state = _check_numbering_contract(project)
            reuse = [b for b in blockers if b["kind"] == "plan-ref-reused"]
            self.assertEqual(len(reuse), 1)
            self.assertEqual(reuse[0]["plan_ref"], "P01-RESEARCH-002")
            self.assertIn("TASK-01-011", reuse[0]["task_ids"])

    def test_audit_is_idempotent_and_mutates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, task = self._governed_project(Path(tmp))
            before = {
                path: path.read_bytes()
                for path in sorted(project.rglob("*"))
                if path.is_file()
            }
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                first = _check_numbering_contract(project)
                second = _check_numbering_contract(project)
            self.assertEqual(first, second)
            after = {
                path: path.read_bytes()
                for path in sorted(project.rglob("*"))
                if path.is_file()
            }
            self.assertEqual(before, after)

    def test_governance_follows_the_task_id_across_status_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, task = self._governed_project(Path(tmp))
            moved = project / "tasks" / "in-progress" / task.name
            task.rename(moved)
            self.assertEqual(
                nc.governed_task_ids(project), frozenset({"TASK-01-002"})
            )
            moved.write_text(
                _task_body("P01-RESEARCH-001"), encoding="utf-8"
            )
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                blockers, _state = _check_numbering_contract(project)
            self.assertEqual(len(blockers), 1)
            self.assertEqual(
                blockers[0]["task_path"].split("/")[1], "in-progress"
            )


class TestSkippedSuffixes(unittest.TestCase):
    """Deterministic skipped-suffix boundary (SPEC-03-010 edge case).

    Under the prospective contract the mediated writer is the authoritative
    allocator: each successful governed creation allocates its phase-wide
    suffix and binds its plan ref. A gap is therefore allowed only where that
    authoritative allocation has advanced past (or later reserves) the
    skipped values — a governed task may take a higher suffix leaving values
    unused, and a skipped value may be filled only by its own aligned,
    freshly allocated ref. Reuse is always blocked: an already-bound plan ref
    is never reallocated, and a skipped value cannot be filled by riding an
    existing allocation's ref.
    """

    def _allocated_project(self, tmp: Path):
        project = _make_project(tmp)
        _write_phase_file(
            project, ["P01-BUILD-002", "P01-DESIGN-005", "P01-TEST-003"]
        )
        with mock.patch.object(
            nc, "activation_state", return_value=_active_state()
        ):
            code, stderr = _write_task(
                project, "TASK-01-002", "first", _task_body("P01-BUILD-002")
            )
            assert code == 0, stderr
            # The allocator advances past 003/004: the gap is legitimate
            # because it was produced by authoritative allocation, not by a
            # caller claiming unallocated values for someone else's work.
            code, stderr = _write_task(
                project, "TASK-01-005", "skip", _task_body("P01-DESIGN-005")
            )
            assert code == 0, stderr
        return project

    def test_gap_produced_by_authoritative_allocation_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._allocated_project(Path(tmp))
            self.assertEqual(
                nc.governed_task_ids(project),
                frozenset({"TASK-01-002", "TASK-01-005"}),
            )
            skipped = (
                project / "tasks" / "open" / "TASK-01-005-skip.md"
            )
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                check = _readiness_check(project, skipped)
                blockers, state = _check_numbering_contract(project)
            self.assertTrue(check["pass"], check["reason"])
            self.assertEqual(blockers, [])
            self.assertTrue(state["active"])

    def test_skipped_value_is_filled_only_by_fresh_aligned_allocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._allocated_project(Path(tmp))
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                # Riding an existing allocation's ref into the gap: blocked.
                code, stderr = _write_task(
                    project,
                    "TASK-01-003",
                    "ride",
                    _task_body("P01-DESIGN-005"),
                )
                self.assertEqual(code, 1)
                self.assertIn("plan-task-suffix-mismatch", stderr)
                # Reserving the skipped value through the allocator with its
                # own aligned ref: allowed.
                code, stderr = _write_task(
                    project,
                    "TASK-01-003",
                    "fill",
                    _task_body("P01-TEST-003"),
                )
                self.assertEqual(code, 0, stderr)
            self.assertEqual(
                nc.governed_task_ids(project),
                frozenset({"TASK-01-002", "TASK-01-003", "TASK-01-005"}),
            )

    def test_reuse_of_a_consumed_skipped_value_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._allocated_project(Path(tmp))
            # Pre-activation history already consumed the skipped value's
            # ref; the gap cannot be "filled" by reallocating that ref.
            (project / "tasks" / "done" / "TASK-01-020-old.md").write_text(
                _task_body("P01-TEST-003"), encoding="utf-8"
            )
            with mock.patch.object(
                nc, "activation_state", return_value=_active_state()
            ):
                code, stderr = _write_task(
                    project,
                    "TASK-01-003",
                    "reuse",
                    _task_body("P01-TEST-003"),
                )
            self.assertEqual(code, 1)
            self.assertIn("plan-ref-reused", stderr)
            self.assertIn("TASK-01-020", stderr)


_TOML_PROJECT = (
    "[project]\n"
    'id = "numbering-fixture"\n'
    'name = "Numbering Fixture"\n'
    'project_schema_version = "v0.9.0"\n'
    "work_roots = []\n"
)


class TestTaskBundleAndMcpDispatch(unittest.TestCase):
    """Aligned and mismatched governed tasks through the public task-bundle
    path and the MCP dispatch path.

    Both surfaces resolve through `cli.numbering_contract` — the activation
    seam patched here — and must emit consistent structured verdicts, so the
    CLI record and the MCP `structuredContent` record are compared directly.
    """

    def _governed_scaffold(self):
        scaffold = project_scaffold(cartopian_toml=_TOML_PROJECT)
        self.addCleanup(scaffold.cleanup)
        scaffold.write(
            "IMPLEMENTATION_PLAN.md", "P01-BUILD-002\nP01-RESEARCH-001\n"
        )
        _write_phase_file(scaffold.project_root, ["P01-BUILD-002"])
        scaffold.capture_request(
            request_id="REQUEST-001",
            unit="task:TASK-01-002",
            text="Run the aligned governed task.",
        )
        with mock.patch.object(
            nc, "activation_state", return_value=_active_state()
        ):
            code, stderr = _write_task(
                scaffold.project_root,
                "TASK-01-002",
                "aligned",
                _task_body("P01-BUILD-002"),
            )
        assert code == 0, stderr
        task_path = (
            scaffold.project_root / "tasks" / "open" / "TASK-01-002-aligned.md"
        )
        return scaffold, task_path

    def _cli_bundle_record(self, task_path: Path):
        args = argparse.Namespace(task_path=str(task_path))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ):
            code = task_bundle.handler(args)
        records = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), 1, stderr.getvalue())
        return code, records[0]

    def _cli_readiness_record(self, task_path: Path):
        args = argparse.Namespace(task_path=str(task_path))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ):
            code = readiness_command.handler(args)
        records = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), 1, stderr.getvalue())
        return code, records[0]

    def test_aligned_governed_task_is_ready_on_both_surfaces(self):
        scaffold, task_path = self._governed_scaffold()
        with mock.patch.object(
            nc, "activation_state", return_value=_active_state()
        ):
            cli_code, cli_record = self._cli_bundle_record(task_path)
            mcp_result = mcp_server.call_tool(
                "task_bundle", {"task_path": str(task_path)}
            )
        self.assertEqual(cli_code, 0)
        self.assertTrue(cli_record["ready"], cli_record["validator_blockers"])
        self.assertEqual(cli_record["validator_blockers"], [])
        self.assertFalse(mcp_result["isError"])
        structured = mcp_result["structuredContent"]
        self.assertEqual(structured["exit_code"], 0)
        # One resolver, one verdict: the MCP dispatch path emits the same
        # structured record the public CLI path emits.
        self.assertEqual(structured["records"], [cli_record])

    def test_mismatched_governed_task_blocks_identically_on_both_surfaces(
        self,
    ):
        scaffold, task_path = self._governed_scaffold()
        # Hand-rewrite the governed binding out of band: P01-RESEARCH-001 is
        # a real plan item, so only the shared resolver can catch the
        # suffix mismatch.
        task_path.write_text(
            _task_body("P01-RESEARCH-001"), encoding="utf-8"
        )
        with mock.patch.object(
            nc, "activation_state", return_value=_active_state()
        ):
            bundle_code, bundle_record = self._cli_bundle_record(task_path)
            readiness_code, readiness_record = self._cli_readiness_record(
                task_path
            )
            mcp_bundle = mcp_server.call_tool(
                "task_bundle", {"task_path": str(task_path)}
            )
            mcp_readiness = mcp_server.call_tool(
                "validate_task_readiness", {"task_path": str(task_path)}
            )
        # Public task-bundle path: not ready, resolver-attributed blocker.
        self.assertEqual(bundle_code, 0)
        self.assertFalse(bundle_record["ready"])
        aligned_blockers = [
            blocker
            for blocker in bundle_record["validator_blockers"]
            if blocker.startswith("plan-ref-aligned:")
        ]
        self.assertEqual(len(aligned_blockers), 1)
        self.assertIn("expects plan-ref suffix 002", aligned_blockers[0])
        self.assertIn("P01-RESEARCH-001", aligned_blockers[0])
        # Readiness path: same check, failing exit.
        self.assertEqual(readiness_code, 1)
        aligned_checks = [
            check
            for check in readiness_record["checks"]
            if check["name"] == "plan-ref-aligned"
        ]
        self.assertEqual(len(aligned_checks), 1)
        self.assertFalse(aligned_checks[0]["pass"])
        self.assertIn("expects plan-ref suffix 002", aligned_checks[0]["reason"])
        # MCP dispatch path: identical structured records and exit codes.
        self.assertEqual(
            mcp_bundle["structuredContent"]["records"], [bundle_record]
        )
        self.assertTrue(mcp_readiness["isError"])
        self.assertEqual(
            mcp_readiness["structuredContent"]["exit_code"], 1
        )
        self.assertEqual(
            mcp_readiness["structuredContent"]["records"], [readiness_record]
        )

    def test_broken_phase_anchor_blocks_through_both_surfaces(self):
        # The F1 anchor gap, exercised end to end: the phase file stops
        # carrying the governed task's plan ref, so bundle and MCP dispatch
        # must both report the anchor blocker from the shared resolver.
        scaffold, task_path = self._governed_scaffold()
        _write_phase_file(scaffold.project_root, ["P01-BUILD-099"])
        with mock.patch.object(
            nc, "activation_state", return_value=_active_state()
        ):
            bundle_code, bundle_record = self._cli_bundle_record(task_path)
            mcp_bundle = mcp_server.call_tool(
                "task_bundle", {"task_path": str(task_path)}
            )
        self.assertEqual(bundle_code, 0)
        self.assertFalse(bundle_record["ready"])
        anchor_blockers = [
            blocker
            for blocker in bundle_record["validator_blockers"]
            if "does not carry plan ref P01-BUILD-002" in blocker
        ]
        self.assertEqual(len(anchor_blockers), 1)
        self.assertTrue(anchor_blockers[0].startswith("plan-ref-aligned:"))
        self.assertEqual(
            mcp_bundle["structuredContent"]["records"], [bundle_record]
        )


if __name__ == "__main__":
    unittest.main()
