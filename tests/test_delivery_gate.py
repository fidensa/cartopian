"""Behavioral coverage for the domain-neutral delivery gate.

The gate exists for one failure: an artifact being mistaken for an outcome.
These tests hold the shared validator to that line — one semantic contract for
technical and nontechnical delivery alike, three states that never collapse
into one another, fail-closed handling of missing, placeholder, contradictory,
unavailable-owner, changed-target, and self-certified records, and an
external-action boundary the validator never crosses.
"""
import argparse
import io
import json
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cli import delivery_contract
from cli.commands import close_audit, compose_state, next_action
from cli.main import SUBCOMMANDS, build_parser
from tests.scaffold import project_scaffold

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "delivery"
CONTRACT_PATH = REPO_ROOT / "protocol" / "delivery-contract.json"
PROJECTION_PATH = REPO_ROOT / "protocol" / "DELIVERY.md"

_TOML = (
    "[project]\n"
    'id = "delivery-proj"\n'
    'name = "Delivery Project"\n'
    'project_schema_version = "v0.12.0"\n'
)

# The seven domain-neutral delivery semantics FR-018 requires, plus the two
# rows that carry the states they are read against.
SEVEN_SEMANTICS = (
    "owner",
    "target",
    "acceptance-evidence",
    "success-signals",
    "contingency",
    "immediate-verification",
    "follow-up",
)
STATE_ROWS = ("authority", "artifact")


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.md").read_text(encoding="utf-8")


def validate(name: str) -> dict:
    return delivery_contract.validate_record(fixture(name))


def codes(result: dict) -> list:
    return [item["code"] for item in result["ordered_findings"]]


def run_cli(*argv):
    """Drive the real CLI parser in-process; return (exit_code, records, stderr)."""
    parser = build_parser()
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            args = parser.parse_args(list(argv))
            handler = getattr(args, "_handler", None)
            code = handler(args) if handler is not None else 2
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return code, records, err.getvalue()


def planned_project(scaffold, plan_fixture: str) -> str:
    """Give a scaffold a minimal satisfied plan carrying ``plan_fixture``."""
    scaffold.write("IMPLEMENTATION_PLAN.md", fixture(plan_fixture))
    scaffold.write(
        "phases/PHASE-01.md",
        "# PHASE-01: Deliver\n\n## Exit criteria\n\n- `TASK-01-001`\n",
    )
    scaffold.write("tasks/done/TASK-01-001.md", "# TASK-01-001: done\n\nPhase: PHASE-01\n")
    return str(scaffold.project_root)


def invoke(module, **kwargs):
    """Invoke a command handler with emit capture; return (records, exit_code)."""
    captured = []
    original = module.emit_record
    module.emit_record = lambda record, *, out=None: captured.append(record)
    try:
        rc = module.handler(argparse.Namespace(**kwargs))
    finally:
        module.emit_record = original
    return captured, rc


# ---------------------------------------------------------------------------
# One authority, projected once
# ---------------------------------------------------------------------------


class DeliveryContractAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_registry_declares_the_seven_semantics_and_two_state_rows(self) -> None:
        declared = tuple(delivery_contract.declared_semantics())
        self.assertEqual(declared, SEVEN_SEMANTICS + STATE_ROWS)

    def test_every_emitted_code_is_declared_with_a_recovery(self) -> None:
        declared = {item["code"] for item in self.registry["failures"]}
        self.assertTrue(declared)
        emitted = set()
        for path in sorted(FIXTURES.glob("*.md")):
            result = delivery_contract.validate_record(path.read_text(encoding="utf-8"))
            for finding in result["ordered_findings"]:
                emitted.add(finding["code"])
                self.assertTrue(
                    finding["recovery"].strip(),
                    msg=f"{finding['code']} carries no recovery",
                )
        self.assertTrue(emitted)
        self.assertEqual(emitted - declared, set())

    def test_projection_names_the_registry_as_authority(self) -> None:
        text = PROJECTION_PATH.read_text(encoding="utf-8")
        self.assertIn("protocol/delivery-contract.json", text)
        for label in ("Acceptance evidence", "Success signals", "Immediate verification"):
            self.assertIn(label, text)

    def test_result_carries_every_declared_result_field(self) -> None:
        result = validate("complete-technical-rollback")
        for field in self.registry["result"]["result_fields"]:
            self.assertIn(field, result, msg=f"missing result field: {field}")

    def test_vocabulary_is_domain_neutral(self) -> None:
        """No row label or state may assume software delivery.

        The core contract is shared by publication, transition, and adoption
        work; a software-only word in a row label would silently exclude them.
        """
        software_only = (
            "deploy",
            "release",
            "build",
            "commit",
            "repository",
            "server",
            "endpoint",
            "api",
            "binary",
            "rollout",
        )
        surface = [str(row["label"]) for row in self.registry["record"]["rows"]]
        surface.extend(
            str(field["label"])
            for row in self.registry["record"]["rows"]
            for field in row.get("fields", [])
        )
        for group in ("artifact_states", "outcome_states", "follow_up_states"):
            surface.extend(str(item["id"]) for item in self.registry["result"][group])
        pattern = re.compile(r"\b(" + "|".join(software_only) + r")\b")
        for value in surface:
            self.assertIsNone(
                pattern.search(value.casefold()),
                msg=f"software-only vocabulary in `{value}`",
            )


