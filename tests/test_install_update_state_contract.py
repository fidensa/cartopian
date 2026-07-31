"""Authoritative install/update state-contract acceptance tests."""
from __future__ import annotations

import ast
import copy
import io
import json
import sys
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cli.install_state import (
    CHECKPOINT_STATUSES,
    ALLOWED_TRANSITIONS,
    LIFECYCLE_STATES,
    OPERATOR_CHOICE_STATES,
    OPERATION_KINDS,
    PEER_IDENTITY_KINDS,
    RECORD_SCHEMA_VERSION,
    RESTART_STATES,
    SCHEMA_IDENTITY,
    SURFACE_KINDS,
    SURFACE_STATES,
    ContractRefusal,
    build_record,
    contract_projection,
    evaluate_record,
    identity_state_vocabulary,
    positive_identity_fact,
    resume_work,
    stable_projection,
    supported_record_schema_version,
    transition,
    validate_portable_evidence,
)
from cli.main import build_parser
from mcp_server import server

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "install_state"


def _version(kind: str, value: str, state: str = "current") -> dict:
    authorities = {
        "release_version": "maintainer-release-metadata",
        "installed_content": "installed-or-materialized-content",
        "project_schema_version": "project-config-and-shipped-schema",
        "running_server": "connected-mcp-process",
        "mcp_protocol_version": "mcp-wire-handshake",
    }
    if kind == "release_version":
        state = "known"
    elif kind == "installed_content":
        state = "verified"
    elif kind == "mcp_protocol_version":
        state = "supported"
    return {
        "kind": kind,
        "value": value,
        "state": state,
        "authority": authorities[kind],
        "verification": "verified",
    }


def _surface(kind: str, state: str = "current", affected: bool = False) -> dict:
    return {
        "kind": kind,
        "locator": f"installed:{kind}",
        "desired_identity": "v1",
        "observed_identity": "v1",
        "state": state,
        "affected": affected,
        "required": True,
    }


def _base_record(*, state: str = "verifying", **overrides) -> dict:
    values = {
        "operation": "update",
        "run_marker": "fixture-run-001",
        "source": {
            "kind": "release",
            "value": "v1",
            "state": "known",
            "authority": "maintainer-release-metadata",
        },
        "versions": [_version(kind, "v1") for kind in PEER_IDENTITY_KINDS],
        "surfaces": [_surface(kind) for kind in SURFACE_KINDS],
        "state": state,
    }
    values.update(overrides)
    return build_record(**values)


