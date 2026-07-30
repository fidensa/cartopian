"""Acceptance coverage for bounded, resumable install/update progress.

Each test names the behavior it protects: what survives an interruption, what
must be inspected rather than replayed, and what must never leak into portable
evidence.  Crash fixtures run in a subprocess and die with ``os._exit`` so the
persisted record is exactly what a real power loss would leave behind.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import OrderedDict
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import cli.resume_state as resume_state
from cli.install_state import SURFACE_KINDS, stable_projection
from cli.install_workflow import (
    STATE_FILE,
    WorkflowRefusal,
    apply_workflow,
    plan_workflow,
    surface_retry_profile,
    surface_retry_profiles,
)
from cli.main import main as cli_main
from cli.resume_state import (
    LEASE_FILE,
    PROGRESS_FILE,
    PROGRESS_SCHEMA_IDENTITY,
    PROGRESS_SCHEMA_VERSION,
    QUARANTINE_FILE,
    ProgressRefusal,
    acquire_lease,
    assess_resume,
    new_owner_token,
    portable_evidence,
    portable_evidence_diagnostics,
    progress_contract,
    read_progress,
    render_portable_evidence,
)
from mcp_server import server

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads(
    (
        REPO_ROOT / "tests" / "fixtures" / "resume_state" / "scenarios.json"
    ).read_text(encoding="utf-8")
)

# A real crash: the mutation boundary is already durable, then the process dies
# without unwinding, so no checkpoint, marker, or lease release can follow.
_CRASH_SCRIPT = """
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import cli.install_workflow as workflow