# ---------------------------------------------------------------------------
# One semantic contract across domains
# ---------------------------------------------------------------------------


class OneContractAcrossDomainsTest(unittest.TestCase):
    COMPLETE = (
        "complete-technical-rollback",
        "complete-publication-correction",
        "complete-transition-alternate",
        "complete-adoption-followup-closed",
    )

    def test_technical_and_nontechnical_complete_records_pass(self) -> None:
        for name in self.COMPLETE:
            with self.subTest(record=name):
                result = validate(name)
                self.assertEqual(result["ordered_findings"], [])
                self.assertEqual(result["gate"], "pass")
                self.assertEqual(result["applicability"], "required")
                self.assertEqual(result["artifact_state"], "complete")
                self.assertEqual(result["outcome_state"], "verified")

    def test_every_domain_uses_the_same_declared_rows(self) -> None:
        labels = [
            str(row["label"])
            for row in delivery_contract.load_delivery_contract()["record"]["rows"]
        ]
        for name in self.COMPLETE:
            with self.subTest(record=name):
                body = fixture(name)
                for label in labels:
                    self.assertIn(f"- {label}:", body)

    def test_nontechnical_records_carry_no_software_vocabulary(self) -> None:
        for name in (
            "complete-publication-correction",
            "complete-transition-alternate",
            "complete-adoption-followup-closed",
        ):
            with self.subTest(record=name):
                body = fixture(name).casefold()
                pattern = re.compile(
                    r"\b(deploy|deployment|repository|commit|server|codebase|api)\b"
                )
                self.assertIsNone(pattern.search(body))


# ---------------------------------------------------------------------------
# Three states that stay apart
# ---------------------------------------------------------------------------


class DistinctOutcomeStatesTest(unittest.TestCase):
    def test_artifact_complete_does_not_imply_outcome_verified(self) -> None:
        result = validate("artifact-complete-outcome-unverified")
        self.assertEqual(result["artifact_state"], "complete")
        self.assertEqual(result["outcome_state"], "unverified")
        self.assertEqual(result["gate"], "blocked")
        self.assertIn("delivery-verification-not-run", codes(result))

    def test_verified_outcome_does_not_discharge_follow_up(self) -> None:
        result = validate("complete-technical-rollback")
        self.assertEqual(result["outcome_state"], "verified")
        self.assertEqual(result["follow_up_state"], "scheduled")

    def test_follow_up_states_are_distinguished(self) -> None:
        cases = {
            "complete-technical-rollback": "scheduled",
            "complete-adoption-followup-closed": "satisfied",
            "complete-publication-correction": "not-applicable",
            "unbounded-follow-up": "required",
            "undeclared": "unknown",
        }
        for name, expected in cases.items():
            with self.subTest(record=name):
                self.assertEqual(validate(name)["follow_up_state"], expected)

    def test_missing_immediate_verification_or_follow_up_blocks(self) -> None:
        result = validate("incomplete-technical")
        missing = {
            finding["semantic"]
            for finding in result["ordered_findings"]
            if finding["code"] == "delivery-row-missing"
        }
        self.assertEqual(missing, {"immediate-verification", "follow-up"})
        self.assertEqual(result["gate"], "blocked")
        self.assertEqual(result["outcome_state"], "unverified")
        self.assertEqual(result["follow_up_state"], "unknown")


# ---------------------------------------------------------------------------
# Fail-closed cases
# ---------------------------------------------------------------------------