class ContractVocabularyTests(unittest.TestCase):
    def test_machine_contract_has_stable_boundary_and_closed_vocabularies(self):
        contract = contract_projection()
        self.assertEqual(contract["schema_identity"], SCHEMA_IDENTITY)
        self.assertEqual(contract["record_schema_version"], RECORD_SCHEMA_VERSION)
        self.assertEqual(contract["field_boundaries"]["internal"], ["internal"])
        self.assertEqual(
            contract["vocabularies"]["peer_identity_kinds"],
            list(PEER_IDENTITY_KINDS),
        )
        self.assertEqual(
            contract["vocabularies"]["surface_kinds"], list(SURFACE_KINDS)
        )
        self.assertEqual(
            contract["vocabularies"]["lifecycle_states"],
            list(LIFECYCLE_STATES),
        )
        self.assertEqual(
            contract["vocabularies"]["surface_states"], list(SURFACE_STATES)
        )
        self.assertEqual(
            contract["vocabularies"]["checkpoint_statuses"],
            list(CHECKPOINT_STATUSES),
        )
        self.assertEqual(
            contract["vocabularies"]["operator_choice_states"],
            list(OPERATOR_CHOICE_STATES),
        )
        self.assertEqual(
            contract["vocabularies"]["restart_states"], list(RESTART_STATES)
        )
        self.assertEqual(
            contract["vocabularies"]["operation_kinds"], list(OPERATION_KINDS)
        )
        self.assertEqual(
            set(contract["vocabularies"]["version_states"]),
            set(PEER_IDENTITY_KINDS),
        )

    def test_every_operation_kind_has_one_run_model(self):
        evidence = {
            "identity": "sha256:abc",
            "kind": "file-digest",
            "verification": "verified",
        }
        completed = {
            "id": "prior-core",
            "phase": "apply",
            "surface": "core-files",
            "status": "completed",
            "evidence": evidence,
            "verification": "verified",
            "retry_safety": "idempotent",
        }
        for operation in OPERATION_KINDS:
            checkpoints = [completed] if operation == "resume" else []
            record = _base_record(
                operation=operation,
                checkpoints=checkpoints,
            )
            self.assertEqual(record["run"]["operation"], operation)
            self.assertNotIn(
                "unknown-vocabulary",
                [item["code"] for item in record["diagnostics"]],
            )

    def test_stable_projection_excludes_internal_diagnostic_detail(self):
        record = _base_record(
            internal={"trace": "private prompt content", "raw_exception": "secret"}
        )
        projected = stable_projection(record)
        self.assertNotIn("internal", projected)
        self.assertEqual(list(projected), contract_projection()["field_boundaries"]["stable"])

    def test_equivalent_input_order_has_equivalent_projection(self):
        left = _base_record()
        right = _base_record(
            versions=list(reversed(left["versions"])),
            surfaces=list(reversed(left["surfaces"])),
        )
        self.assertEqual(stable_projection(left), stable_projection(right))


class SchemaVersionAndPositiveSemanticsTests(unittest.TestCase):
    """The contract owns what a supported version and a positive fact are."""

    def test_only_the_declared_integer_is_the_supported_record_version(self):
        self.assertTrue(supported_record_schema_version(RECORD_SCHEMA_VERSION))
        for value in (
            1.0,
            True,
            False,
            "1",
            None,
            RECORD_SCHEMA_VERSION + 1,
            [RECORD_SCHEMA_VERSION],
        ):
            with self.subTest(value=value):
                self.assertFalse(supported_record_schema_version(value))

    def test_evaluated_record_refuses_a_numerically_equal_float_version(self):
        record = _base_record()
        self.assertEqual(record["diagnostics"], [])
        record["record_schema_version"] = 1.0
        evaluated = evaluate_record(record)
        self.assertIn(
            "invalid-schema",
            [item["code"] for item in evaluated["diagnostics"]],
        )
        self.assertEqual(evaluated["outcome"]["status"], "blocked")

    def test_identity_state_vocabulary_matches_the_machine_projection(self):
        projected = contract_projection()["vocabularies"]["version_states"]
        for kind in PEER_IDENTITY_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(
                    list(identity_state_vocabulary(kind)), projected[kind]
                )
        self.assertEqual(identity_state_vocabulary("not-an-identity"), ())

    def test_only_a_verified_pair_is_a_positive_identity_fact(self):
        for kind in PEER_IDENTITY_KINDS:
            for state in identity_state_vocabulary(kind) + ("", True, None, 1):
                for verification in contract_projection()["vocabularies"][
                    "verification_states"
                ] + ["", True, None, 1]:
                    positive = positive_identity_fact(kind, state, verification)
                    with self.subTest(
                        kind=kind, state=state, verification=verification
                    ):
                        self.assertEqual(
                            positive,
                            state
                            not in (
                                "unknown",
                                "unverified",
                                "missing",
                                "dirty",
                                "older",
                                "stale-runtime",
                                "symlink-divergent",
                                "malformed",
                                "unsupported",
                                "unsupported-newer",
                                "contradictory",
                                "",
                                True,
                                None,
                                1,
                            )
                            and verification == "verified",
                        )

    def test_installed_content_has_exactly_one_positive_state(self):
        positive = [
            state
            for state in identity_state_vocabulary("installed_content")
            if positive_identity_fact("installed_content", state, "verified")
        ]
        self.assertEqual(positive, ["verified"])