workflow._replace_tool_path = lambda *args, **kwargs: os._exit(70)
plan = workflow.plan_workflow(
    source_root=Path(sys.argv[1]),
    install_root=Path(sys.argv[2]),
    operation="fresh-install",
    mode="copy",
    client_home=Path(sys.argv[3]),
    clients=("codex",),
)
workflow.apply_workflow(plan)
"""


class ResumableProgressTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install_root = self.root / "install"
        self.client_home = self.root / "home"
        self.client_home.mkdir()

    def plan(self, **overrides):
        values = {
            "source_root": REPO_ROOT,
            "install_root": self.install_root,
            "operation": "update",
            "mode": "copy",
            "client_home": self.client_home,
            "clients": ("codex",),
        }
        values.update(overrides)
        return plan_workflow(**values)

    def envelope(self) -> dict:
        return json.loads(
            (self.install_root / PROGRESS_FILE).read_text(encoding="utf-8")
        )

    def write_envelope(self, envelope) -> None:
        resume_state.recoverable_write_text(
            self.install_root / PROGRESS_FILE,
            json.dumps(envelope, indent=2, ensure_ascii=False) + "\n",
        )

    def assessment(self, **overrides):
        return self.plan(**overrides)["internal"]["resume_assessment"]

    def crash_mid_mutation(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                _CRASH_SCRIPT,
                str(REPO_ROOT),
                str(self.install_root),
                str(self.client_home),
            ],
            capture_output=True,
            text=True,
        )


class SurfaceProfileTests(ResumableProgressTestCase):
    def test_every_surface_declares_retry_safety_and_observability(self) -> None:
        expected = SCENARIOS["surface_retry_profiles"]
        self.assertEqual(sorted(expected), sorted(SURFACE_KINDS))
        self.assertEqual(
            [item["surface"] for item in surface_retry_profiles()],
            list(SURFACE_KINDS),
        )
        for kind in SURFACE_KINDS:
            retry, observation = expected[kind]
            with self.subTest(surface=kind):
                self.assertEqual(
                    surface_retry_profile(kind),
                    {
                        "surface": kind,
                        "retry_safety": retry,
                        "observation": observation,
                    },
                )

    def test_unknown_surface_has_no_profile(self) -> None:
        with self.assertRaises(WorkflowRefusal):
            surface_retry_profile("invented-surface")

    def test_progress_contract_is_deterministic_and_closed(self) -> None:
        first = progress_contract()
        self.assertEqual(json.dumps(first), json.dumps(progress_contract()))
        self.assertEqual(
            first["progress_schema_identity"], PROGRESS_SCHEMA_IDENTITY
        )
        self.assertEqual(
            first["progress_schema_version"], PROGRESS_SCHEMA_VERSION
        )
        self.assertEqual(
            first["recovery_by_classification"],
            SCENARIOS["recovery_by_classification"],
        )
        self.assertEqual(
            sorted(first["recovery_by_classification"]),
            sorted(first["vocabularies"]["compatibility_states"]),
        )

    def test_every_declared_vocabulary_member_has_a_producer(self) -> None:
        """No vocabulary member exists that nothing can ever emit."""
        vocabularies = progress_contract()["vocabularies"]
        emitted = {name: set() for name in vocabularies}

        apply_workflow(self.plan(operation="fresh-install"))
        terminal_envelope = self.envelope()
        emitted["progress_statuses"].add(terminal_envelope["status"])
        emitted["retention_classes"].add(
            terminal_envelope["retention"]["class"]
        )
        emitted["marker_states"].update(terminal_envelope["terminal"].values())
        for profile in terminal_envelope["surface_profiles"]:
            emitted["observation_capabilities"].add(profile["observation"])

        # A parseable-but-unusable record is relabelled when quarantined. The
        # drifted client config keeps a repair offered, so the recovering run
        # never reaches the terminal proof that would supersede the quarantine.
        (self.client_home / ".codex" / "config.toml").write_text(
            '[mcp_servers.cartopian]\ncommand = "/drifted"\n', encoding="utf-8"
        )
        broken = self.envelope()
        broken["progress"]["checkpoints"][0]["evidence"] = {}
        self.write_envelope(broken)
        apply_workflow(self.plan())
        quarantined = json.loads(
            (self.install_root / QUARANTINE_FILE).read_text(encoding="utf-8")
        )
        emitted["progress_statuses"].add(quarantined["status"])
        emitted["retention_classes"].add(quarantined["retention"]["class"])

        active = self.envelope()
        emitted["progress_statuses"].add(active["status"])
        emitted["retention_classes"].add(active["retention"]["class"])
        emitted["marker_states"].update(active["terminal"].values())

        emitted["recovery_actions"].update(
            action
            for actions in progress_contract()[
                "recovery_by_classification"
            ].values()
            for action in actions
        )
        emitted["compatibility_states"].update(
            progress_contract()["recovery_by_classification"]
        )
        emitted["surface_diagnoses"].update(
            resume_state._DIAGNOSIS_BY_SURFACE_STATE.values()
        )
        emitted["resume_dispositions"].update(
            {"reuse-verified", "preserve-choice", "replan"}
        )
        emitted["resume_dispositions"].update(
            {"inspect-before-retry", "refuse-replay"}
        )

        for name, declared in vocabularies.items():
            with self.subTest(vocabulary=name):
                self.assertEqual(
                    sorted(set(declared)), sorted(set(declared) | emitted[name])
                )
                self.assertEqual(
                    sorted(emitted[name] - set(declared)),
                    [],
                    f"{name} emitted a value outside its vocabulary",
                )
                self.assertEqual(sorted(set(declared) - emitted[name]), [])

    def test_persistence_boundary_is_standard_library_only(self) -> None:
        source = (REPO_ROOT / "cli" / "resume_state.py").read_text(
            encoding="utf-8"
        )
        imported = set(
            re.findall(r"^(?:from|import)\s+([A-Za-z_][\w.]*)", source, re.M)
        )
        for module in sorted(imported):
            top = module.split(".")[0]
            with self.subTest(module=module):
                self.assertIn(
                    top,
                    {
                        "__future__",
                        "cli",
                        "collections",
                        "copy",
                        "hashlib",
                        "json",
                        "os",
                        "pathlib",
                        "platform",
                        "re",
                        "stat",
                        "tempfile",
                        "typing",
                        "uuid",
                    },
                )


class VerifiedPartialResumeTests(ResumableProgressTestCase):
    def test_verified_partial_run_resumes_only_unfinished_surfaces(self) -> None:
        scenario = SCENARIOS["verified-partial-resume"]
        apply_workflow(self.plan(operation="fresh-install"))
        drifted = self.install_root.joinpath(*scenario["drifted_path"])
        drifted.write_text("drift\n", encoding="utf-8")
        core_before = (self.install_root / "cli" / "main.py").read_bytes()

        assessment = self.assessment()
        self.assertEqual(
            assessment["compatibility"], scenario["expected_compatibility"]
        )
        self.assertEqual(
            [item["surface"] for item in assessment["reusable"]],
            scenario["expected_reused"],
        )
        self.assertEqual(
            [
                [item["surface"], item["reason"]]
                for item in assessment["incompatible"]
            ],
            scenario["expected_incompatible"],
        )
        self.assertEqual(
            [
                [item["surface"], item["action"], item["disposition"]]
                for item in assessment["remaining_plan"]
            ],
            scenario["expected_remaining"],
        )
        self.assertEqual(
            {
                item["surface"]: item["diagnosis"]
                for item in assessment["surface_diagnosis"]
            },
            scenario["expected_diagnosis"],
        )
        self.assertEqual(assessment["uncertain"], [])

        result = apply_workflow(self.plan())
        self.assertIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertEqual(
            (self.install_root / "cli" / "main.py").read_bytes(), core_before
        )
        self.assertNotEqual(drifted.read_text(encoding="utf-8"), "drift\n")

    def test_equivalent_progress_and_observations_are_byte_stable(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        apply_workflow(self.plan())
        first_progress = (self.install_root / PROGRESS_FILE).read_bytes()
        first_state = (self.install_root / STATE_FILE).read_bytes()
        first_plan = json.dumps(self.assessment())

        apply_workflow(self.plan())
        self.assertEqual(
            (self.install_root / PROGRESS_FILE).read_bytes(), first_progress
        )
        self.assertEqual(
            (self.install_root / STATE_FILE).read_bytes(), first_state
        )
        second_plan = json.dumps(self.assessment())
        self.assertEqual(first_plan, second_plan)

        assessment = self.assessment()
        self.assertEqual(assessment["compatibility"], "compatible")
        self.assertTrue(assessment["byte_stable_noop"])
        self.assertEqual(assessment["remaining_plan"], [])
        self.assertEqual(
            len(assessment["reusable"]), len(SURFACE_KINDS)
        )

    def test_terminal_markers_advance_only_after_verified_work(self) -> None:
        result = apply_workflow(self.plan(operation="fresh-install"))
        envelope = self.envelope()
        self.assertEqual(
            envelope["terminal"],
            {
                "schema": "advanced",
                "completion": "advanced",
                "cleanup": "advanced",
            },
        )
        self.assertEqual(envelope["status"], "terminal")
        self.assertEqual(envelope["retention"]["class"], "terminal")
        self.assertTrue(envelope["retention"]["evidence_retained"])
        self.assertIsNone(envelope["boundary"])
        self.assertFalse((self.install_root / LEASE_FILE).exists())
        for checkpoint in envelope["progress"]["checkpoints"]:
            with self.subTest(checkpoint=checkpoint["id"]):
                self.assertEqual(checkpoint["status"], "completed")
                self.assertEqual(checkpoint["verification"], "verified")
                self.assertTrue(checkpoint["evidence"]["identity"])
        self.assertEqual(
            envelope["progress"]["outcome"]["status"],
            result["outcome"]["status"],
        )

    def test_offered_repair_leaves_terminal_markers_pending(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        config = self.client_home / ".codex" / "config.toml"
        config.write_text(
            '[mcp_servers.cartopian]\ncommand = "/drifted"\n', encoding="utf-8"
        )
        apply_workflow(self.plan())
        envelope = self.envelope()
        self.assertEqual(envelope["status"], "active")
        self.assertEqual(
            envelope["terminal"],
            {
                "schema": "pending",
                "completion": "pending",
                "cleanup": "pending",
            },
        )
        self.assertIn(
            "unresolved work remains", envelope["recovery"]["detail"]
        )


class CrashWindowTests(ResumableProgressTestCase):
    def test_crash_between_mutation_and_checkpoint_is_uncertain(self) -> None:
        scenario = SCENARIOS["crash-window"]
        crashed = self.crash_mid_mutation()
        self.assertEqual(crashed.returncode, scenario["exit_code"])

        envelope = self.envelope()
        self.assertEqual(envelope["status"], "active")
        self.assertEqual(envelope["terminal"], scenario["expected_terminal"])
        for field, value in scenario["expected_boundary"].items():
            with self.subTest(field=field):
                self.assertEqual(envelope["boundary"][field], value)
        open_checkpoint = next(
            item
            for item in envelope["progress"]["checkpoints"]
            if item["surface"] == scenario["expected_boundary"]["surface"]
        )
        self.assertEqual(
            open_checkpoint["status"], scenario["expected_checkpoint_status"]
        )
        self.assertEqual(
            open_checkpoint["retry_safety"], "inspect-before-retry"
        )
        self.assertNotEqual(open_checkpoint["status"], "completed")

        prior = read_progress(self.install_root)
        self.assertEqual(prior["classification"], "compatible")
        self.assertEqual(
            prior["lease_state"], scenario["expected_lease_state"]
        )

        assessment = self.assessment(operation="fresh-install")
        self.assertEqual(
            assessment["compatibility"], scenario["expected_compatibility"]
        )
        self.assertEqual(
            [
                [item["surface"], item["disposition"]]
                for item in assessment["uncertain"]
            ],
            scenario["expected_uncertain"],
        )
        inspect_entry = next(
            item
            for item in assessment["remaining_plan"]
            if item["surface"] == scenario["expected_boundary"]["surface"]
        )
        self.assertEqual(inspect_entry["action"], "inspect")
        self.assertEqual(
            inspect_entry["disposition"], "inspect-before-retry"
        )

    def test_uncertain_boundary_is_refused_rather_than_replayed(self) -> None:
        self.crash_mid_mutation()
        plan = self.plan(operation="fresh-install")
        with self.assertRaises(WorkflowRefusal) as caught:
            apply_workflow(plan)
        message = str(caught.exception)
        self.assertIn("core-files", message)
        self.assertIn("uncertain", message)
        self.assertIn("inspected", message)
        self.assertEqual(
            self.envelope()["boundary"]["mutation_status"], "intended"
        )

        resumed = apply_workflow(
            self.plan(operation="fresh-install"), inspected=("core-files",)
        )
        self.assertIn(
            resumed["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertEqual(self.envelope()["terminal"]["completion"], "advanced")

    def test_inspected_surface_must_be_a_closed_surface_kind(self) -> None:
        self.crash_mid_mutation()
        with self.assertRaises(WorkflowRefusal):
            apply_workflow(
                self.plan(operation="fresh-install"),
                inspected=("../../etc/passwd",),
            )

    def test_non_repeatable_work_is_refused_even_after_inspection(self) -> None:
        profile = surface_retry_profile("project-schema-migration-offers")
        self.assertEqual(profile["retry_safety"], "refuse-replay")
        plan = self.plan(operation="fresh-install")
        plan["internal"]["resume_assessment"]["uncertain"] = [
            {
                "checkpoint": "verify-project-schema-migration-offers",
                "surface": "project-schema-migration-offers",
                "disposition": "refuse-replay",
                "observation": "unobservable",
                "attempted_action": "offer-migration",
                "detail": "prior migration boundary is not repeatable",
            }
        ]
        with self.assertRaises(WorkflowRefusal) as caught:
            apply_workflow(
                plan, inspected=("project-schema-migration-offers",)
            )
        self.assertIn("not repeatable", str(caught.exception))
        self.assertFalse((self.install_root / PROGRESS_FILE).exists())

    def test_bounded_refusal_closes_its_boundary_and_stays_resumable(
        self,
    ) -> None:
        config = self.client_home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text("[broken\n", encoding="utf-8")
        with self.assertRaises(WorkflowRefusal):
            apply_workflow(self.plan(operation="fresh-install"))
        envelope = self.envelope()
        # Nothing was mutated, so nothing is uncertain and a retry is allowed.
        self.assertIsNone(envelope["boundary"])
        self.assertEqual(envelope["terminal"]["completion"], "pending")
        self.assertEqual(self.assessment()["uncertain"], [])
        config.write_text("", encoding="utf-8")
        recovered = apply_workflow(
            self.plan(operation="fresh-install", decisions={"bridges": "accept"})
        )
        self.assertNotEqual(recovered["outcome"]["status"], "failed")


class IncompatibleProgressTests(ResumableProgressTestCase):
    def test_changed_source_identity_blocks_reuse(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
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
        assessment = self.assessment(source_root=alternate)
        self.assertEqual(assessment["compatibility"], "source-mismatch")
        self.assertEqual(assessment["reusable"], [])
        self.assertEqual(
            len(assessment["incompatible"]), len(SURFACE_KINDS)
        )
        self.assertEqual(
            assessment["recovery"]["actions"],
            SCENARIOS["recovery_by_classification"]["source-mismatch"],
        )
        self.assertEqual(assessment["preserved_choices"], [])
        self.assertFalse(assessment["byte_stable_noop"])

    def test_conflicting_run_identity_with_open_boundary_blocks_reuse(
        self,
    ) -> None:
        self.crash_mid_mutation()
        envelope = self.envelope()
        envelope["run"]["marker"] = "run:0000000000000000conflict"
        self.write_envelope(envelope)
        assessment = self.assessment(operation="fresh-install")
        self.assertEqual(assessment["compatibility"], "run-conflict")
        self.assertEqual(assessment["reusable"], [])
        self.assertEqual(
            assessment["recovery"]["actions"],
            SCENARIOS["recovery_by_classification"]["run-conflict"],
        )
        self.assertEqual(
            [item["surface"] for item in assessment["uncertain"]],
            ["core-files"],
        )

    def test_truncated_and_malformed_progress_fails_closed(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        good = (self.install_root / PROGRESS_FILE).read_bytes()
        for label, payload in (
            ("truncated", good[: len(good) // 2]),
            ("not-json", b"{not-json\n"),
            ("not-object", b"[]\n"),
        ):
            with self.subTest(corruption=label):
                (self.install_root / PROGRESS_FILE).write_bytes(payload)
                prior = read_progress(self.install_root)
                self.assertEqual(prior["classification"], "corrupted")
                self.assertIsNone(prior["envelope"])
                assessment = self.assessment()
                self.assertEqual(assessment["compatibility"], "corrupted")
                self.assertEqual(assessment["reusable"], [])
                self.assertEqual(
                    assessment["recovery"]["actions"],
                    SCENARIOS["recovery_by_classification"]["corrupted"],
                )

    def test_corrupted_progress_is_quarantined_before_reuse(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        good = (self.install_root / PROGRESS_FILE).read_bytes()
        corrupt = good[: len(good) // 2]
        (self.install_root / PROGRESS_FILE).write_bytes(corrupt)
        config = self.client_home / ".codex" / "config.toml"
        config.write_text(
            '[mcp_servers.cartopian]\ncommand = "/drifted"\n', encoding="utf-8"
        )

        apply_workflow(self.plan())
        quarantine = self.install_root / QUARANTINE_FILE
        # The run did not reach terminal proof, so the quarantined evidence is
        # still the only record of the failure and must survive.
        self.assertTrue(quarantine.exists())
        self.assertEqual(quarantine.read_bytes(), corrupt)
        envelope = self.envelope()
        self.assertEqual(envelope["recovery"]["quarantine"], QUARANTINE_FILE)
        self.assertTrue(envelope["recovery"]["quarantined_identity"])

    def test_terminal_proof_supersedes_quarantine_but_keeps_its_identity(
        self,
    ) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        good = (self.install_root / PROGRESS_FILE).read_bytes()
        (self.install_root / PROGRESS_FILE).write_bytes(
            good[: len(good) // 2]
        )
        apply_workflow(self.plan())
        envelope = self.envelope()
        self.assertEqual(envelope["terminal"]["cleanup"], "advanced")
        self.assertFalse((self.install_root / QUARANTINE_FILE).exists())
        self.assertEqual(envelope["recovery"]["quarantine"], "")
        self.assertTrue(
            envelope["recovery"]["quarantined_identity"].startswith("sha256:")
        )
        self.assertIn(QUARANTINE_FILE, envelope["recovery"]["detail"])

    def test_symlinked_progress_record_is_never_followed(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        elsewhere = self.root / "elsewhere.json"
        elsewhere.write_text(
            (self.install_root / PROGRESS_FILE).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (self.install_root / PROGRESS_FILE).unlink()
        (self.install_root / PROGRESS_FILE).symlink_to(elsewhere)
        prior = read_progress(self.install_root)
        self.assertEqual(prior["classification"], "corrupted")
        self.assertIn("symlink", prior["detail"])

    def test_unsupported_newer_progress_is_refused_and_preserved(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        envelope = self.envelope()
        envelope["progress_schema_version"] = PROGRESS_SCHEMA_VERSION + 1
        self.write_envelope(envelope)
        preserved = (self.install_root / PROGRESS_FILE).read_bytes()

        prior = read_progress(self.install_root)
        self.assertEqual(prior["classification"], "unsupported-newer")
        assessment = self.assessment()
        self.assertEqual(assessment["compatibility"], "unsupported-newer")
        self.assertEqual(assessment["reusable"], [])
        self.assertEqual(
            assessment["recovery"]["actions"],
            SCENARIOS["recovery_by_classification"]["unsupported-newer"],
        )
        with self.assertRaises(WorkflowRefusal) as caught:
            apply_workflow(self.plan())
        self.assertIn("newer schema", str(caught.exception))
        self.assertEqual(
            (self.install_root / PROGRESS_FILE).read_bytes(), preserved
        )
        self.assertFalse((self.install_root / QUARANTINE_FILE).exists())

    def test_unsupported_newer_record_schema_is_refused(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        envelope = self.envelope()
        envelope["progress"]["record_schema_version"] = 99
        self.write_envelope(envelope)
        self.assertEqual(
            read_progress(self.install_root)["classification"],
            "unsupported-newer",
        )

    def test_missing_checkpoint_evidence_fails_closed(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        envelope = self.envelope()
        envelope["progress"]["checkpoints"][0]["evidence"] = {}
        self.write_envelope(envelope)
        prior = read_progress(self.install_root)
        self.assertEqual(prior["classification"], "evidence-missing")
        self.assertIn(
            envelope["progress"]["checkpoints"][0]["id"], prior["detail"]
        )
        assessment = self.assessment()
        self.assertEqual(assessment["compatibility"], "evidence-missing")
        self.assertEqual(assessment["reusable"], [])
        self.assertEqual(
            assessment["recovery"]["actions"],
            SCENARIOS["recovery_by_classification"]["evidence-missing"],
        )

    def test_unverified_evidence_cannot_pass_as_completed(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        envelope = self.envelope()
        envelope["progress"]["checkpoints"][0]["evidence"][
            "verification"
        ] = "unverified"
        self.write_envelope(envelope)
        self.assertEqual(
            read_progress(self.install_root)["classification"],
            "evidence-missing",
        )

    def test_orphaned_progress_without_installed_content_fails_closed(
        self,
    ) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
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
        assessment = self.assessment()
        self.assertEqual(assessment["compatibility"], "orphaned")
        self.assertEqual(assessment["reusable"], [])
        self.assertEqual(
            assessment["recovery"]["actions"],
            SCENARIOS["recovery_by_classification"]["orphaned"],
        )

    def test_matching_evidence_alone_never_authorizes_reuse(self) -> None:
        """A digest match cannot skip work while the surface is not safe."""
        completed = {
            "id": "verify-core-files",
            "phase": "verify",
            "surface": "core-files",
            "status": "completed",
            "verification": "verified",
            "retry_safety": "idempotent",
            "evidence": {
                "identity": "sha256:same",
                "kind": "file-digest",
                "observed_identity": "sha256:same",
                "observed_state": "verified",
                "verification": "verified",
            },
        }
        prior = OrderedDict(
            (
                ("classification", "compatible"),
                ("detail", "synthetic compatible record"),
                (
                    "envelope",
                    {
                        "status": "terminal",
                        "boundary": None,
                        "run": {
                            "operation": "update",
                            "marker": "run:same",
                            "source": {"value": "sha256:source"},
                        },
                        "progress": {
                            "checkpoints": [completed],
                            "surfaces": [],
                            "choices": [],
                            "migrations": [],
                            "versions": [],
                        },
                    },
                ),
                ("lease", None),
                ("lease_state", "absent"),
            )
        )
        plan_action = {
            "surface": "core-files",
            "action": "install",
            "authorization": "required",
        }
        for state, reusable in (
            ("verified", True),
            ("current", True),
            ("missing", False),
            ("dirty", False),
            ("unverified", False),
            ("blocked", False),
        ):
            with self.subTest(surface_state=state):
                assessment = assess_resume(
                    prior=prior,
                    current={
                        "operation": "update",
                        "marker": "run:same",
                        "source_identity": "sha256:source",
                        "installed_identity": "sha256:installed",
                        "surfaces": [
                            {
                                "kind": "core-files",
                                "state": state,
                                "observed_identity": "sha256:same",
                            }
                        ],
                        "choices": [],
                        "migrations": [],
                        "plan_actions": [plan_action],
                    },
                    profiles=surface_retry_profiles(),
                )
                self.assertEqual(
                    bool(assessment["reusable"]),
                    reusable,
                    f"{state} reuse should be {reusable}",
                )
                if not reusable:
                    self.assertEqual(
                        [
                            item["reason"]
                            for item in assessment["incompatible"]
                        ],
                        ["evidence-superseded"],
                    )
                    self.assertEqual(
                        [
                            item["surface"]
                            for item in assessment["remaining_plan"]
                        ],
                        ["core-files"],
                    )

    def test_absent_progress_with_a_stray_lease_is_orphaned(self) -> None:
        self.install_root.mkdir(parents=True, exist_ok=True)
        acquire_lease(
            self.install_root, run_marker="run:stray", owner=new_owner_token()
        )
        (self.install_root / PROGRESS_FILE).unlink(missing_ok=True)
        prior = read_progress(self.install_root)
        self.assertEqual(prior["classification"], "absent")
        assessment = assess_resume(
            prior=prior,
            current={
                "operation": "update",
                "marker": "run:current",
                "source_identity": "sha256:current",
                "installed_identity": None,
                "surfaces": [],
                "choices": [],
                "migrations": [],
                "plan_actions": [],
            },
            profiles=surface_retry_profiles(),
        )
        self.assertEqual(assessment["compatibility"], "lease-conflict")


class ConcurrentInvocationTests(ResumableProgressTestCase):
    def test_another_runs_lease_blocks_apply_and_evidence_reuse(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        untouched = (self.install_root / PROGRESS_FILE).read_bytes()
        holder = new_owner_token()
        acquire_lease(
            self.install_root, run_marker="run:concurrent-holder", owner=holder
        )
        self.addCleanup(
            lambda: (self.install_root / LEASE_FILE).unlink(missing_ok=True)
        )

        assessment = self.assessment()
        self.assertEqual(assessment["compatibility"], "lease-conflict")
        self.assertEqual(assessment["reusable"], [])
        self.assertEqual(
            assessment["recovery"]["actions"],
            SCENARIOS["recovery_by_classification"]["lease-conflict"],
        )

        with self.assertRaises(WorkflowRefusal) as caught:
            apply_workflow(self.plan())
        self.assertIn("run:concurrent-holder", str(caught.exception))
        self.assertEqual(
            (self.install_root / PROGRESS_FILE).read_bytes(), untouched
        )

    def test_duplicate_invocation_by_the_same_owner_is_reentrant(self) -> None:
        self.install_root.mkdir(parents=True, exist_ok=True)
        owner = new_owner_token()
        first = acquire_lease(
            self.install_root, run_marker="run:same", owner=owner
        )
        second = acquire_lease(
            self.install_root, run_marker="run:same", owner=owner
        )
        self.assertEqual(first["state"], "held")
        self.assertEqual(second["state"], "held")
        with self.assertRaises(ProgressRefusal):
            acquire_lease(
                self.install_root,
                run_marker="run:other",
                owner=new_owner_token(),
            )

    def test_a_lease_is_released_only_by_its_owner(self) -> None:
        self.install_root.mkdir(parents=True, exist_ok=True)
        owner = new_owner_token()
        acquire_lease(self.install_root, run_marker="run:owned", owner=owner)
        resume_state.release_lease(self.install_root, new_owner_token())
        self.assertTrue((self.install_root / LEASE_FILE).exists())
        resume_state.release_lease(self.install_root, owner)
        self.assertFalse((self.install_root / LEASE_FILE).exists())

    def test_a_foreign_lease_cannot_commit_into_this_envelope(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        envelope = self.envelope()
        acquire_lease(
            self.install_root,
            run_marker="run:someone-else",
            owner=new_owner_token(),
        )
        self.addCleanup(
            lambda: (self.install_root / LEASE_FILE).unlink(missing_ok=True)
        )
        with self.assertRaises(ProgressRefusal) as caught:
            resume_state.commit_checkpoint(
                self.install_root,
                envelope,
                checkpoint=envelope["progress"]["checkpoints"][0],
                owner=new_owner_token(),
            )
        self.assertEqual(caught.exception.code, "lease-conflict")

    @unittest.skipIf(
        os.name != "posix", "provable liveness probing is POSIX-only"
    )
    def test_a_crashed_holders_lease_is_taken_over_not_deadlocked(self) -> None:
        crashed = self.crash_mid_mutation()
        self.assertEqual(crashed.returncode, 70)
        self.assertTrue((self.install_root / LEASE_FILE).exists())
        self.assertEqual(
            read_progress(self.install_root)["lease_state"], "orphaned"
        )
        resumed = apply_workflow(
            self.plan(operation="fresh-install"), inspected=("core-files",)
        )
        self.assertIn(
            resumed["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertFalse((self.install_root / LEASE_FILE).exists())


class PersistenceFailureTests(ResumableProgressTestCase):
    def test_permission_failure_cannot_advance_completion_markers(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        config = self.client_home / ".codex" / "config.toml"
        config.write_text(
            '[mcp_servers.cartopian]\ncommand = "/drifted"\n', encoding="utf-8"
        )
        apply_workflow(
            self.plan(decisions={"client-registrations": "accept"})
        )
        stable_before = (self.install_root / STATE_FILE).read_bytes()
        real = resume_state.recoverable_write_text

        def deny_marker_write(path, content):
            payload = json.loads(content)
            if payload.get("terminal", {}).get("schema") == "advanced":
                raise PermissionError("simulated disk exhaustion")
            return real(path, content)

        with patch(
            "cli.resume_state.recoverable_write_text",
            side_effect=deny_marker_write,
        ):
            with self.assertRaises(WorkflowRefusal) as caught:
                apply_workflow(self.plan())
        self.assertIn("could not be persisted", str(caught.exception))
        envelope = self.envelope()
        self.assertEqual(
            envelope["terminal"],
            {
                "schema": "pending",
                "completion": "pending",
                "cleanup": "pending",
            },
        )
        self.assertNotEqual(envelope["status"], "terminal")
        # The visible mirror publishes only inside the marker sequence, so it
        # cannot record a completion the progress record never reached.
        self.assertEqual(
            (self.install_root / STATE_FILE).read_bytes(), stable_before
        )

    def test_visible_mirror_failure_leaves_cleanup_pending(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        with patch(
            "cli.install_workflow._write_state",
            side_effect=PermissionError("read-only filesystem"),
        ):
            with self.assertRaises(WorkflowRefusal) as caught:
                apply_workflow(self.plan())
        self.assertIn("could not be persisted", str(caught.exception))
        envelope = self.envelope()
        self.assertEqual(envelope["terminal"]["cleanup"], "pending")
        self.assertEqual(
            read_progress(self.install_root)["classification"], "compatible"
        )

    def test_checkpoint_evidence_failure_cannot_be_committed(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        envelope = self.envelope()
        owner = new_owner_token()
        acquire_lease(
            self.install_root,
            run_marker=envelope["run"]["marker"],
            owner=owner,
        )
        self.addCleanup(
            lambda: resume_state.release_lease(self.install_root, owner)
        )
        checkpoint = dict(envelope["progress"]["checkpoints"][0])
        checkpoint["evidence"] = {}
        with self.assertRaises(ProgressRefusal) as caught:
            resume_state.commit_checkpoint(
                self.install_root, envelope, checkpoint=checkpoint, owner=owner
            )
        self.assertEqual(caught.exception.code, "checkpoint-evidence-missing")

        forbidden = dict(envelope["progress"]["checkpoints"][0])
        forbidden["evidence"] = {
            **forbidden["evidence"],
            "task_id": "REDACTED-GOVERNANCE-PROBE",
        }
        with self.assertRaises(ProgressRefusal) as caught:
            resume_state.commit_checkpoint(
                self.install_root, envelope, checkpoint=forbidden, owner=owner
            )
        self.assertEqual(caught.exception.code, "checkpoint-evidence-forbidden")

    def test_cleanup_cannot_advance_before_completion(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        envelope = self.envelope()
        envelope["terminal"]["completion"] = "pending"
        with self.assertRaises(ProgressRefusal) as caught:
            resume_state.advance_cleanup(
                self.install_root, envelope, owner=new_owner_token()
            )
        self.assertEqual(caught.exception.code, "cleanup-before-completion")


class PreservedChoiceTests(ResumableProgressTestCase):
    def test_repair_decline_is_preserved_only_while_facts_are_unchanged(
        self,
    ) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        bridge = self.client_home / ".codex" / "skills" / "use-cartopian"
        bridge.mkdir(parents=True, exist_ok=True)
        (bridge / "SKILL.md").write_text("operator edit\n", encoding="utf-8")
        apply_workflow(self.plan(decisions={"bridges": "decline"}))

        assessment = self.assessment()
        self.assertEqual(
            [item["surface"] for item in assessment["preserved_choices"]],
            ["bridges"],
        )
        self.assertNotIn(
            "bridges",
            {item["surface"] for item in assessment["remaining_plan"]},
        )
        carried = next(
            item
            for item in self.plan()["choices"]
            if item["surface"] == "bridges"
        )
        self.assertEqual(carried["state"], "declined")
        self.assertEqual(carried["provenance"], "prior-run-matched-decline")
        self.assertEqual(
            (bridge / "SKILL.md").read_text(encoding="utf-8"), "operator edit\n"
        )

        (bridge / "SKILL.md").write_text("changed again\n", encoding="utf-8")
        reoffered = self.plan()
        offered = next(
            item for item in reoffered["choices"] if item["surface"] == "bridges"
        )
        self.assertEqual(offered["state"], "offered")
        self.assertEqual(
            reoffered["internal"]["resume_assessment"]["preserved_choices"], []
        )

    def test_migration_deferral_is_preserved_only_while_offer_is_unchanged(
        self,
    ) -> None:
        scenario = SCENARIOS["migration-deferral"]
        apply_workflow(self.plan(operation="fresh-install"))
        governed = self.root / "governed"
        governed.mkdir()
        config = governed / "cartopian.toml"
        config.write_text(
            f'[project]\nproject_schema_version = "{scenario["stale_schema"]}"\n',
            encoding="utf-8",
        )
        (self.install_root / "projects.json").write_text(
            json.dumps([{"id": "governed", "path": str(governed)}]) + "\n",
            encoding="utf-8",
        )

        deferred = apply_workflow(
            self.plan(
                decisions={"project-schema-migration-offers": "defer"}
            )
        )
        self.assertEqual(
            deferred["outcome"]["deferred_surfaces"],
            ["project-schema-migration-offers"],
        )
        self.assertFalse(deferred["outcome"]["fully_updated"])
        choice = next(
            item
            for item in deferred["choices"]
            if item["surface"] == "project-schema-migration-offers"
        )
        self.assertEqual(choice["state"], "deferred")
        self.assertEqual(
            choice["provenance"],
            scenario["expected_choice_provenance"][0],
        )

        carried = self.plan()
        self.assertEqual(
            [item["choice_state"] for item in carried["migrations"]],
            ["deferred"],
        )
        carried_choice = next(
            item
            for item in carried["choices"]
            if item["surface"] == "project-schema-migration-offers"
        )
        self.assertEqual(
            carried_choice["provenance"],
            scenario["expected_choice_provenance"][1],
        )
        assessment = carried["internal"]["resume_assessment"]
        self.assertEqual(
            [
                item["project_identity"]
                for item in assessment["preserved_migrations"]
            ],
            ["governed"],
        )
        self.assertEqual(assessment["remaining_plan"], [])

        config.write_text(
            f'[project]\nproject_schema_version = "{scenario["changed_schema"]}"\n',
            encoding="utf-8",
        )
        reoffered = self.plan()
        self.assertEqual(
            [item["choice_state"] for item in reoffered["migrations"]],
            [scenario["expected_reoffered_state"]],
        )
        self.assertEqual(
            reoffered["internal"]["resume_assessment"][
                "preserved_migrations"
            ],
            [],
        )
        self.assertEqual(
            [
                item["surface"]
                for item in reoffered["internal"]["resume_assessment"][
                    "remaining_plan"
                ]
            ],
            ["project-schema-migration-offers"],
        )

    def test_migration_cannot_be_authorized_through_the_install_workflow(
        self,
    ) -> None:
        for disposition in ("accept", "decline"):
            with self.subTest(disposition=disposition):
                with self.assertRaises(WorkflowRefusal) as caught:
                    self.plan(
                        decisions={
                            "project-schema-migration-offers": disposition
                        }
                    )
                self.assertIn(
                    "separately authorized", str(caught.exception)
                )

    def test_declines_are_not_carried_across_a_source_change(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        bridge = self.client_home / ".codex" / "skills" / "use-cartopian"
        bridge.mkdir(parents=True, exist_ok=True)
        (bridge / "SKILL.md").write_text("operator edit\n", encoding="utf-8")
        apply_workflow(self.plan(decisions={"bridges": "decline"}))
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
        assessment = self.assessment(source_root=alternate)
        self.assertEqual(assessment["compatibility"], "source-mismatch")
        self.assertEqual(assessment["preserved_choices"], [])


class PortableEvidenceTests(ResumableProgressTestCase):
    def test_portable_record_explains_state_without_private_content(
        self,
    ) -> None:
        expectations = SCENARIOS["portable_evidence"]
        apply_workflow(self.plan(operation="fresh-install"))
        (self.install_root / "wrappers" / "bin" / "cartopian-codex").write_text(
            "drift\n", encoding="utf-8"
        )
        plan = self.plan()
        envelope = self.envelope()
        portable = portable_evidence(
            envelope, assessment=plan["internal"]["resume_assessment"]
        )
        self.assertEqual(
            portable_evidence_diagnostics(portable), []
        )
        self.assertEqual(list(portable), expectations["expected_fields"])
        self.assertEqual(
            [item["kind"] for item in portable["version_identities"]],
            expectations["expected_version_kinds"],
        )
        self.assertEqual(
            [item["surface"] for item in portable["surfaces"]],
            list(SURFACE_KINDS),
        )
        self.assertEqual(
            [item["surface"] for item in portable["remaining_work"]],
            ["wrappers"],
        )
        self.assertIn("restart_required", portable)

        # The exclusions list names the content classes that are kept out, so
        # it is scanned for identifiers but not for the class names themselves.
        scanned = {
            key: value
            for key, value in portable.items()
            if key != "exclusions"
        }
        serialized = json.dumps(scanned)
        for forbidden in expectations["forbidden_substrings"]:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)
        for pattern in expectations["forbidden_patterns"]:
            with self.subTest(pattern=pattern):
                self.assertNotRegex(serialized, pattern)
        for internal_only in (
            envelope["run"]["marker"],
            str(self.install_root),
            str(self.client_home),
            str(REPO_ROOT),
        ):
            with self.subTest(internal_only=internal_only):
                self.assertNotIn(internal_only, serialized)

        document = render_portable_evidence(portable)
        self.assertIn("## Version identities", document)
        self.assertIn("## Per-surface state", document)
        self.assertIn("## Restart", document)
        self.assertIn("## Remaining work", document)
        self.assertIn("wrappers", document)
        for pattern in expectations["forbidden_patterns"]:
            self.assertNotRegex(document, pattern)
        self.assertEqual(document, render_portable_evidence(portable))

    def test_portable_evidence_rejects_smuggled_identifiers(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        portable = portable_evidence(self.envelope())
        portable["surfaces"][0]["evidence"]["marker"] = "TASK-03-004"
        diagnostics = portable_evidence_diagnostics(portable)
        self.assertIn(
            "portable-evidence-governance-field",
            {item["code"] for item in diagnostics},
        )

        portable = portable_evidence(self.envelope())
        portable["surfaces"][0]["evidence"]["identity"] = (
            "api_key sk-live-not-a-real-value"
        )
        self.assertIn(
            "portable-evidence-private-field",
            {
                item["code"]
                for item in portable_evidence_diagnostics(portable)
            },
        )

    def test_portable_evidence_is_separate_from_recovery_metadata(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        envelope = self.envelope()
        portable = portable_evidence(envelope)
        for internal_field in (
            "boundary",
            "sequence",
            "retention",
            "recovery",
            "surface_profiles",
            "progress_schema_identity",
        ):
            with self.subTest(field=internal_field):
                self.assertNotIn(internal_field, portable)
        self.assertIn("internal-recovery-metadata", portable["exclusions"])


class CliAndMcpParityTests(ResumableProgressTestCase):
    def _cli(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli_main(arguments)
        return exit_code, output.getvalue()

    def test_cli_and_mcp_expose_equivalent_resume_semantics(self) -> None:
        install = self.root / "parity-install"
        exit_code, _ = self._cli(
            [
                "install-workflow",
                str(REPO_ROOT),
                str(install),
                "--operation",
                "fresh-install",
                "--apply",
            ]
        )
        self.assertEqual(exit_code, 0)

        arguments = [
            "resume-install",
            str(REPO_ROOT),
            str(install),
            "--portable-evidence",
        ]
        cli_code, raw = self._cli(arguments)
        cli_record = json.loads(raw)

        response = server.handle_request(
            "tools/call",
            {
                "name": "resume_install",
                "arguments": {
                    "source_root": str(REPO_ROOT),
                    "install_root": str(install),
                    "portable_evidence": True,
                },
            },
        )
        self.assertEqual(
            response["structuredContent"]["exit_code"], cli_code
        )
        self.assertEqual(
            response["structuredContent"]["records"][0], cli_record
        )
        for field in (
            "progress_contract",
            "progress",
            "resume",
            "affected_surface_plan",
            "surface_retry_profiles",
            "workflow",
            "portable_evidence",
            "portable_evidence_document",
        ):
            self.assertIn(field, cli_record)
        self.assertEqual(
            cli_record["resume"]["assessment_schema"],
            resume_state.RESUME_ASSESSMENT_SCHEMA,
        )
        # The emitted classification is always inside the emitted vocabulary.
        self.assertIn(
            cli_record["resume"]["compatibility"],
            cli_record["progress_contract"]["vocabularies"][
                "compatibility_states"
            ],
        )

    def test_resume_command_is_read_only(self) -> None:
        apply_workflow(self.plan(operation="fresh-install"))
        before = {
            path.relative_to(self.install_root).as_posix(): path.read_bytes()
            for path in self.install_root.rglob("*")
            if path.is_file()
        }
        exit_code, raw = self._cli(
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
        self.assertTrue(json.loads(raw)["resume"]["reusable"])
        after = {
            path.relative_to(self.install_root).as_posix(): path.read_bytes()
            for path in self.install_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_resume_command_reports_uncertain_work_as_failure(self) -> None:
        self.crash_mid_mutation()
        exit_code, raw = self._cli(
            [
                "resume-install",
                str(REPO_ROOT),
                str(self.install_root),
                "--operation",
                "fresh-install",
                "--client",
                "codex",
            ]
        )
        self.assertEqual(exit_code, 1)
        record = json.loads(raw)
        self.assertEqual(
            [item["surface"] for item in record["resume"]["uncertain"]],
            ["core-files"],
        )
        self.assertEqual(
            record["progress"]["terminal"]["completion"], "pending"
        )

    def test_install_workflow_command_emits_the_resume_assessment(self) -> None:
        exit_code, raw = self._cli(
            [
                "install-workflow",
                str(REPO_ROOT),
                str(self.install_root),
                "--operation",
                "fresh-install",
                "--client",
                "codex",
            ]
        )
        self.assertEqual(exit_code, 0)
        record = json.loads(raw)
        self.assertEqual(record["resume"]["compatibility"], "absent")
        self.assertEqual(record["resume"]["reusable"], [])

    def test_documentation_names_the_persistence_boundary(self) -> None:
        document = (
            REPO_ROOT / "protocol" / "INSTALL_UPDATE_STATE.md"
        ).read_text(encoding="utf-8")
        for token in (
            PROGRESS_FILE,
            QUARANTINE_FILE,
            LEASE_FILE,
            "resume-install",
            "inspect-before-retry",
            "refuse-replay",
        ):
            with self.subTest(token=token):
                self.assertIn(token, document)
        self.assertNotRegex(
            document, r"\b(?:TASK|SPEC|PHASE|PROMPT|REVIEW|REPORT)-[0-9]"
        )


if __name__ == "__main__":
    unittest.main()
