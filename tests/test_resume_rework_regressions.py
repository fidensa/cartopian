"""Adversarial regressions for the resumable-update rework.

Four failures survived the first delivery of bounded resumable progress.  Each
test below reproduces one of them deterministically — no sleeps, no threads, no
wall-clock races — so the defect is proven by an interleaving the test controls
rather than by one it hopes to observe.

1. Orphan-lease takeover removed a pathname instead of the object it inspected,
   so two recoverers could both believe they held the root.
2. Apply gated on the intrinsic progress-read classification, so a changed
   source could overwrite the last useful recovery evidence of its predecessor.
3. Portable evidence could pair a persisted envelope's source authority with the
   current run's remaining work, producing an internally inconsistent record.
4. Migration-deferral reuse validated the offer but not the source identity that
   produced it, so a deferral crossed silently into a changed source.

A fifth followed from the same shape as the second: apply re-read progress under
the lease but only recomputed schema usability and source binding, so a plan
computed against absent progress could take over an orphan lease and erase the
open mutation boundary another run had just published.
"""
from __future__ import annotations

import io
import json
import platform
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import cli.resume_state as resume_state
from cli.install_state import stable_projection
from cli.install_workflow import (
    WorkflowRefusal,
    _current_resume_facts,
    apply_workflow,
    surface_retry_profiles,
)
from cli.main import main as cli_main
from cli.resume_state import (
    LEASE_FILE,
    PROGRESS_FILE,
    QUARANTINE_FILE,
    ProgressRefusal,
    acquire_lease,
    build_envelope,
    new_owner_token,
    portable_evidence,
    read_lease,
    read_progress,
    render_portable_evidence,
)

from tests.test_resumable_update_evidence import (
    REPO_ROOT,
    ResumableProgressTestCase,
)

_MIGRATION_SURFACE = "project-schema-migration-offers"