class AccountingAndIdentityTests(unittest.TestCase):
    def test_every_surface_and_peer_identity_appears_exactly_once(self):
        record = _base_record()
        self.assertEqual([item["kind"] for item in record["surfaces"]], list(SURFACE_KINDS))
        self.assertEqual(
            [item["kind"] for item in record["versions"]],
            list(PEER_IDENTITY_KINDS),
        )
        self.assertEqual(record["diagnostics"], [])

    def test_missing_and_duplicate_surface_fail_closed(self):
        surfaces = [_surface(kind) for kind in SURFACE_KINDS[:-1]]
        surfaces.append(_surface(SURFACE_KINDS[0]))
        record = _base_record(surfaces=surfaces)
        codes = [item["code"] for item in record["diagnostics"]]
        self.assertIn("duplicate-surface", codes)
        self.assertIn("missing-surface", codes)
        self.assertEqual(record["outcome"]["status"], "blocked")

    def test_peer_authority_cannot_substitute_for_another_identity(self):
        versions = [_version(kind, "v1") for kind in PEER_IDENTITY_KINDS]
        versions[0]["authority"] = versions[1]["authority"]
        versions[0]["derived_from"] = "installed_content"
        record = _base_record(versions=versions)
        codes = [item["code"] for item in record["diagnostics"]]
        self.assertIn("peer-identity-substitution", codes)
        self.assertEqual(record["outcome"]["status"], "blocked")

    def test_ambiguous_surface_detection_is_explicit_and_blocking(self):
        surfaces = [_surface(kind) for kind in SURFACE_KINDS]
        surfaces[0].update(
            {
                "affected": False,
                "state": "stale",
                "observed_identity": "v0",
            }
        )
        record = _base_record(surfaces=surfaces)
        self.assertIn(
            "surface-detection-contradictory",
            [item["code"] for item in record["diagnostics"]],
        )
        self.assertEqual(record["outcome"]["status"], "blocked")