class FailClosedTest(unittest.TestCase):
    def test_every_required_semantic_blocks_when_absent(self) -> None:
        """Dropping any one of the seven rows blocks the gate by name."""
        complete = fixture("complete-technical-rollback")
        labels = {
            str(row["id"]): str(row["label"])
            for row in delivery_contract.load_delivery_contract()["record"]["rows"]
        }
        for semantic in SEVEN_SEMANTICS:
            with self.subTest(semantic=semantic):
                label = labels[semantic]
                reduced = "\n".join(
                    line
                    for line in complete.splitlines()
                    if not line.startswith(f"- {label}:")
                )
                result = delivery_contract.validate_record(reduced)
                self.assertEqual(result["gate"], "blocked")
                self.assertIn(
                    ("delivery-row-missing", semantic),
                    [
                        (finding["code"], finding["semantic"])
                        for finding in result["ordered_findings"]
                    ],
                )

    def test_undeclared_section_fails_closed_with_the_migration_recovery(self) -> None:
        result = validate("undeclared")
        self.assertEqual(result["applicability"], "undeclared")
        self.assertEqual(result["gate"], "blocked")
        self.assertEqual(codes(result), ["delivery-contract-undeclared"])
        self.assertIn(
            "## Delivery contract", result["ordered_findings"][0]["recovery"]
        )

    def test_placeholders_record_nothing(self) -> None:
        result = validate("incomplete-nontechnical")
        self.assertEqual(result["gate"], "blocked")
        self.assertIn("delivery-field-placeholder", codes(result))
        self.assertEqual(result["artifact_state"], "incomplete")
        self.assertEqual(result["outcome_state"], "unverified")

    def test_unavailable_owner_fails_closed(self) -> None:
        result = validate("unavailable-owner")
        self.assertEqual(result["gate"], "blocked")
        self.assertIn("delivery-owner-unavailable", codes(result))

    def test_changed_target_invalidates_prior_target_evidence(self) -> None:
        result = validate("changed-target")
        self.assertEqual(result["gate"], "blocked")
        mismatched = {
            finding["semantic"]
            for finding in result["ordered_findings"]
            if finding["code"] == "delivery-evidence-target-mismatch"
        }
        self.assertEqual(mismatched, {"acceptance-evidence", "immediate-verification"})
        self.assertEqual(result["outcome_state"], "unverified")

    def test_self_certified_acceptance_evidence_fails_closed(self) -> None:
        result = validate("self-certified-evidence")
        self.assertEqual(result["gate"], "blocked")
        self.assertIn("delivery-evidence-self-certified", codes(result))

    def test_unbounded_follow_up_fails_closed(self) -> None:
        result = validate("unbounded-follow-up")
        self.assertEqual(result["gate"], "blocked")
        self.assertIn("delivery-follow-up-unbounded", codes(result))

    def test_a_declared_value_is_read_whatever_its_casing(self) -> None:
        """Casing must not open a hole in a closed vocabulary.

        Every downstream rule compares against a declared value, so accepting
        `Unavailable` while carrying the author's casing forward would satisfy
        the vocabulary check and then silently miss the rule behind it.
        """
        cases = {
            ("Availability: available", "Availability: Unavailable"): (
                "delivery-owner-unavailable"
            ),
            ("Result: pass", "Result: NOT-RUN"): "delivery-verification-not-run",
            ("- Artifact: complete;", "- Artifact: INCOMPLETE;"): (
                "delivery-artifact-incomplete"
            ),
            ("- Authority: operator-authorized;", "- Authority: Declined;"): (
                "delivery-authority-declined"
            ),
        }
        for (old, new), expected in cases.items():
            with self.subTest(value=new):
                result = delivery_contract.validate_record(
                    fixture("complete-technical-rollback").replace(old, new, 1)
                )
                self.assertIn(expected, codes(result))
                self.assertNotIn("delivery-value-not-declared", codes(result))

    def test_a_digit_bounds_a_due_time_before_any_word_is_read(self) -> None:
        """`no later than <date>` is a date, not the word `later`.

        Reading unbounded terms as substrings would reject ordinary due dates,
        so the digit decides first and words are read only without one.
        """
        bounded = {
            "2026-09-02": True,
            "no later than 2026-09-13": True,
            "within 30 days": True,
            "quarterly": True,
            "later": False,
            "eventually": False,
            "asap": False,
            "ongoing monthly": False,
        }
        for value, expected in bounded.items():
            with self.subTest(due=value):
                text = fixture("complete-technical-rollback").replace(
                    "Due: 2026-09-02;", f"Due: {value};", 1
                )
                result = delivery_contract.validate_record(text)
                self.assertEqual(
                    "delivery-follow-up-unbounded" not in codes(result), expected
                )

    def test_an_observed_time_never_accepts_a_cadence(self) -> None:
        text = fixture("complete-technical-rollback").replace(
            "Time: 2026-08-14;", "Time: monthly;", 1
        )
        self.assertIn(
            "delivery-verification-untimed",
            codes(delivery_contract.validate_record(text)),
        )

    def test_partial_record_never_appears_complete(self) -> None:
        for name in ("incomplete-technical", "incomplete-nontechnical"):
            with self.subTest(record=name):
                result = validate(name)
                self.assertEqual(result["gate"], "blocked")
                self.assertNotEqual(result["outcome_state"], "verified")

    def test_repeated_validation_is_idempotent(self) -> None:
        for name in ("complete-technical-rollback", "incomplete-nontechnical"):
            with self.subTest(record=name):
                first = validate(name)
                second = validate(name)
                self.assertEqual(first, second)
                self.assertEqual(first["contract_identity"], second["contract_identity"])

    def test_duplicate_section_leaves_no_single_governing_record(self) -> None:
        doubled = fixture("complete-technical-rollback") + fixture(
            "complete-publication-correction"
        )
        result = delivery_contract.validate_record(doubled)
        self.assertEqual(codes(result), ["delivery-contract-duplicated"])
        self.assertEqual(result["gate"], "blocked")

    def test_undeclared_row_label_is_reported_not_dropped(self) -> None:
        text = fixture("complete-technical-rollback").replace(
            "- Owner: R. Okonkwo;", "- Accountable person: R. Okonkwo;", 1
        )
        result = delivery_contract.validate_record(text)
        self.assertIn("delivery-row-unknown", codes(result))
        self.assertIn("delivery-row-missing", codes(result))

    def test_stray_semicolon_inside_a_value_fails_closed(self) -> None:
        text = fixture("complete-technical-rollback").replace(
            "- Target: operations tenant production environment; Kind: system of record",
            "- Target: operations tenant; the production environment; Kind: system of record",
            1,
        )
        result = delivery_contract.validate_record(text)
        self.assertIn("delivery-field-unknown", codes(result))
        self.assertEqual(result["gate"], "blocked")


# ---------------------------------------------------------------------------
# Contingency
# ---------------------------------------------------------------------------