class ReworkTestCase(ResumableProgressTestCase):
    """Shared fixtures for the four adversarial cases."""

    def alternate_source(self) -> Path:
        """A byte-different source tree that still offers the same work.

        Only the changelog differs, so every surface plan and every migration
        offer is identical; the one thing that changes is the source identity.
        That isolates source binding from every other reason to refuse reuse.
        """
        alternate = self.root / "alternate-source"
        shutil.copytree(
            REPO_ROOT,
            alternate,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "projects", ".pytest_cache"
            ),
        )
        changelog = alternate / "protocol" / "CHANGELOG.md"
        changelog.write_text(
            changelog.read_text(encoding="utf-8") + "\n<!-- altered -->\n",
            encoding="utf-8",
        )
        return alternate

    def drift_client_config(self) -> None:
        """Leave one repair offered so the run cannot reach terminal proof.

        Preserved predecessor evidence is superseded once a later run proves it
        completed, so a test that inspects preservation must keep the recovering
        run short of that proof.
        """
        (self.client_home / ".codex" / "config.toml").write_text(
            '[mcp_servers.cartopian]\ncommand = "/drifted"\n', encoding="utf-8"
        )

    def dead_pid(self) -> int:
        """A PID on this node that has certainly exited and been reaped."""
        finished = subprocess.Popen([sys.executable, "-c", ""])
        finished.wait()
        return finished.pid

    def orphan_lease(self) -> bytes:
        """Leave a lease whose holder is provably gone on this host."""
        self.install_root.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                {
                    "owner": "owner-crashed",
                    "run": "run:crashed",
                    "pid": self.dead_pid(),
                    "node": platform.node(),
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        resume_state.recoverable_write_text(
            self.install_root / LEASE_FILE, payload
        )
        return payload.encode("utf-8")

    def cli(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli_main(arguments)
        return exit_code, output.getvalue()


class AtomicOrphanTakeoverTests(ReworkTestCase):
    def test_simultaneous_takeover_yields_exactly_one_live_owner(self) -> None:
        """Two recoverers inspecting one orphan cannot both claim the root.

        The interleaving is forced, not raced: the second recoverer runs to
        completion inside the first recoverer's orphan probe, which is exactly
        the window between "this lease is orphaned" and "remove it".
        """
        self.orphan_lease()
        self.assertEqual(
            read_progress(self.install_root)["lease_state"], "orphaned"
        )

        probe = resume_state.lease_is_orphaned
        second = {"ran": False, "owner": "", "held": False}

        def racing_probe(lease):
            verdict = probe(lease)
            if verdict and not second["ran"]:
                # The second recoverer completes its whole takeover here.
                second["ran"] = True
                second["owner"] = new_owner_token()
                try:
                    acquire_lease(
                        self.install_root,
                        run_marker="run:second",
                        owner=second["owner"],
                    )
                    second["held"] = True
                except ProgressRefusal:
                    second["held"] = False
            return verdict

        first_owner = new_owner_token()
        first_held = False
        with patch(
            "cli.resume_state.lease_is_orphaned", side_effect=racing_probe
        ):
            try:
                acquire_lease(
                    self.install_root,
                    run_marker="run:first",
                    owner=first_owner,
                )
                first_held = True
            except ProgressRefusal:
                first_held = False

        self.assertTrue(second["ran"], "the interleaving never happened")
        holders = [
            owner
            for owner, held in (
                (first_owner, first_held),
                (second["owner"], second["held"]),
            )
            if held
        ]
        self.assertEqual(
            len(holders),
            1,
            "two recoverers took over the same orphan lease",
        )
        self.assertEqual(read_lease(self.install_root)["owner"], holders[0])

        # The loser has no authority: it cannot cross a mutation boundary and
        # cannot release the winner's lease.
        loser = next(
            owner
            for owner in (first_owner, second["owner"])
            if owner != holders[0]
        )
        with self.assertRaises(ProgressRefusal) as caught:
            resume_state.begin_progress(
                self.install_root,
                record={
                    "run": {
                        "operation": "update",
                        "marker": "run:loser",
                        "source": {
                            "kind": "local-checkout",
                            "value": "sha256:source",
                            "authority": "maintainer-source-content",
                        },
                    }
                },
                projection={},
                surface_profiles=surface_retry_profiles(),
                owner=loser,
            )
        self.assertEqual(caught.exception.code, "lease-conflict")
        resume_state.release_lease(self.install_root, loser)
        self.assertEqual(read_lease(self.install_root)["owner"], holders[0])

    def test_takeover_leaves_no_stray_claim_artifacts(self) -> None:
        """A completed takeover leaves exactly one lease and nothing else."""
        self.orphan_lease()
        owner = new_owner_token()
        claim = acquire_lease(
            self.install_root, run_marker="run:recovery", owner=owner
        )
        self.assertEqual(claim["takeover"], "released-orphaned-lease")
        self.assertEqual(
            sorted(item.name for item in self.install_root.iterdir()),
            [LEASE_FILE],
        )
        self.assertEqual(read_lease(self.install_root)["owner"], owner)

    def test_a_live_lease_is_never_removed_by_a_stale_inspection(self) -> None:
        """Removal is bound to the inspected object, not to the pathname."""
        self.orphan_lease()
        live = new_owner_token()
        probe = resume_state.lease_is_orphaned
        replaced = {"done": False}

        def replace_between_inspection_and_removal(lease):
            verdict = probe(lease)
            if verdict and not replaced["done"]:
                replaced["done"] = True
                (self.install_root / LEASE_FILE).unlink()
                acquire_lease(
                    self.install_root, run_marker="run:live", owner=live
                )
            return verdict

        with patch(
            "cli.resume_state.lease_is_orphaned",
            side_effect=replace_between_inspection_and_removal,
        ):
            with self.assertRaises(ProgressRefusal) as caught:
                acquire_lease(
                    self.install_root,
                    run_marker="run:stale",
                    owner=new_owner_token(),
                )
        self.assertEqual(caught.exception.code, "lease-conflict")
        self.assertEqual(read_lease(self.install_root)["owner"], live)


class SourceMismatchPreservationTests(ReworkTestCase):
    def test_changed_source_preserves_predecessor_evidence_verbatim(
        self,
    ) -> None:
        """A new source may not overwrite its predecessor's recovery record."""
        apply_workflow(self.plan(operation="fresh-install"))
        predecessor = (self.install_root / PROGRESS_FILE).read_bytes()
        predecessor_identity = resume_state._digest_text(
            predecessor.decode("utf-8")
        )
        alternate = self.alternate_source()
        self.drift_client_config()

        plan = self.plan(source_root=alternate)
        self.assertEqual(
            plan["internal"]["resume_assessment"]["compatibility"],
            "source-mismatch",
        )
        apply_workflow(plan)

        preserved = self.install_root / QUARANTINE_FILE
        self.assertTrue(
            preserved.exists(),
            "the source-mismatched predecessor was destroyed",
        )
        self.assertEqual(preserved.read_bytes(), predecessor)
        envelope = self.envelope()
        self.assertEqual(
            envelope["recovery"]["preserved_classification"], "source-mismatch"
        )
        self.assertEqual(envelope["recovery"]["quarantine"], QUARANTINE_FILE)
        self.assertEqual(
            envelope["recovery"]["quarantined_identity"], predecessor_identity
        )
        # The new envelope is bound to the new source and reuses nothing.
        self.assertNotEqual(
            envelope["run"]["source"]["value"],
            json.loads(predecessor.decode("utf-8"))["run"]["source"]["value"],
        )

    def test_changed_source_cannot_overwrite_preserved_evidence(self) -> None:
        """A second changed-source run cannot consume the preserved record."""
        apply_workflow(self.plan(operation="fresh-install"))
        predecessor = (self.install_root / PROGRESS_FILE).read_bytes()
        alternate = self.alternate_source()
        self.drift_client_config()
        apply_workflow(self.plan(source_root=alternate))

        third = self.root / "third-source"
        shutil.copytree(
            alternate,
            third,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "projects", ".pytest_cache"
            ),
        )
        changelog = third / "protocol" / "CHANGELOG.md"
        changelog.write_text(
            changelog.read_text(encoding="utf-8") + "\n<!-- third -->\n",
            encoding="utf-8",
        )
        with self.assertRaises(WorkflowRefusal) as caught:
            apply_workflow(self.plan(source_root=third))
        self.assertIn("preserved", str(caught.exception))
        self.assertEqual(
            (self.install_root / QUARANTINE_FILE).read_bytes(), predecessor
        )

    def test_preserved_evidence_survives_until_terminal_proof_replaces_it(
        self,
    ) -> None:
        """Supersession is by proof, and the content identity outlives it.

        The preserved record is the last useful recovery evidence only until a
        later run proves every surface verified against the new source.  This
        pins that boundary explicitly: preserved while the recovering run is
        incomplete, superseded once it is terminal, and identifiable either way.
        """
        apply_workflow(self.plan(operation="fresh-install"))
        predecessor = (self.install_root / PROGRESS_FILE).read_bytes()
        identity = resume_state._digest_bytes(predecessor)
        alternate = self.alternate_source()
        self.drift_client_config()

        apply_workflow(self.plan(source_root=alternate))
        self.assertEqual(
            (self.install_root / QUARANTINE_FILE).read_bytes(), predecessor
        )
        self.assertEqual(self.envelope()["terminal"]["completion"], "pending")

        # Resolve the outstanding repair so the recovering run reaches terminal
        # proof, which is the only thing allowed to supersede the record.
        terminal = apply_workflow(
            self.plan(
                source_root=alternate,
                decisions={"client-registrations": "accept"},
            )
        )
        self.assertIn(
            terminal["outcome"]["status"], ("complete", "complete-qualified")
        )
        envelope = self.envelope()
        self.assertEqual(envelope["terminal"]["cleanup"], "advanced")
        self.assertFalse((self.install_root / QUARANTINE_FILE).exists())
        self.assertEqual(envelope["recovery"]["quarantine"], "")
        self.assertEqual(
            envelope["recovery"]["quarantined_identity"], identity
        )
        self.assertEqual(
            envelope["recovery"]["preserved_classification"], "source-mismatch"
        )

    def test_changed_source_cannot_mutate_across_an_uncertain_boundary(
        self,
    ) -> None:
        """An interrupted predecessor is not replayable by a changed source."""
        self.crash_mid_mutation()
        untouched = (self.install_root / PROGRESS_FILE).read_bytes()
        alternate = self.alternate_source()
        plan = self.plan(source_root=alternate, operation="fresh-install")
        self.assertEqual(
            plan["internal"]["resume_assessment"]["compatibility"],
            "source-mismatch",
        )
        with self.assertRaises(WorkflowRefusal) as caught:
            apply_workflow(plan)
        self.assertIn("inspected", str(caught.exception))
        self.assertEqual(
            (self.install_root / PROGRESS_FILE).read_bytes(), untouched
        )

    def test_orphaned_predecessor_is_preserved_before_replanning(self) -> None:
        """An orphaned record is preserved, not silently replaced."""
        apply_workflow(self.plan(operation="fresh-install"))
        predecessor = (self.install_root / PROGRESS_FILE).read_bytes()
        for name in (
            "protocol",
            "templates",
            "skills",
            "cli",
            "scripts",
            "bin",
        ):
            target = self.install_root / name
            if target.is_dir():
                shutil.rmtree(target)
        for name in ("install-cartopian.md", "CHANGELOG.md"):
            target = self.install_root / name
            if target.exists():
                target.unlink()
        self.drift_client_config()

        plan = self.plan()
        self.assertEqual(
            plan["internal"]["resume_assessment"]["compatibility"], "orphaned"
        )
        apply_workflow(plan)
        self.assertEqual(
            (self.install_root / QUARANTINE_FILE).read_bytes(), predecessor
        )
        self.assertEqual(
            self.envelope()["recovery"]["preserved_classification"], "orphaned"
        )


class PortableEvidenceAuthorityTests(ReworkTestCase):
    def test_portable_evidence_never_merges_stale_source_authority(
        self,
    ) -> None:
        """One authoritative observation set, or a refusal — never a blend."""
        apply_workflow(self.plan(operation="fresh-install"))
        persisted = self.envelope()
        alternate = self.alternate_source()
        assessment = self.assessment(source_root=alternate)
        self.assertEqual(assessment["compatibility"], "source-mismatch")

        with self.assertRaises(ProgressRefusal) as caught:
            portable_evidence(persisted, assessment=assessment)
        self.assertEqual(
            caught.exception.code, "portable-evidence-authority-conflict"
        )

    def test_incompatible_predecessor_renders_a_wholly_current_record(
        self,
    ) -> None:
        """The emitted record is internally consistent and self-classifying."""
        apply_workflow(self.plan(operation="fresh-install"))
        alternate = self.alternate_source()
        exit_code, raw = self.cli(
            [
                "resume-install",
                str(alternate),
                str(self.install_root),
                "--client",
                "codex",
                "--portable-evidence",
            ]
        )
        self.assertEqual(exit_code, 1)
        record = json.loads(raw)
        portable = record["portable_evidence"]
        self.assertEqual(record["resume"]["compatibility"], "source-mismatch")
        self.assertEqual(
            portable["source"]["identity"],
            record["resume"]["current_run"]["source_identity"],
            "portable evidence carried a source identity the plan did not",
        )
        self.assertEqual(
            portable["predecessor"]["classification"], "source-mismatch"
        )
        self.assertEqual(portable["predecessor"]["authority"], "not-used")
        self.assertNotEqual(
            portable["predecessor"]["superseded_source_identity"], ""
        )
        self.assertNotEqual(
            portable["predecessor"]["superseded_source_identity"],
            portable["source"]["identity"],
        )
        self.assertIn(
            "## Predecessor",
            record["portable_evidence_document"],
        )
        self.assertEqual(
            record["portable_evidence_document"],
            render_portable_evidence(portable),
        )

    def test_compatible_predecessor_still_uses_the_persisted_envelope(
        self,
    ) -> None:
        """A compatible predecessor is one authority with the current plan."""
        apply_workflow(self.plan(operation="fresh-install"))
        exit_code, raw = self.cli(
            [
                "resume-install",
                str(REPO_ROOT),
                str(self.install_root),
                "--client",
                "codex",
                "--portable-evidence",
            ]
        )
        self.assertEqual(exit_code, 0)
        record = json.loads(raw)
        portable = record["portable_evidence"]
        self.assertEqual(
            portable["source"]["identity"],
            record["resume"]["current_run"]["source_identity"],
        )
        self.assertEqual(portable["predecessor"]["authority"], "same-source")
        self.assertEqual(
            portable["predecessor"]["superseded_source_identity"], ""
        )

    def test_current_only_evidence_is_consistent_with_its_own_plan(
        self,
    ) -> None:
        """A wholly current record's remaining work matches its own source."""
        apply_workflow(self.plan(operation="fresh-install"))
        alternate = self.alternate_source()
        plan = self.plan(source_root=alternate)
        assessment = plan["internal"]["resume_assessment"]
        current = build_envelope(
            record=plan,
            surface_profiles=surface_retry_profiles(),
            projection=stable_projection(plan),
        )
        portable = portable_evidence(current, assessment=assessment)
        self.assertEqual(
            portable["source"]["identity"],
            assessment["current_run"]["source_identity"],
        )
        self.assertEqual(
            [item["surface"] for item in portable["remaining_work"]],
            [item["surface"] for item in assessment["remaining_plan"]],
        )


class SourceBoundMigrationDeferralTests(ReworkTestCase):
    def _govern_stale_project(self) -> Path:
        governed = self.root / "governed"
        governed.mkdir()
        config = governed / "cartopian.toml"
        config.write_text(
            '[project]\nproject_schema_version = "v0.1.0"\n', encoding="utf-8"
        )
        (self.install_root / "projects.json").write_text(
            json.dumps([{"id": "governed", "path": str(governed)}]) + "\n",
            encoding="utf-8",
        )
        return governed

    def test_a_deferral_does_not_cross_into_a_changed_source(self) -> None:
        """The prior answer belongs to the source that asked the question."""
        apply_workflow(self.plan(operation="fresh-install"))
        self._govern_stale_project()
        apply_workflow(self.plan(decisions={_MIGRATION_SURFACE: "defer"}))

        carried = self.plan()
        self.assertEqual(
            [item["choice_state"] for item in carried["migrations"]],
            ["deferred"],
        )

        alternate = self.alternate_source()
        crossed = self.plan(source_root=alternate)
        self.assertEqual(
            [item["choice_state"] for item in crossed["migrations"]],
            ["offered"],
            "a migration deferral crossed into a changed source",
        )
        migration_surface = next(
            item
            for item in crossed["surfaces"]
            if item["kind"] == _MIGRATION_SURFACE
        )
        self.assertEqual(migration_surface["state"], "offered")
        self.assertEqual(
            [
                item
                for item in crossed["choices"]
                if item["surface"] == _MIGRATION_SURFACE
            ],
            [],
        )
        assessment = crossed["internal"]["resume_assessment"]
        self.assertEqual(assessment["preserved_migrations"], [])
        self.assertIn(
            _MIGRATION_SURFACE,
            {item["surface"] for item in assessment["remaining_plan"]},
        )

    def test_a_deferral_does_not_survive_a_materially_changed_offer(
        self,
    ) -> None:
        """A changed offer re-asks rather than inheriting the old answer."""
        apply_workflow(self.plan(operation="fresh-install"))
        governed = self._govern_stale_project()
        apply_workflow(self.plan(decisions={_MIGRATION_SURFACE: "defer"}))

        second = self.root / "second-project"
        second.mkdir()
        (second / "cartopian.toml").write_text(
            '[project]\nproject_schema_version = "v0.1.0"\n', encoding="utf-8"
        )
        (self.install_root / "projects.json").write_text(
            json.dumps(
                [
                    {"id": "governed", "path": str(governed)},
                    {"id": "second", "path": str(second)},
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        reoffered = self.plan()
        self.assertEqual(
            sorted(
                item["choice_state"] for item in reoffered["migrations"]
            ),
            ["offered", "offered"],
            "a stale deferral survived a materially changed offer set",
        )

    def test_a_matching_offer_from_the_same_source_still_carries(self) -> None:
        """Source binding must not break the legitimate reuse path."""
        apply_workflow(self.plan(operation="fresh-install"))
        self._govern_stale_project()
        apply_workflow(self.plan(decisions={_MIGRATION_SURFACE: "defer"}))
        carried = self.plan()
        choice = next(
            item
            for item in carried["choices"]
            if item["surface"] == _MIGRATION_SURFACE
        )
        self.assertEqual(choice["state"], "deferred")
        self.assertEqual(choice["provenance"], "prior-run-matched-deferral")
        self.assertEqual(
            [
                item["project_identity"]
                for item in carried["internal"]["resume_assessment"][
                    "preserved_migrations"
                ]
            ],
            ["governed"],
        )


class StalePlanResumeRaceTests(ReworkTestCase):
    """A plan computed before an interruption may not consume it.

    The window is real and needs no threads to reach: a plan reads progress
    once, and everything it concluded about resume is fixed at that moment.
    Another invocation can then persist an open mutation boundary and die
    before the first one acquires the lease.  The stale plan takes over the
    orphan, and the record it is about to replace describes work it never saw.
    """

    def _publish_interrupted_run(self) -> bytes:
        """Crash a same-source run mid-mutation and return its record bytes."""
        crash = self.crash_mid_mutation()
        self.assertEqual(
            crash.returncode, 70, f"the crash fixture did not crash: {crash}"
        )
        return (self.install_root / PROGRESS_FILE).read_bytes()

    def test_a_stale_plan_cannot_erase_a_newly_published_open_boundary(
        self,
    ) -> None:
        # 1. Plan against absent progress.  Nothing is uncertain yet, and the
        #    plan's own assessment will never learn otherwise.
        self.assertEqual(
            read_progress(self.install_root)["classification"], "absent"
        )
        stale = self.plan()
        planned = stale["internal"]["resume_assessment"]
        self.assertEqual(planned["compatibility"], "absent")
        self.assertEqual(planned["uncertain"], [])

        # 2. Publish an interrupted same-source run: a durable open mutation
        #    boundary under a different run identity, plus an orphaned lease.
        interrupted = self._publish_interrupted_run()
        envelope = json.loads(interrupted.decode("utf-8"))
        self.assertEqual(envelope["status"], "active")
        boundary_surface = envelope["boundary"]["surface"]
        self.assertEqual(envelope["boundary"]["mutation_status"], "intended")
        self.assertEqual(
            envelope["run"]["source"]["value"],
            stale["run"]["source"]["value"],
            "the interrupted run must share this plan's source",
        )
        self.assertNotEqual(
            envelope["run"]["marker"],
            stale["run"]["marker"],
            "the interrupted run must be a different run identity",
        )
        self.assertEqual(
            read_progress(self.install_root)["lease_state"], "orphaned"
        )

        # 3. Apply the stale plan.  It takes over the orphan lease, and every
        #    gate must run again against the record it is about to replace.
        with self.assertRaises(WorkflowRefusal) as caught:
            apply_workflow(stale)
        message = str(caught.exception)
        self.assertIn(boundary_surface, message)
        self.assertIn("inspected before retry", message)

        # 4. Refusal preserves the interrupted envelope byte-for-byte: it is
        #    neither replaced, quarantined, nor consumed, and the lease this
        #    run took over is released.
        self.assertEqual(
            (self.install_root / PROGRESS_FILE).read_bytes(),
            interrupted,
            "the stale plan erased another run's open mutation boundary",
        )
        self.assertFalse((self.install_root / QUARANTINE_FILE).exists())
        self.assertIsNone(read_lease(self.install_root))

        # Refusal is not a one-shot: the same stale plan keeps refusing until
        # the newly observed boundary is explicitly inspected.
        with self.assertRaises(WorkflowRefusal):
            apply_workflow(stale)
        self.assertEqual(
            (self.install_root / PROGRESS_FILE).read_bytes(), interrupted
        )

        # Explicit inspection is what unblocks it, and even then the
        # interrupted envelope is preserved verbatim rather than erased.
        (self.client_home / ".codex").mkdir(parents=True, exist_ok=True)
        self.drift_client_config()
        apply_workflow(stale, inspected=(boundary_surface,))
        preserved = self.install_root / QUARANTINE_FILE
        self.assertTrue(
            preserved.exists(), "the inspected predecessor was destroyed"
        )
        self.assertEqual(preserved.read_bytes(), interrupted)
        recovery = self.envelope()["recovery"]
        self.assertEqual(recovery["preserved_classification"], "run-conflict")
        self.assertEqual(recovery["quarantine"], QUARANTINE_FILE)

    def test_the_post_lease_assessment_sees_what_the_plan_could_not(
        self,
    ) -> None:
        """The refusal comes from the reread record, not the plan snapshot."""
        stale = self.plan()
        self.assertEqual(
            stale["internal"]["resume_assessment"]["compatibility"], "absent"
        )
        interrupted = self._publish_interrupted_run()
        held = read_progress(self.install_root)
        recomputed = resume_state.assess_resume(
            prior=held,
            current=_current_resume_facts(
                stale, install_root=self.install_root
            ),
            profiles=surface_retry_profiles(),
        )
        self.assertEqual(recomputed["compatibility"], "run-conflict")
        self.assertIn(
            json.loads(interrupted.decode("utf-8"))["boundary"]["surface"],
            {item["surface"] for item in recomputed["uncertain"]},
        )

    def test_an_uninterrupted_root_is_unaffected_by_the_post_lease_gate(
        self,
    ) -> None:
        """The gate refuses discovered uncertainty, not ordinary applies."""
        applied = apply_workflow(self.plan(operation="fresh-install"))
        self.assertIn(
            applied["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertEqual(self.envelope()["recovery"]["quarantine"], "")
        # A second ordinary run over the same root still applies cleanly, and
        # nothing about its own lease reads as a prior run's orphan.
        second = apply_workflow(self.plan())
        self.assertIn(
            second["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertEqual(
            self.envelope()["recovery"]["classification"], "compatible"
        )
        self.assertFalse((self.install_root / QUARANTINE_FILE).exists())


if __name__ == "__main__":
    unittest.main()