class TransitionAndTerminalSafetyTests(unittest.TestCase):
    def test_allowed_transition_is_deterministic(self):
        record = _base_record(state="preflight")
        planned = transition(record, "planned")
        self.assertEqual(planned["state"], "planned")
        self.assertEqual(planned["outcome"]["status"], "in-progress")

    def test_transition_grammar_accepts_exactly_the_declared_edges(self):
        for source in LIFECYCLE_STATES:
            record = _base_record(state=source)
            for target in LIFECYCLE_STATES:
                if target in ALLOWED_TRANSITIONS[source]:
                    self.assertEqual(
                        transition(record, target)["state"],
                        target,
                        msg=f"declared edge refused: {source} -> {target}",
                    )
                else:
                    with self.assertRaises(
                        ContractRefusal,
                        msg=f"undeclared edge accepted: {source} -> {target}",
                    ):
                        transition(record, target)

    def test_every_lifecycle_and_unresolved_surface_state_is_observable(self):
        for lifecycle in LIFECYCLE_STATES:
            self.assertEqual(_base_record(state=lifecycle)["state"], lifecycle)
        unresolved = (
            "unknown",
            "dirty",
            "symlink-divergent",
            "missing",
            "malformed",
            "unsupported-newer",
            "contradictory",
        )
        for state in unresolved:
            surfaces = [_surface(kind) for kind in SURFACE_KINDS]
            surfaces[0].update(
                {
                    "state": state,
                    "affected": True,
                    "observed_identity": "unresolved",
                }
            )
            record = _base_record(state="complete", surfaces=surfaces)
            self.assertEqual(record["surfaces"][0]["state"], state)
            self.assertEqual(record["outcome"]["status"], "blocked")

    def test_restart_required_can_only_advance_to_restart_verified_or_terminal_failure(self):
        record = _base_record(
            state="restart-required",
            restarts=[
                {
                    "client": "codex",
                    "installed_identity": "v1",
                    "running_identity": "v0",
                    "state": "required",
                    "instruction_class": "restart-client",
                    "proof_state": "unverified",
                }
            ],
        )
        with self.assertRaises(ContractRefusal) as caught:
            transition(record, "complete")
        self.assertEqual(caught.exception.code, "invalid-transition")
        verified = transition(record, "restart-verified")
        self.assertEqual(verified["state"], "restart-verified")
        self.assertEqual(verified["outcome"]["status"], "blocked")

    def test_terminal_completion_refuses_pending_surface(self):
        surfaces = [_surface(kind) for kind in SURFACE_KINDS]
        surfaces[2].update(
            {"state": "pending", "affected": True, "observed_identity": "v0"}
        )
        record = _base_record(state="complete", surfaces=surfaces)
        self.assertIn(
            "terminal-claim-unsafe",
            [item["code"] for item in record["diagnostics"]],
        )
        self.assertEqual(record["outcome"]["status"], "blocked")
        self.assertEqual(record["outcome"]["claim"], "none")

    def test_optional_pending_or_blocked_surface_cannot_claim_fully_updated(self):
        for unresolved in ("pending", "blocked"):
            with self.subTest(state=unresolved):
                surfaces = [_surface(kind) for kind in SURFACE_KINDS]
                bridge = next(
                    item for item in surfaces if item["kind"] == "bridges"
                )
                bridge.update(
                    {
                        "required": False,
                        "affected": True,
                        "state": unresolved,
                        "observed_identity": "v0",
                    }
                )
                record = _base_record(state="complete", surfaces=surfaces)
                self.assertIn(
                    "terminal-claim-unsafe",
                    [item["code"] for item in record["diagnostics"]],
                )
                self.assertEqual(record["outcome"]["status"], "blocked")
                self.assertEqual(record["outcome"]["claim"], "none")
                self.assertFalse(record["outcome"]["fully_updated"])
                accounted = (
                    record["outcome"]["pending_surfaces"]
                    + record["outcome"]["blocked_surfaces"]
                )
                self.assertEqual(accounted, ["bridges"])

    def test_verified_complete_record_can_claim_fully_updated(self):
        record = _base_record(state="complete")
        self.assertEqual(record["outcome"]["status"], "complete")
        self.assertEqual(record["outcome"]["claim"], "fully-updated")
        self.assertTrue(record["outcome"]["fully_updated"])

    def test_blocked_and_failed_are_distinct_terminal_outcomes(self):
        blocked = _base_record(state="blocked")
        failed = _base_record(state="failed")
        self.assertEqual(blocked["outcome"]["status"], "blocked")
        self.assertEqual(failed["outcome"]["status"], "failed")
        self.assertEqual(blocked["outcome"]["claim"], "none")
        self.assertEqual(failed["outcome"]["claim"], "none")