class ContingencyTest(unittest.TestCase):
    def test_contingency_requires_trigger_owner_and_action(self) -> None:
        result = validate("contingency-without-trigger")
        self.assertEqual(result["gate"], "blocked")
        self.assertIn(
            ("delivery-field-missing", "contingency", "trigger"),
            [
                (finding["code"], finding["semantic"], finding["field"])
                for finding in result["ordered_findings"]
            ],
        )

    def test_correction_and_alternate_substitute_only_when_justified(self) -> None:
        self.assertIn(
            "delivery-rollback-substitution-unjustified",
            codes(validate("rollback-substitution-unjustified")),
        )
        for name in (
            "complete-publication-correction",
            "complete-transition-alternate",
        ):
            with self.subTest(record=name):
                self.assertEqual(validate(name)["ordered_findings"], [])

    def test_rollback_kind_and_availability_must_agree(self) -> None:
        text = fixture("complete-technical-rollback").replace(
            "Kind: rollback; Rollback: available", "Kind: rollback; Rollback: inapplicable", 1
        )
        result = delivery_contract.validate_record(text)
        self.assertIn("delivery-contingency-contradictory", codes(result))


# ---------------------------------------------------------------------------
# The external-action boundary
# ---------------------------------------------------------------------------


class ExternalActionBoundaryTest(unittest.TestCase):
    def test_declined_external_action_is_not_delivered_never_verified(self) -> None:
        result = validate("declined-external-action")
        self.assertEqual(result["outcome_state"], "not-delivered")
        self.assertNotEqual(result["outcome_state"], "verified")
        self.assertIn("delivery-authority-declined", codes(result))
        self.assertEqual(result["gate"], "blocked")

    def test_pending_authority_stays_pending(self) -> None:
        result = validate("pending-authority-contradicted")
        self.assertEqual(result["outcome_state"], "pending-authority")
        self.assertIn("delivery-authority-pending", codes(result))

    def test_verification_claimed_without_authority_is_a_contradiction(self) -> None:
        result = validate("pending-authority-contradicted")
        self.assertIn("delivery-authority-contradicted", codes(result))

    def test_incomplete_artifact_cannot_carry_a_passing_target_observation(self) -> None:
        text = fixture("complete-technical-rollback").replace(
            "- Artifact: complete;", "- Artifact: incomplete;", 1
        )
        result = delivery_contract.validate_record(text)
        self.assertIn("delivery-artifact-incomplete", codes(result))
        self.assertIn("delivery-artifact-contradicted", codes(result))
        self.assertEqual(result["outcome_state"], "unverified")

    def test_validator_never_reports_performing_a_delivery(self) -> None:
        for path in sorted(FIXTURES.glob("*.md")):
            with self.subTest(record=path.stem):
                result = delivery_contract.validate_record(
                    path.read_text(encoding="utf-8")
                )
                self.assertIs(result["delivery_performed"], False)

    def test_validator_module_holds_no_external_action_capability(self) -> None:
        """Static guard: the validator can only read contained files.

        An unattended run may prepare and validate a delivery record; it may
        not cross the external-action boundary. A validator with no network,
        process, or write API cannot cross it whatever its input says.
        """
        source = (REPO_ROOT / "cli" / "delivery_contract.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "import socket",
            "import subprocess",
            "import urllib",
            "import http",
            "import smtplib",
            "import shutil",
            "os.system",
            "write_text",
            "write_bytes",
            "mkdir",
            "unlink",
        ):
            self.assertNotIn(
                forbidden, source, msg=f"validator must not reach for `{forbidden}`"
            )

    def test_declared_boundary_states_it_performs_no_delivery(self) -> None:
        boundaries = delivery_contract.load_delivery_contract()["boundaries"]
        self.assertIs(boundaries["performs_delivery"], False)
        self.assertEqual(boundaries["authority_source"], "operator")
        self.assertEqual(boundaries["unattended"], "prepare-and-validate-only")


# ---------------------------------------------------------------------------
# Justified not-applicable vs. missing
# ---------------------------------------------------------------------------


class NotApplicableTest(unittest.TestCase):
    def test_justified_not_applicable_passes(self) -> None:
        result = validate("not-applicable")
        self.assertEqual(result["applicability"], "not-applicable")
        self.assertEqual(result["gate"], "pass")
        for field in ("artifact_state", "outcome_state", "follow_up_state"):
            self.assertEqual(result[field], "not-applicable")

    def test_not_applicable_is_distinct_from_missing(self) -> None:
        self.assertNotEqual(
            validate("not-applicable")["applicability"],
            validate("undeclared")["applicability"],
        )
        self.assertEqual(validate("not-applicable")["gate"], "pass")
        self.assertEqual(validate("undeclared")["gate"], "blocked")

    def test_an_empty_justification_is_reported_once(self) -> None:
        """One missing justification is one defect, not two.

        The row's own not-applicable rule owns the justification; validating
        it a second time as an ordinary field would double-count it.
        """
        result = delivery_contract.validate_record(
            "## Delivery contract\n\n- Delivery: not-applicable; Justification: TBD\n"
        )
        self.assertEqual(codes(result), ["delivery-not-applicable-unjustified"])

    def test_a_not_applicable_follow_up_carries_no_other_parts(self) -> None:
        text = fixture("complete-publication-correction").replace(
            "- Follow-up: not-applicable; Justification:",
            "- Follow-up: not-applicable; Status: open; Justification:",
            1,
        )
        result = delivery_contract.validate_record(text)
        self.assertIn("delivery-field-unknown", codes(result))

    def test_unjustified_not_applicable_fails_closed(self) -> None:
        result = delivery_contract.validate_record(
            "## Delivery contract\n\n- Delivery: not-applicable\n"
        )
        self.assertIn("delivery-not-applicable-unjustified", codes(result))
        self.assertEqual(result["artifact_state"], "unknown")

    def test_not_applicable_carrying_obligations_fails_closed(self) -> None:
        result = validate("not-applicable-contradicted")
        self.assertIn("delivery-not-applicable-contradicted", codes(result))
        self.assertEqual(result["gate"], "blocked")


# ---------------------------------------------------------------------------
# Containment and compatibility
# ---------------------------------------------------------------------------


class ContainmentAndMigrationTest(unittest.TestCase):
    def test_relative_project_root_is_refused(self) -> None:
        with self.assertRaises(delivery_contract.DeliveryContractError) as ctx:
            delivery_contract.validate_plan(Path("relative/project"))
        self.assertEqual(ctx.exception.code, "delivery-path-invalid")

    def test_plan_resolving_outside_the_project_root_is_refused(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            outside = scaffold.root / "outside-plan.md"
            outside.write_text(fixture("complete-technical-rollback"), encoding="utf-8")
            (scaffold.project_root / "IMPLEMENTATION_PLAN.md").symlink_to(outside)
            with self.assertRaises(delivery_contract.DeliveryContractError) as ctx:
                delivery_contract.validate_plan(scaffold.project_root)
            self.assertEqual(ctx.exception.code, "delivery-path-outside-root")

    def test_absent_plan_is_undeclared_not_a_crash(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            result = delivery_contract.validate_plan(scaffold.project_root)
            self.assertEqual(codes(result), ["delivery-contract-undeclared"])

    def test_migration_is_one_authoring_action_with_one_accepted_form(self) -> None:
        compatibility = delivery_contract.load_delivery_contract()["compatibility"]
        self.assertEqual(compatibility["legacy_state"], "undeclared")
        self.assertEqual(compatibility["accepted_forms"], 1)
        self.assertIs(compatibility["dual_form"], False)
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("IMPLEMENTATION_PLAN.md", fixture("undeclared"))
            before = delivery_contract.validate_plan(scaffold.project_root)
            self.assertEqual(before["gate"], "blocked")
            scaffold.write(
                "IMPLEMENTATION_PLAN.md", fixture("complete-technical-rollback")
            )
            after = delivery_contract.validate_plan(scaffold.project_root)
            self.assertEqual(after["gate"], "pass")

    def test_shipped_plan_template_carries_the_section(self) -> None:
        template = (REPO_ROOT / "templates" / "IMPLEMENTATION_PLAN.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"## {delivery_contract.section_heading()}", template)
        # An unfilled template is a placeholder record, so it must fail closed
        # rather than pass a plan that only copied the shape.
        self.assertEqual(delivery_contract.validate_record(template)["gate"], "blocked")


# ---------------------------------------------------------------------------
# Integration: closeout, compact status, review context
# ---------------------------------------------------------------------------


class FailureClassificationTest(unittest.TestCase):
    """The contract classifies every finding it can emit.

    The classification is what separates "this plan has not adopted the
    contract" from "this plan adopted it and is honestly mid-flight", so a
    finding without a declared class must fail closed rather than default to
    either side.
    """

    def test_every_declared_failure_carries_a_declared_class(self) -> None:
        registry = delivery_contract.load_delivery_contract()
        classes = registry["failure_classes"]
        self.assertEqual(
            set(classes),
            {delivery_contract.RECORD_FORM, delivery_contract.RECORD_SEMANTICS},
        )
        for entry in registry["failures"]:
            with self.subTest(code=entry["code"]):
                self.assertIn(entry.get("class"), classes)
                self.assertEqual(
                    delivery_contract.failure_class(entry["code"]),
                    entry["class"],
                )

    def test_an_undeclared_code_fails_closed(self) -> None:
        with self.assertRaises(delivery_contract.DeliveryContractError) as ctx:
            delivery_contract.failure_class("delivery-not-a-real-code")
        self.assertEqual(ctx.exception.code, "delivery-contract-invalid")

    def test_unauthored_records_are_not_declared(self) -> None:
        for name in (
            "undeclared",
            "incomplete-technical",
            "incomplete-nontechnical",
            "not-applicable-contradicted",
            "contingency-without-trigger",
            "unbounded-follow-up",
            "rollback-substitution-unjustified",
        ):
            with self.subTest(record=name):
                self.assertFalse(
                    delivery_contract.record_is_declared(validate(name))
                )

    def test_honest_mid_flight_records_are_declared_but_blocked(self) -> None:
        """Adoption and satisfaction are different questions.

        A plan that records an unverified target, a declined external action,
        or an unavailable owner has adopted the contract — it is saying
        something true. It is blocked at closeout, not at adoption.
        """
        for name in (
            "artifact-complete-outcome-unverified",
            "declined-external-action",
            "unavailable-owner",
            "self-certified-evidence",
            "changed-target",
        ):
            with self.subTest(record=name):
                result = validate(name)
                self.assertTrue(delivery_contract.record_is_declared(result))
                self.assertEqual(result["gate"], "blocked")

    def test_the_unfilled_template_is_not_an_adopted_record(self) -> None:
        template = (REPO_ROOT / "templates" / "IMPLEMENTATION_PLAN.md").read_text(
            encoding="utf-8"
        )
        self.assertFalse(
            delivery_contract.record_is_declared(
                delivery_contract.validate_record(template)
            )
        )


class SchemaMigrationGateTest(unittest.TestCase):
    """The breaking plan-format change is gated by the project schema marker.

    Reproduces the failure this gate exists to prevent: a project whose plan
    predates the delivery contract must not read as canonical and task-ready
    while closeout rejects it for a section the plan was never asked to carry.
    """

    STALE = _TOML.replace("v0.12.0", "v0.11.0")

    def _plan_project(self, scaffold, plan_text: str) -> Path:
        scaffold.write("IMPLEMENTATION_PLAN.md", plan_text)
        scaffold.write(
            "phases/PHASE-01.md",
            "# PHASE-01: Deliver\n\n## Exit criteria\n\n- `TASK-01-001`\n",
        )
        scaffold.write(
            "tasks/done/TASK-01-001.md", "# TASK-01-001: done\n\nPhase: PHASE-01\n"
        )
        scaffold.write(
            "STANDARDS.md", "# Standards: P\n\n## Working standards\n\nRules.\n"
        )
        return scaffold.project_root

    def _migration(self, project_root: Path, home: Path):
        from cli import config_migration

        return config_migration.plan_configuration_migration(
            project_root, home_root=home
        )

    def test_pre_contract_plan_is_offered_migration_not_treated_as_current(
        self,
    ) -> None:
        with project_scaffold(cartopian_toml=self.STALE) as scaffold:
            home = scaffold.root / "home"
            (home / ".cartopian").mkdir(parents=True, exist_ok=True)
            root = self._plan_project(scaffold, fixture("undeclared"))

            # 1. Migration is required and refused, not a canonical no-op.
            plan = self._migration(root, home)
            self.assertEqual(plan.status, "refused")
            self.assertEqual(plan.detected_schema_version, "v0.11.0")
            self.assertEqual(plan.current_schema_version, "v0.12.0")
            self.assertIsNone(plan.marker_update)
            self.assertEqual(
                plan.diagnostics[0]["code"],
                "delivery-contract-migration-required",
            )

            # 2. Closeout rejects the same project for the same contract, so
            #    the two surfaces agree instead of contradicting each other.
            records, _ = invoke(close_audit, project_path=str(root))
            self.assertFalse(records[0]["closable"])
            self.assertIn(
                "delivery-contract-undeclared",
                " ".join(records[0]["blocking_reasons"]),
            )

    def test_declaring_the_contract_advances_the_marker_idempotently(self) -> None:
        with project_scaffold(cartopian_toml=self.STALE) as scaffold:
            home = scaffold.root / "home"
            (home / ".cartopian").mkdir(parents=True, exist_ok=True)
            root = self._plan_project(
                scaffold, fixture("complete-technical-rollback")
            )
            from cli import config_migration

            plan = self._migration(root, home)
            self.assertEqual(plan.status, "planned", msg=plan.diagnostics)
            self.assertEqual(
                [entry.identity for entry in plan.entries],
                ["config-v0.11-to-v0.12"],
            )
            result = config_migration.execute_configuration_migration(
                root, plan, home_root=home
            )
            self.assertEqual(result["status"], "complete")

            again = self._migration(root, home)
            self.assertEqual(again.status, "noop")
            self.assertEqual(again.entries, ())

            # Recovered end to end: the gate passes and closeout is clear.
            records, rc = invoke(close_audit, project_path=str(root))
            self.assertEqual(rc, 0)
            self.assertTrue(records[0]["closable"], msg=records[0]["blocking_reasons"])

    def test_migration_writes_only_the_marker(self) -> None:
        """Recovery never authors a delivery record on the operator's behalf."""
        with project_scaffold(cartopian_toml=self.STALE) as scaffold:
            home = scaffold.root / "home"
            (home / ".cartopian").mkdir(parents=True, exist_ok=True)
            root = self._plan_project(scaffold, fixture("not-applicable"))
            plan_before = (root / "IMPLEMENTATION_PLAN.md").read_bytes()
            from cli import config_migration

            config_migration.execute_configuration_migration(
                root, self._migration(root, home), home_root=home
            )
            self.assertEqual(
                (root / "IMPLEMENTATION_PLAN.md").read_bytes(), plan_before
            )

    def test_refusal_leaves_the_marker_untouched(self) -> None:
        with project_scaffold(cartopian_toml=self.STALE) as scaffold:
            home = scaffold.root / "home"
            (home / ".cartopian").mkdir(parents=True, exist_ok=True)
            root = self._plan_project(scaffold, fixture("incomplete-technical"))
            before = (root / "cartopian.toml").read_bytes()
            plan = self._migration(root, home)
            self.assertEqual(plan.status, "refused")
            self.assertEqual((root / "cartopian.toml").read_bytes(), before)
            # Repeating a refusal is deterministic, not progressive.
            self.assertEqual(
                self._migration(root, home).diagnostics[0]["code"],
                plan.diagnostics[0]["code"],
            )


class CloseoutIntegrationTest(unittest.TestCase):
    def test_complete_record_leaves_the_project_closable(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            path = planned_project(scaffold, "complete-technical-rollback")
            records, rc = invoke(close_audit, project_path=path)
            self.assertEqual(rc, 0)
            record = records[0]
            self.assertTrue(record["closable"], msg=record["blocking_reasons"])
            self.assertEqual(record["delivery"]["gate"], "pass")

    def test_unmet_delivery_obligation_blocks_closeout(self) -> None:
        for name in (
            "undeclared",
            "incomplete-technical",
            "incomplete-nontechnical",
            "declined-external-action",
            "artifact-complete-outcome-unverified",
        ):
            with self.subTest(record=name):
                with project_scaffold(cartopian_toml=_TOML) as scaffold:
                    path = planned_project(scaffold, name)
                    records, _ = invoke(close_audit, project_path=path)
                    record = records[0]
                    self.assertFalse(record["closable"])
                    delivery_blockers = [
                        reason
                        for reason in record["blocking_reasons"]
                        if reason.startswith("delivery gate blocks closeout:")
                    ]
                    self.assertEqual(
                        len(delivery_blockers),
                        len(record["delivery"]["ordered_findings"]),
                    )

    def test_declined_archive_does_not_erase_the_delivery_obligation(self) -> None:
        """Archival is optional; the obligation is not.

        A plan closed without an archive runs the same gate, so declining the
        snapshot cannot quietly discharge a delivery that never happened.
        """
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            path = planned_project(scaffold, "declined-external-action")
            self.assertFalse((scaffold.project_root / "archive").exists())
            records, _ = invoke(close_audit, project_path=path)
            self.assertFalse(records[0]["closable"])
            self.assertEqual(records[0]["delivery"]["outcome_state"], "not-delivered")

    def test_no_plan_record_carries_a_null_delivery_field(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            records, rc = invoke(close_audit, project_path=str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertIn("delivery", records[0])
            self.assertIsNone(records[0]["delivery"])


class CompactStatusTest(unittest.TestCase):
    COMPACT_KEYS = {
        "applicability",
        "artifact_state",
        "outcome_state",
        "follow_up_state",
        "gate",
        "finding_count",
        "first_finding",
    }

    def test_compose_state_carries_bounded_delivery_status(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            path = planned_project(scaffold, "artifact-complete-outcome-unverified")
            records, rc = invoke(compose_state, project_path=path)
            self.assertEqual(rc, 0)
            status = records[0]["delivery"]
            self.assertEqual(set(status), self.COMPACT_KEYS)
            self.assertEqual(status["artifact_state"], "complete")
            self.assertEqual(status["outcome_state"], "unverified")
            self.assertEqual(status["first_finding"], "delivery-verification-not-run")

    def test_compact_status_omits_the_record_and_its_findings(self) -> None:
        status = delivery_contract.compact_status(
            validate("incomplete-nontechnical")
        )
        self.assertNotIn("ordered_findings", status)
        self.assertNotIn("contract_identity", status)
        self.assertGreater(status["finding_count"], 1)

    def test_no_plan_state_carries_a_null_delivery_field(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            records, rc = invoke(compose_state, project_path=str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertIsNone(records[0]["delivery"])
            self.assertIsNone(records[0]["rendered_body"])

    def test_startup_exposes_status_without_halting_the_session(self) -> None:
        """The delivery gate is a closeout gate, not a startup blocker."""
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            path = planned_project(scaffold, "incomplete-technical")
            scaffold.write(
                "tasks/open/TASK-01-002.md",
                "# TASK-01-002: next\n\nPhase: PHASE-01\n",
            )
            records, rc = invoke(next_action, project_path=path)
            self.assertEqual(rc, 0)
            record = records[0]
            self.assertEqual(set(record["delivery"]), self.COMPACT_KEYS)
            self.assertEqual(record["delivery"]["gate"], "blocked")
            for blocker in record["blockers"]:
                self.assertNotIn("delivery gate", blocker)


class ReviewContextTest(unittest.TestCase):
    def _context(self, scaffold, unit: str, argv) -> dict:
        # Review context binds the unit's captured operator request; without it
        # the projection refuses before any delivery record is read.
        scaffold.capture_request(
            request_id="REQUEST-001",
            unit=unit,
            text="Deliver the outcome and close the plan.",
        )
        code, records, err = run_cli(
            "review-context", str(scaffold.project_root), *argv
        )
        self.assertEqual(code, 0, msg=err)
        return records[0]

    def test_planning_review_receives_the_delivery_contract_and_its_evidence(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            planned_project(scaffold, "complete-technical-rollback")
            record = self._context(
                scaffold,
                "planning:PLAN-001",
                ["--review-kind", "planning", "--checkpoint", "PLAN-001"],
            )
            projection = record["delivery_contract"]
            self.assertEqual(projection["gate"], "pass")
            self.assertIn("Acceptance evidence:", projection["section"])
            self.assertIsNone(projection["section_omitted"])
            self.assertEqual(
                projection["context_bytes"],
                len(projection["section"].encode("utf-8")),
            )

    def test_projection_carries_the_section_not_the_plan(self) -> None:
        content = fixture("complete-technical-rollback")
        projection = delivery_contract.review_projection(
            delivery_contract.validate_record(content), content
        )
        self.assertNotIn("## Phase sequence", projection["section"])
        self.assertNotIn("# Implementation Plan", projection["section"])

    def test_large_section_is_carried_complete(self) -> None:
        # Review evidence is never omitted by a byte threshold: a section far
        # past the removed 8 KiB bound is projected complete, with its
        # measured size as nonblocking telemetry.
        padded = fixture("complete-technical-rollback").replace(
            "## Delivery contract\n",
            "## Delivery contract\n\n" + ("padding " * (65536 // 4)) + "\n",
            1,
        )
        projection = delivery_contract.review_projection(
            delivery_contract.validate_record(padded), padded
        )
        self.assertIsNotNone(projection["section"])
        self.assertIsNone(projection["section_omitted"])
        self.assertGreater(projection["context_bytes"], 65536)
        self.assertIn("padding", projection["section"])

    def test_task_closure_review_receives_no_delivery_contract(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            planned_project(scaffold, "complete-technical-rollback")
            task = scaffold.project_root / "tasks" / "open" / "TASK-01-003.md"
            task.write_text(
                "# TASK-01-003: closure\n\nPhase: PHASE-01\n", encoding="utf-8"
            )
            record = self._context(
                scaffold,
                "task:TASK-01-003",
                ["--review-kind", "task-closure", "--task", str(task)],
            )
            self.assertIsNone(record["delivery_contract"])


class CliMcpParityTest(unittest.TestCase):
    def test_subcommand_is_registered(self) -> None:
        self.assertIn("validate-delivery", SUBCOMMANDS)
        args = build_parser().parse_args(["validate-delivery", "/tmp/project"])
        self.assertEqual(args.cmd, "validate-delivery")

    def test_cli_exits_non_zero_when_the_gate_is_blocked(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            path = planned_project(scaffold, "undeclared")
            code, records, err = run_cli("validate-delivery", path)
            self.assertEqual(code, 1)
            self.assertIn("delivery-contract-undeclared", err)
            self.assertEqual(records[0]["action"], "validate-delivery")
            self.assertEqual(records[0]["gate"], "blocked")

    def test_cli_exits_zero_when_the_gate_passes(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            path = planned_project(scaffold, "complete-publication-correction")
            code, records, err = run_cli("validate-delivery", path)
            self.assertEqual(code, 0, msg=err)
            self.assertEqual(records[0]["gate"], "pass")

    def test_relative_path_is_a_usage_error(self) -> None:
        code, _records, err = run_cli("validate-delivery", "relative/path")
        self.assertEqual(code, 2)
        self.assertIn("must be an absolute path", err)

    def test_mcp_tool_returns_the_same_record_as_the_cli(self) -> None:
        from mcp_server import server

        self.assertIn(
            "validate_delivery", {tool["name"] for tool in server.list_tools()}
        )
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            path = planned_project(scaffold, "incomplete-nontechnical")
            cli_code, cli_records, _ = run_cli("validate-delivery", path)
            result = server.call_tool("validate_delivery", {"project_root": path})
            structured = result["structuredContent"]
            self.assertEqual(structured["exit_code"], cli_code)
            self.assertEqual(structured["records"], cli_records)
            self.assertTrue(result["isError"])

    def test_close_audit_and_the_gate_agree_on_the_verdict(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            path = planned_project(scaffold, "self-certified-evidence")
            _code, gate_records, _ = run_cli("validate-delivery", path)
            audit_records, _ = invoke(close_audit, project_path=path)
            self.assertEqual(
                gate_records[0]["ordered_findings"],
                audit_records[0]["delivery"]["ordered_findings"],
            )


class CrossSurfaceParityTest(unittest.TestCase):
    """The gate crosses protocol, template, skill, CLI, and MCP surfaces.

    A behavior that lives on five surfaces drifts on four of them unless the
    row labels and the command name are checked in one place.
    """

    def test_row_labels_are_identical_on_every_authoring_surface(self) -> None:
        labels = [
            str(row["label"])
            for row in delivery_contract.load_delivery_contract()["record"]["rows"]
        ]
        template = (REPO_ROOT / "templates" / "IMPLEMENTATION_PLAN.md").read_text(
            encoding="utf-8"
        )
        projection = PROJECTION_PATH.read_text(encoding="utf-8")
        for label in labels:
            with self.subTest(label=label):
                self.assertIn(f"- {label}:", template)
                self.assertIn(label, projection)

    def test_closeout_and_planning_skills_name_the_gate(self) -> None:
        close_plan = (REPO_ROOT / "skills" / "close-plan.md").read_text(
            encoding="utf-8"
        )
        plan_project = (REPO_ROOT / "skills" / "plan-project.md").read_text(
            encoding="utf-8"
        )
        for text in (close_plan, plan_project):
            self.assertIn("cartopian validate-delivery", text)
            self.assertIn("cartopian://protocol/DELIVERY", text)
        self.assertIn("delivery gate blocks closeout:", close_plan)

    def test_conventions_carries_the_delivery_gate_and_its_closeout_rule(self) -> None:
        text = (REPO_ROOT / "protocol" / "CONVENTIONS.md").read_text(encoding="utf-8")
        self.assertIn("## Delivery Gate", text)
        self.assertIn("protocol/delivery-contract.json", text)
        lifecycle = text.split("## Plan Lifecycle", 1)[1].split("## Plan Archives", 1)[0]
        self.assertIn("delivery gate", lifecycle)

    def test_protocol_doc_is_exposed_as_a_resource(self) -> None:
        from mcp_server import server

        uris = {resource["uri"] for resource in server.list_resources()}
        self.assertIn("cartopian://protocol/DELIVERY", uris)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