class CheckpointChoiceRestartMigrationTests(unittest.TestCase):
    def test_completed_checkpoint_requires_portable_verified_evidence(self):
        checkpoint = {
            "id": "apply-core",
            "phase": "apply",
            "surface": "core-files",
            "status": "completed",
            "evidence": None,
            "verification": "unverified",
            "retry_safety": "inspect-before-retry",
        }
        record = _base_record(checkpoints=[checkpoint])
        codes = [item["code"] for item in record["diagnostics"]]
        self.assertIn("checkpoint-evidence-missing", codes)
        self.assertIn("checkpoint-verification-missing", codes)
        self.assertEqual(record["outcome"]["status"], "blocked")

    def test_apply_refusal_checkpoint_exposes_recovery_guidance(self):
        checkpoint = {
            "id": "apply-client-configuration",
            "phase": "apply",
            "surface": "client-configuration",
            "status": "blocked",
            "evidence": {
                "identity": "sha256:preserved",
                "kind": "configuration-fingerprint",
                "verification": "failed",
            },
            "verification": "failed",
            "retry_safety": "inspect-before-retry",
            "attempted_action": "reconfigure-registration",
            "mutation_status": "refused-preserved",
            "recovery": "repair the malformed operator configuration before retry",
            "recovery_artifact": "operator-client-configuration:preserved",
        }
        record = _base_record(
            state="blocked", checkpoints=[checkpoint]
        )
        self.assertIn(
            "apply-refused",
            [item["code"] for item in record["diagnostics"]],
        )
        self.assertEqual(
            record["outcome"]["recovery_guidance"],
            ["repair the malformed operator configuration before retry"],
        )

    def test_resume_names_only_incomplete_or_unverified_work(self):
        evidence = {
            "identity": "sha256:abc",
            "kind": "file-digest",
            "verification": "verified",
        }
        checkpoints = [
            {
                "id": "complete-core",
                "phase": "apply",
                "surface": "core-files",
                "status": "completed",
                "evidence": evidence,
                "verification": "verified",
                "retry_safety": "idempotent",
            },
            {
                "id": "inspect-wrapper",
                "phase": "apply",
                "surface": "wrappers",
                "status": "unverified",
                "evidence": evidence,
                "verification": "unverified",
                "retry_safety": "inspect-before-retry",
            },
            {
                "id": "pending-bridge",
                "phase": "repair",
                "surface": "bridges",
                "status": "pending",
                "evidence": None,
                "verification": "unknown",
                "retry_safety": "idempotent",
            },
        ]
        record = _base_record(operation="resume", checkpoints=checkpoints)
        self.assertEqual(
            [item["id"] for item in resume_work(record)],
            ["inspect-wrapper", "pending-bridge"],
        )

    def test_repair_offer_is_not_authorization(self):
        checkpoint = {
            "id": "repair-bridge",
            "phase": "repair",
            "surface": "bridges",
            "status": "completed",
            "evidence": {
                "identity": "sha256:def",
                "kind": "file-digest",
                "verification": "verified",
            },
            "verification": "verified",
            "retry_safety": "idempotent",
        }
        choice = {
            "id": "bridge-repair",
            "surface": "bridges",
            "offered_action": "repair",
            "state": "offered",
            "provenance": "interactive-operator",
        }
        record = _base_record(checkpoints=[checkpoint], choices=[choice])
        self.assertIn(
            "choice-not-authorized",
            [item["code"] for item in record["diagnostics"]],
        )
        self.assertEqual(record["outcome"]["status"], "blocked")

    def test_declined_repair_remains_visible_and_qualifies_completion(self):
        surfaces = [_surface(kind) for kind in SURFACE_KINDS]
        bridge = next(item for item in surfaces if item["kind"] == "bridges")
        bridge.update({"state": "declined", "affected": True, "observed_identity": "v0"})
        choice = {
            "id": "bridge-repair",
            "surface": "bridges",
            "offered_action": "repair",
            "state": "declined",
            "provenance": "interactive-operator",
        }
        record = _base_record(state="complete", surfaces=surfaces, choices=[choice])
        self.assertEqual(record["outcome"]["status"], "complete-qualified")
        self.assertEqual(record["outcome"]["claim"], "qualified-complete")
        self.assertFalse(record["outcome"]["fully_updated"])
        self.assertEqual(record["outcome"]["declined_surfaces"], ["bridges"])

    def test_declined_surface_requires_provenance_backed_choice(self):
        surfaces = [_surface(kind) for kind in SURFACE_KINDS]
        bridge = next(item for item in surfaces if item["kind"] == "bridges")
        bridge.update(
            {"state": "declined", "affected": True, "observed_identity": "v0"}
        )
        unprovenanced = {
            "id": "bridge-repair",
            "surface": "bridges",
            "offered_action": "repair",
            "state": "declined",
            "provenance": "",
        }
        for choices in ([], [unprovenanced]):
            with self.subTest(choice_present=bool(choices)):
                record = _base_record(
                    state="complete", surfaces=surfaces, choices=choices
                )
                self.assertIn(
                    "decline-provenance-missing",
                    [item["code"] for item in record["diagnostics"]],
                )
                self.assertEqual(record["outcome"]["status"], "blocked")
                self.assertEqual(record["outcome"]["claim"], "none")

    def test_disk_update_never_proves_running_activation(self):
        versions = [_version(kind, "v1") for kind in PEER_IDENTITY_KINDS]
        running = next(item for item in versions if item["kind"] == "running_server")
        running.update({"value": "v0", "state": "stale-runtime"})
        restart = {
            "client": "codex",
            "installed_identity": "v1",
            "running_identity": "v0",
            "state": "required",
            "instruction_class": "restart-client",
            "proof_state": "unverified",
        }
        record = _base_record(
            state="restart-required", versions=versions, restarts=[restart]
        )
        self.assertTrue(record["outcome"]["restart_required"])
        self.assertFalse(record["outcome"]["fully_updated"])
        with self.assertRaises(ContractRefusal):
            transition(record, "complete")

    def test_stale_runtime_without_restart_fact_fails_closed(self):
        versions = [_version(kind, "v1") for kind in PEER_IDENTITY_KINDS]
        running = next(item for item in versions if item["kind"] == "running_server")
        running.update({"value": "v0", "state": "stale-runtime"})
        record = _base_record(state="verifying", versions=versions)
        self.assertTrue(record["outcome"]["restart_required"])
        self.assertEqual(record["outcome"]["status"], "blocked")
        self.assertIn(
            "restart-fact-missing",
            [item["code"] for item in record["diagnostics"]],
        )

    def test_unverified_peer_fact_never_supports_fully_updated(self):
        versions = [_version(kind, "v1") for kind in PEER_IDENTITY_KINDS]
        versions[0]["verification"] = "unverified"
        record = _base_record(state="complete", versions=versions)
        self.assertEqual(record["outcome"]["status"], "complete-qualified")
        self.assertFalse(record["outcome"]["fully_updated"])

    def test_restart_verification_requires_fresh_process_proof(self):
        restart = {
            "client": "codex",
            "installed_identity": "v1",
            "running_identity": "v0",
            "state": "verified",
            "instruction_class": "restart-client",
            "proof_state": "unverified",
        }
        record = _base_record(state="restart-verified", restarts=[restart])
        self.assertIn(
            "restart-proof-missing",
            [item["code"] for item in record["diagnostics"]],
        )
        self.assertEqual(record["outcome"]["status"], "blocked")

    def test_migration_offer_is_not_migration_completion(self):
        migration = {
            "project_identity": "registered-project",
            "current_schema": "v0",
            "target_schema": "v1",
            "applicability": "applicable",
            "choice_state": "offered",
            "result": "completed",
        }
        record = _base_record(migrations=[migration])
        self.assertIn(
            "migration-not-authorized",
            [item["code"] for item in record["diagnostics"]],
        )
        self.assertEqual(record["outcome"]["status"], "blocked")


class PortableEvidenceTests(unittest.TestCase):
    def test_portable_evidence_excludes_private_and_governance_fields(self):
        evidence = {
            "identity": "sha256:abc",
            "kind": "file-digest",
            "verification": "verified",
            "prompt": "private prompt content",
            "task_id": "TASK-03-001",
            "executable": "/tmp/arbitrary",
            "destination": "/tmp/arbitrary",
            "token": "secret",
        }
        codes = [item["code"] for item in validate_portable_evidence(evidence)]
        self.assertEqual(
            codes,
            [
                "portable-evidence-destination",
                "portable-evidence-executable",
                "portable-evidence-governance-field",
                "portable-evidence-private-field",
                "portable-evidence-private-field",
            ],
        )

    def test_every_non_completed_checkpoint_rejects_and_redacts_prohibited_evidence(self):
        prohibited = {
            "token": "REDACTED-SECRET-PROBE",
            "prompt": "REDACTED-PRIVATE-PROBE",
            "task_id": "REDACTED-GOVERNANCE-PROBE",
            "executable": "/bin/redacted-probe",
            "destination": "/tmp/redacted-probe",
        }
        expected_codes = {
            "portable-evidence-destination",
            "portable-evidence-executable",
            "portable-evidence-governance-field",
            "portable-evidence-private-field",
        }
        for status in CHECKPOINT_STATUSES:
            if status == "completed":
                continue
            with self.subTest(status=status):
                checkpoint = {
                    "id": f"evidence-{status}",
                    "phase": "apply",
                    "surface": "core-files",
                    "status": status,
                    "evidence": {
                        "identity": "sha256:probe",
                        "kind": "file-digest",
                        "verification": "unverified",
                        **prohibited,
                    },
                    "verification": "unverified",
                    "retry_safety": (
                        "inspect-before-retry"
                        if status == "unverified"
                        else "idempotent"
                    ),
                }
                built = _base_record(
                    state="applying", checkpoints=[checkpoint]
                )
                rebuilt = evaluate_record(built)
                for record in (built, rebuilt):
                    codes = {
                        item["code"] for item in record["diagnostics"]
                    }
                    self.assertTrue(expected_codes.issubset(codes))
                    self.assertEqual(record["outcome"]["status"], "blocked")
                    projected = stable_projection(record)
                    self.assertEqual(
                        projected["checkpoints"][0]["evidence"],
                        {
                            "identity": "sha256:probe",
                            "kind": "file-digest",
                            "verification": "unverified",
                        },
                    )
                    serialized = json.dumps(projected, sort_keys=True)
                    for value in prohibited.values():
                        self.assertNotIn(value, serialized)

    def test_state_contract_runtime_remains_standard_library_only(self):
        source_path = REPO_ROOT / "cli" / "install_state.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        allowed = set(sys.stdlib_module_names) | {"cli"}
        self.assertEqual(imported_roots - allowed, set())

        metadata = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["project"]["dependencies"], [])


class FixtureAndProjectionTests(unittest.TestCase):
    def test_mixed_state_fixture_matches_acceptance_example(self):
        raw = json.loads((FIXTURES / "mixed-state.json").read_text(encoding="utf-8"))
        record = build_record(**raw)
        self.assertEqual(record["state"], "restart-required")
        self.assertEqual(record["outcome"]["status"], "in-progress")
        self.assertTrue(record["outcome"]["restart_required"])
        self.assertEqual(record["outcome"]["declined_surfaces"], ["bridges"])
        self.assertEqual(record["migrations"][0]["choice_state"], "offered")
        self.assertEqual(record["migrations"][0]["result"], "not-run")

    def test_cli_and_mcp_emit_the_same_contract_projection(self):
        parser = build_parser()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            args = parser.parse_args(["install-state-contract"])
            code = args._handler(args)
        self.assertEqual(code, 0, err.getvalue())
        cli_record = json.loads(out.getvalue())

        server._TOOL_CACHE = None
        mcp_result = server.call_tool("install_state_contract", {})
        self.assertEqual(mcp_result["structuredContent"]["exit_code"], 0)
        mcp_record = mcp_result["structuredContent"]["records"][0]
        self.assertEqual(cli_record, mcp_record)
        self.assertEqual(cli_record, contract_projection())

    def test_contract_documentation_points_to_code_authority(self):
        text = (REPO_ROOT / "protocol" / "INSTALL_UPDATE_STATE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("cli.install_state", text)
        self.assertIn("cartopian install-state-contract", text)
        self.assertIn("Stable external fields", text)
        self.assertIn("Internal diagnostic detail", text)
        self.assertIn("`required`", text)
        self.assertIn("`affected`", text)
        self.assertNotIn("TASK-03-001", text)


if __name__ == "__main__":
    unittest.main()
