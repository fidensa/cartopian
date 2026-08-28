"""Reference-output and semantic tests for `cartopian compose-assignment-prompt`.

Covers the complete generation path required by the assignment-prompt
contract: deterministic composition from fixed task/specification/standards/
configuration fixtures, comparison of the stable assignee-facing output with
approved reference prompts (dynamic values normalized), semantic assertions
over the composed prompt and its bound trace receipt, fail-closed validation,
the mediated `write-prompt --composed-file` flow, budget anchoring, and
CLI/MCP output equivalence.

Regenerate the reference fixtures deliberately (after reviewing the diff)
with::

    CARTOPIAN_REGEN_FIXTURES=1 python3 -m pytest \
        tests/cli/commands/test_compose_assignment_prompt.py
"""
import argparse
import contextlib
import io
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from cli import deidentify, judgment_guidance, practice_packs, prompt_composer, risk_contract
from cli.commands import capture_request as capture_request_command
from cli.commands import compose_assignment_prompt, write_prompt
from cli.main import EXIT_FAIL, EXIT_OK
from tests.scaffold import project_scaffold

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "assignment_prompts"
_REGEN = os.environ.get("CARTOPIAN_REGEN_FIXTURES") == "1"

# The scaffold's temp directory is the only dynamic value in a composed
# prompt (no dates, hashes, or installed versions reach the assignee body).
_SCAFFOLD_PATH_RE = re.compile(r"\S*cartopian-scaffold-[^/\s]+")

_TOML_REVIEW_REQUIRED = (
    "[project]\n"
    'id = "compose-proj"\n'
    'name = "Compose Project"\n'
    'project_schema_version = "v0.12.0"\n'
    'work_roots = ["tool-repo"]\n'
    "\n"
    "[roles.coder]\n"
    'description = "Implements tasks per spec."\n'
    'auto_launch = ["task_run"]\n'
    'agent = "cartopian-claude"\n'
    "\n"
    "[reviews]\n"
    'planning = "off"\n'
    'task_closure = "required"\n'
    'task_role = "coder"\n'
)

_TOML_REVIEW_OFF = _TOML_REVIEW_REQUIRED.replace(
    'task_closure = "required"\n' 'task_role = "coder"\n',
    'task_closure = "off"\n',
)

_FULL_TASK = """# TASK-01-002: Add config loader

Phase: PHASE-01
Plan ref: n/a
Work root: tool-repo
Assignee: coder
Spec: SPEC-01-002.md
Depends on: n/a
Blocked by: n/a
Created: 2026-05-18
Evidence gate: required
Source guidance: task

## Goal

Implement the configuration loader described by the specification.

## Evidence gate

Run the loader unit tests before and after the change; capture the failing and passing runs.

## Risk observations

- consequence-reach: project-internal; Fact: loader is consumed only by in-repo tools
- reversibility: direct-undo; Fact: single-module change under version control
- authority: covered; Fact: approved plan item covers the loader
- ambiguity: confirmed; Fact: spec names inputs, outputs, and failure behavior
- evidence-coverage: deterministic; Fact: unit tests assert exact outputs

## Judgment envelope

- lifecycle-boundaries: evidence-and-review-gate
- open-failure-conditions: evidence-self-certified-or-missing

## Practice-pack envelope

- primary-outcomes: software-behavior-change
- artifact-kinds: source-code
- incidental-terms: none
- exclusions: none
- lifecycle-substrate-activities: none
- domain-scopes: none
- authorized-profile-hint: software

## Source guidance

### Authoritative sources

- Identity: TOML v1.0.0 specification; Applicable context: v1.0.0 (2021-01-11); Status: current; Scope: configuration file syntax and semantics

### Conflict resolution

- Status: none; Rule: single governing source; Decision: n/a

### Unverified claims

- none

## Acceptance

- [ ] The loader parses a valid config file into the documented structure.
- [ ] Invalid input produces the documented error, not a traceback.

## References

- IMPLEMENTATION_PLAN.md section "Loader".
"""

_FULL_SPEC = """# SPEC-01-002: Config loader

Status: locked
Profile: general
Author: PM
Reviewer: operator
Date: 2026-05-18
Plan ref: BUILD-01-002
Source: n/a
Source guidance: n/a

## Problem

Tools re-parse configuration ad hoc; behavior drifts between them.

## Goal

One loader with documented structure and failure behavior.

## Non-goals

- No schema migration tooling.

## Interface

`load_config(path) -> Config`; missing file raises ConfigMissing; malformed TOML raises ConfigInvalid with line context.

## Constraints

Stdlib only.

## References

- IMPLEMENTATION_PLAN.md section "Loader".

## Examples / acceptance

- [ ] The loader parses a valid config file into the documented structure.
- `load_config(missing)` raises ConfigMissing.

## Open questions

## Review checklist

- [ ] Scope is consistent with `IMPLEMENTATION_PLAN.md`.
"""

_STANDARDS = """# Standards: Compose Project

## Tools and dependencies

Python 3.11+, stdlib only.

## Working standards

Applies to: software-behavior-change

RED/GREEN TDD: write the failing test first; commit messages describe the unit of work.

## Marketing voice

Applies to: audience-facing-claim

Claims must be substantiated before publication.
"""

_MINIMAL_TASK = """# TASK-02-001: Tidy fixture data

Phase: PHASE-02
Plan ref: n/a
Work root: tool-repo
Assignee: coder
Spec: none
Depends on: n/a
Blocked by: n/a
Created: 2026-05-18
Evidence gate: n/a
Source guidance: n/a

## Goal

Normalize the fixture data files to one record per line.

## Evidence gate

n/a — mechanical normalization verified by inspection.

## Risk observations

- consequence-reach: local-artifact; Fact: fixtures are test-only inputs
- reversibility: direct-undo; Fact: files are under version control
- authority: covered; Fact: approved plan item covers fixture cleanup
- ambiguity: confirmed; Fact: target format is stated in the goal
- evidence-coverage: direct-observation; Fact: normalized files are inspected directly

## Judgment envelope

- lifecycle-boundaries: none
- open-failure-conditions: none

## Practice-pack envelope

- primary-outcomes: none
- artifact-kinds: none
- incidental-terms: none
- exclusions: none
- lifecycle-substrate-activities: none
- domain-scopes: none
- authorized-profile-hint: none

## Acceptance

- [ ] Every fixture file holds one record per line.
"""

_OPERATOR_REQUEST = (
    "Build the config loader; stdlib only, and keep the error messages actionable."
)

# The permitted provenance form: a whole-governance-document marker used as a
# source identity inside Source guidance, byte-preserved through composition.
_PROTOCOL_IDENTITY_ROW = (
    "- Identity: Cartopian protocol/CONVENTIONS.md; Applicable context: "
    "installed Cartopian v1.6.40; Status: current; Scope: operator authority "
    "and evidence discipline"
)

_PROTOCOL_SOURCE_TASK = _FULL_TASK.replace(
    "- Identity: TOML v1.0.0 specification; Applicable context: v1.0.0 "
    "(2021-01-11); Status: current; Scope: configuration file syntax and "
    "semantics",
    _PROTOCOL_IDENTITY_ROW,
)

_AGENTS_ONLY_STANDARDS = _STANDARDS.replace(
    "RED/GREEN TDD: write the failing test first; commit messages describe "
    "the unit of work.",
    "Follow the repository's AGENTS.md when implementing in the tool repo.\n"
    "\n"
    "RED/GREEN TDD: write the failing test first; commit messages describe "
    "the unit of work.",
)

_CONTAMINATED_STANDARDS = _AGENTS_ONLY_STANDARDS.replace(
    "Follow the repository's AGENTS.md when implementing in the tool repo.",
    "Follow the repository's AGENTS.md and protocol/CONVENTIONS.md when "
    "implementing in the tool repo.",
)


def _capture(scaffold, request_id, unit, text, correction_of=None):
    source = scaffold.root / f"capture-{request_id}-{'c' if correction_of else 'o'}.txt"
    source.write_text(text, encoding="utf-8")
    args = argparse.Namespace(
        project_root=str(scaffold.project_root),
        request_id=request_id,
        unit=unit,
        content_file=str(source),
        correction_of=correction_of,
        captured_at="2026-07-27T12:00:00Z",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in capture_request_command.NON_OPERATOR_MARKERS
    }
    with (
        mock.patch.dict(os.environ, env, clear=True),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        code = capture_request_command.handler(args)
    if code != 0:
        raise AssertionError(f"capture-request {request_id} failed")


def _prepare_work_root(scaffold):
    work_root = scaffold.root / "tool-repo"
    work_root.mkdir(exist_ok=True)
    scaffold.write(
        "cartopian.local.toml", f'[work_roots]\ntool-repo = "{work_root}"\n'
    )
    return work_root


def _build_full(scaffold) -> Path:
    _prepare_work_root(scaffold)
    task_path = scaffold.write("tasks/in-progress/TASK-01-002.md", _FULL_TASK)
    scaffold.write("specs/SPEC-01-002.md", _FULL_SPEC)
    scaffold.write("STANDARDS.md", _STANDARDS)
    _capture(scaffold, "REQUEST-001", "task:TASK-01-002", _OPERATOR_REQUEST)
    _capture(
        scaffold, "REQUEST-001", "task:TASK-01-002", "continue",
        correction_of="REQUEST-001",
    )
    return task_path


def _build_minimal(scaffold) -> Path:
    _prepare_work_root(scaffold)
    task_path = scaffold.write("tasks/in-progress/TASK-02-001.md", _MINIMAL_TASK)
    _capture(
        scaffold, "REQUEST-001", "task:TASK-02-001",
        "Normalize the fixture data files.",
    )
    return task_path


def _normalize(prompt: str) -> str:
    return _SCAFFOLD_PATH_RE.sub("<SCAFFOLD>", prompt)


def _invoke_cli(task_path: str, role: str):
    args = argparse.Namespace(task_path=task_path, role=role)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = compose_assignment_prompt.handler(args)
    records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return records, err.getvalue(), code


def _write_prompt_args(scaffold, task_path, **overrides):
    base = dict(
        project_root=str(scaffold.project_root),
        content=None,
        content_file=None,
        composed_file=None,
        prompt_id="PROMPT-01-002",
        review_kind=None,
        task=str(task_path),
        checkpoint=None,
        phase=None,
        plan_ref=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestReferenceOutput(unittest.TestCase):
    """Stable assignee-facing output matches the approved reference prompts."""

    def _assert_matches_reference(self, name: str, prompt: str) -> None:
        reference_path = FIXTURES_DIR / name
        normalized = _normalize(prompt)
        if _REGEN:
            reference_path.parent.mkdir(parents=True, exist_ok=True)
            reference_path.write_text(normalized, encoding="utf-8")
        self.assertTrue(
            reference_path.is_file(),
            msg=f"missing reference fixture {reference_path}; regenerate with "
            "CARTOPIAN_REGEN_FIXTURES=1",
        )
        self.assertEqual(
            normalized, reference_path.read_text(encoding="utf-8"),
            msg=f"composed prompt drifted from approved reference {name}",
        )

    def test_full_fixture_matches_reference(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = _build_full(scaffold)
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])
            self._assert_matches_reference(
                "full-task.md", record["assignee_prompt"]
            )

    def test_minimal_fixture_matches_reference(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            task_path = _build_minimal(scaffold)
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])
            self._assert_matches_reference(
                "minimal-task.md", record["assignee_prompt"]
            )

    def test_composition_is_deterministic(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = _build_full(scaffold)
            first = prompt_composer.compose(task_path, "coder")
            second = prompt_composer.compose(task_path, "coder")
            self.assertEqual(first["assignee_prompt"], second["assignee_prompt"])
            self.assertEqual(first["content_identity"], second["content_identity"])


class TestSemanticContract(unittest.TestCase):
    """Semantic assertions over the composed prompt and its trace receipt."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._scaffold = project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED)
        cls._task_path = _build_full(cls._scaffold)
        cls.record = prompt_composer.compose(cls._task_path, "coder")
        cls.prompt = cls.record["assignee_prompt"]
        cls.receipt = cls.record["trace_receipt"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._scaffold.cleanup()

    def test_no_raw_diagnostic_json(self) -> None:
        self.assertNotIn("```json", self.prompt)
        self.assertNotIn('"context_receipt"', self.prompt)
        self.assertNotIn('"ordered_match_reasons"', self.prompt)
        for line in prompt_composer._outside_fences(self.prompt).splitlines():
            self.assertIsNone(
                re.match(r'^\s*[\[{]"', line),
                msg=f"raw JSON line in assignee prompt: {line!r}",
            )

    def test_source_guidance_appears_exactly_once(self) -> None:
        unfenced = prompt_composer._outside_fences(self.prompt)
        self.assertEqual(unfenced.count("### Authoritative sources"), 1)
        self.assertEqual(
            unfenced.count("Identity: TOML v1.0.0 specification"), 1
        )

    def test_all_acceptance_criteria_present(self) -> None:
        for criterion in (
            "The loader parses a valid config file into the documented structure.",
            "Invalid input produces the documented error, not a traceback.",
        ):
            self.assertIn(criterion, self.prompt)

    def test_no_duplicated_acceptance_criteria(self) -> None:
        self.assertEqual(
            self.prompt.count(
                "The loader parses a valid config file into the documented "
                "structure."
            ),
            1,
        )

    def test_reviewer_only_material_absent(self) -> None:
        self.assertNotIn("## Review checklist", self.prompt)
        self.assertNotIn("Preserved coder completion evidence", self.prompt)
        self.assertNotIn("<approve | request-changes | reject>", self.prompt)
        self.assertNotIn("reviews/REVIEW-", self.prompt)

    def test_only_applicable_standards_included(self) -> None:
        self.assertIn("RED/GREEN TDD", self.prompt)
        self.assertIn("Python 3.11+, stdlib only.", self.prompt)  # untagged
        self.assertNotIn("Marketing voice", self.prompt)
        self.assertNotIn("Claims must be substantiated", self.prompt)
        self.assertNotIn("Applies to:", self.prompt)  # tag lines never surface

    def test_only_active_guidance_included(self) -> None:
        self.assertIn("evidence-self-certified-or-missing", self.prompt)
        # Inactive judgment cards and their table rows never surface.
        self.assertNotIn("inferred-intent-not-confirmed", self.prompt)
        self.assertNotIn("artifact-mistaken-for-outcome", self.prompt)
        # Rejected pack candidates never surface.
        self.assertNotIn("marketing-claim", self.prompt)
        self.assertNotIn("rejected", self.prompt.lower())

    def test_practice_capsule_excludes_generic_bulk(self) -> None:
        self.assertIn("Practice profile: software-delivery", self.prompt)
        self.assertIn("Decision Gates", self.prompt)
        self.assertIn("Stop And Escalation", self.prompt)
        # The 5KB principles catalog and routing sections stay out.
        self.assertNotIn("Principles And Heuristics", self.prompt)
        self.assertNotIn("When To Apply", self.prompt)
        self.assertNotIn("Examples And Counterexamples", self.prompt)

    def test_no_hashes_budgets_or_receipts_in_prompt(self) -> None:
        self.assertNotIn("sha256:", self.prompt)
        self.assertNotIn("_bytes", self.prompt)
        self.assertNotIn("budget", self.prompt.lower())

    def test_no_pm_identifiers_except_report_slot(self) -> None:
        tokens = deidentify.list_identifiers(self.prompt)
        self.assertTrue(
            all(re.fullmatch(r"REPORT-\d{2}-\d{3}", token) for token in tokens),
            msg=f"unexpected PM identifiers in prompt: {tokens}",
        )

    def test_machine_trace_metadata_available_in_receipt(self) -> None:
        self.assertIsNotNone(self.receipt["risk"]["contract_id"])
        self.assertTrue(self.receipt["judgment"]["context_receipt"])
        self.assertTrue(self.receipt["judgment"]["inactive_cards"])
        self.assertTrue(self.receipt["practice_pack"]["context_receipt"])
        self.assertIn("rejected_candidates", self.receipt["practice_pack"])
        self.assertEqual(
            self.receipt["prompt_content_identity"],
            self.record["prompt_content_identity"],
        )
        # Verbatim operator evidence — including the low-information
        # correction — is retained in the receipt.
        texts = [
            item["text"]
            for item in self.receipt["request_context"]["request_trace"]["records"]
        ]
        self.assertIn(_OPERATOR_REQUEST, texts)
        self.assertIn("continue", texts)

    def test_report_skeleton_carries_prefilled_risk_identifiers(self) -> None:
        self.assertIn("## Risk-scaled evidence", self.prompt)
        self.assertIn(f"- Band: {self.receipt['risk']['band']}", self.prompt)

    def test_no_blanket_governance_reads(self) -> None:
        self.assertNotIn("protocol/CONVENTIONS.md", self.prompt)
        self.assertNotIn("cartopian://protocol/CONVENTIONS", self.prompt)

    def test_spec_planning_material_excluded(self) -> None:
        self.assertNotIn("Status: locked", self.prompt)
        self.assertNotIn("Author:", self.prompt)
        self.assertNotIn("Reviewer:", self.prompt)
        self.assertNotIn("Open questions", self.prompt)

    def test_sections_follow_contract_order(self) -> None:
        contract = prompt_composer.load_contract()
        names = [
            name
            for name, _ in prompt_composer.split_sections(self.prompt)
            if name != "(title)"
        ]
        order = contract["sections"]["order"]
        positions = [order.index(name) for name in names]
        self.assertEqual(positions, sorted(positions))
        for required in contract["sections"]["required"]:
            self.assertIn(required, names)


class TestComposedWriteFlow(unittest.TestCase):
    """`write-prompt --composed-file` verifies, writes, and appends the channel."""

    def test_composed_write_and_operator_evidence_scoping(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = _build_full(scaffold)
            record = prompt_composer.compose(task_path, "coder")
            composed_path = scaffold.root / "composed.json"
            composed_path.write_text(
                json.dumps({"action": "compose-assignment-prompt", **record}),
                encoding="utf-8",
            )
            args = _write_prompt_args(
                scaffold, task_path, composed_file=str(composed_path)
            )
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = write_prompt.handler(args)
            self.assertEqual(code, EXIT_OK, err.getvalue())
            written = (scaffold.prompts / "PROMPT-01-002.md").read_text(
                encoding="utf-8"
            )
            # Direct operator constraints are preserved in the coder channel…
            self.assertIn("## Original operator request (verbatim)", written)
            self.assertIn(_OPERATOR_REQUEST, written)
            # …while low-information inherited approvals are excluded from
            # the pasted channel but stay identity-bound.
            self.assertNotIn("\ncontinue\n", written)
            self.assertIn("Low-information operator approval", written)
            details = json.loads(out.getvalue().splitlines()[-1])["details"]
            self.assertEqual(
                details["composed"]["content_identity"],
                record["content_identity"],
            )

    def test_tampered_composed_record_is_refused(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = _build_full(scaffold)
            record = prompt_composer.compose(task_path, "coder")
            record["assignee_prompt"] += "\ninjected"
            composed_path = scaffold.root / "composed.json"
            composed_path.write_text(json.dumps(record), encoding="utf-8")
            args = _write_prompt_args(
                scaffold, task_path, composed_file=str(composed_path)
            )
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = write_prompt.handler(args)
            self.assertEqual(code, EXIT_FAIL)
            self.assertIn("no longer matches its recorded identity", err.getvalue())

    def test_authored_body_contamination_is_refused(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = _build_full(scaffold)
            args = _write_prompt_args(
                scaffold,
                task_path,
                content=(
                    "# Prompt\n\n## Your task\n\nDo it.\n\n"
                    '```json\n{"band": "critical", "context_receipt": {}}\n```\n'
                ),
            )
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = write_prompt.handler(args)
            self.assertEqual(code, EXIT_FAIL)
            self.assertIn("raw-diagnostic-json", err.getvalue())


class TestValidationFailClosed(unittest.TestCase):
    def test_spec_open_questions_refuse_composition(self) -> None:
        spec = _FULL_SPEC.replace(
            "## Open questions\n",
            "## Open questions\n\n- Which error type wraps OSError? (owner: PM)\n",
        )
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = _build_full(scaffold)
            scaffold.write("specs/SPEC-01-002.md", spec)
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "invalid")
            self.assertIn(
                "spec-open-questions-unresolved",
                [item["code"] for item in record["findings"]],
            )
            records, stderr, code = _invoke_cli(str(task_path), "coder")
            self.assertEqual(code, EXIT_FAIL)
            self.assertIn("spec-open-questions-unresolved", stderr)

    def test_unmapped_work_root_refuses(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = _build_full(scaffold)
            scaffold.write("cartopian.local.toml", "[work_roots]\n")
            with self.assertRaises(prompt_composer.ComposeRefusal) as ctx:
                prompt_composer.compose(task_path, "coder")
            # Config resolution may flag the unmapped root before the
            # composer's own guard; either way composition fails closed.
            self.assertIn(
                ctx.exception.code,
                ("work-root-unmapped", "project-config-invalid"),
            )

    def test_validator_flags_each_forbidden_content_class(self) -> None:
        contract = prompt_composer.load_contract()
        cases = {
            "raw-diagnostic-json": '{"band": "critical"}\n',
            "duplicate-source-guidance": (
                "### Authoritative sources\n\n- Identity: a; \n\n"
                "### Authoritative sources\n\n- Identity: a; \n"
            ),
            "reviewer-only-content": "## Review checklist\n\n- [ ] item\n",
            "pm-lifecycle-instruction": "Run `cartopian move-task x done`.\n",
            "pm-identifier-present": "Implements FR-001 for TASK-01-001.\n",
            "blanket-governance-read": "Read protocol/CONVENTIONS.md first.\n",
        }
        for expected, body in cases.items():
            with self.subTest(code=expected):
                findings = prompt_composer.validate_prompt(
                    body, contract, structural=False
                )
                self.assertIn(
                    expected, [item["code"] for item in findings]
                )

    def test_missing_required_sections_flagged(self) -> None:
        contract = prompt_composer.load_contract()
        findings = prompt_composer.validate_prompt("# Title\n", contract)
        codes = [item["code"] for item in findings]
        self.assertIn("missing-required-section", codes)


class TestGovernanceReadScoping(unittest.TestCase):
    """blanket-governance-read is semantic: instructions fail, provenance
    identities inside ## Source guidance pass byte-identical."""

    def _build_protocol_source(self, scaffold) -> Path:
        _prepare_work_root(scaffold)
        task_path = scaffold.write(
            "tasks/in-progress/TASK-01-002.md", _PROTOCOL_SOURCE_TASK
        )
        scaffold.write("specs/SPEC-01-002.md", _FULL_SPEC)
        scaffold.write("STANDARDS.md", _AGENTS_ONLY_STANDARDS)
        _capture(scaffold, "REQUEST-001", "task:TASK-01-002", _OPERATOR_REQUEST)
        return task_path

    def test_source_identity_composes_clean_and_byte_identical(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = self._build_protocol_source(scaffold)
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])
            self.assertEqual(record["findings"], [])
            prompt = record["assignee_prompt"]
            # The provenance identity is preserved exactly, not renamed or
            # redacted to satisfy validation.
            self.assertIn(_PROTOCOL_IDENTITY_ROW, prompt)
            # One authoritative Source guidance rendering, no raw JSON.
            unfenced = prompt_composer._outside_fences(prompt)
            self.assertEqual(unfenced.count("### Authoritative sources"), 1)
            self.assertEqual(unfenced.count(_PROTOCOL_IDENTITY_ROW), 1)
            self.assertFalse(prompt_composer._raw_json_present(prompt))
            # The AGENTS.md-only contributor rule is carried, uncensored.
            self.assertIn("Follow the repository's AGENTS.md when", prompt)

    def test_imperative_standards_instruction_fails_composition(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = _build_full(scaffold)
            scaffold.write("STANDARDS.md", _CONTAMINATED_STANDARDS)
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "invalid")
            blanket = [
                item
                for item in record["findings"]
                if item["code"] == "blanket-governance-read"
            ]
            # Reported once, attributed to the originating component, and
            # naming the exact offending statement.
            self.assertEqual(len(blanket), 1, record["findings"])
            self.assertIn("STANDARDS.md", blanket[0]["detail"])
            self.assertIn(
                "Follow the repository's AGENTS.md and "
                "protocol/CONVENTIONS.md",
                blanket[0]["detail"],
            )

    def test_source_identity_grants_no_exemption_elsewhere(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = self._build_protocol_source(scaffold)
            scaffold.write("STANDARDS.md", _CONTAMINATED_STANDARDS)
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "invalid")
            self.assertIn(
                "blanket-governance-read",
                [item["code"] for item in record["findings"]],
            )

    def test_task_goal_instruction_fails_composition(self) -> None:
        contaminated_task = _FULL_TASK.replace(
            "Implement the configuration loader described by the "
            "specification.",
            "Implement the loader; read protocol/CONVENTIONS.md end to end "
            "first.",
        )
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = _build_full(scaffold)
            scaffold.write("tasks/in-progress/TASK-01-002.md", contaminated_task)
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "invalid")
            blanket = [
                item
                for item in record["findings"]
                if item["code"] == "blanket-governance-read"
            ]
            self.assertTrue(blanket)
            self.assertIn("Goal", blanket[0]["detail"])

    def test_validator_scoping_unit_cases(self) -> None:
        contract = prompt_composer.load_contract()
        identity_only = (
            "## Source guidance\n\n### Authoritative sources\n\n"
            f"{_PROTOCOL_IDENTITY_ROW}\n"
        )
        imperative_inside_guidance = (
            identity_only
            + "\nRead protocol/CONVENTIONS.md fully before starting.\n"
        )
        fenced_marker = (
            "## Completion report\n\n```text\nSee protocol/CONVENTIONS.md "
            "for context.\n```\n"
        )
        for name, body, expected in (
            ("identity row passes", identity_only, False),
            ("imperative in guidance fails", imperative_inside_guidance, True),
            ("fenced skeleton content passes", fenced_marker, False),
        ):
            with self.subTest(case=name):
                findings = prompt_composer.validate_prompt(
                    body, contract, structural=False
                )
                codes = [item["code"] for item in findings]
                if expected:
                    self.assertIn("blanket-governance-read", codes)
                else:
                    self.assertNotIn("blanket-governance-read", codes, findings)

    def test_cli_and_mcp_agree_on_source_identity_prompt(self) -> None:
        from mcp_server import server

        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = self._build_protocol_source(scaffold)
            cli_records, _stderr, cli_code = _invoke_cli(str(task_path), "coder")
            self.assertEqual(cli_code, EXIT_OK)
            result = server._invoke_cli(
                "compose-assignment-prompt",
                [str(task_path), "--role", "coder"],
            )
            self.assertEqual(result["exit_code"], EXIT_OK)
            self.assertEqual(len(cli_records), 1)
            self.assertEqual(len(result["records"]), 1)
            self.assertEqual(cli_records[0], result["records"][0])
            self.assertEqual(cli_records[0]["outcome"], "composed")


# Activate grant gating: the coder deliberately lacks read:governance, so
# the composer must embed governance-scoped inputs as typed payloads.
_TOML_CONTAINED_CODER = _TOML_REVIEW_REQUIRED.replace(
    'agent = "cartopian-claude"\n',
    'agent = "cartopian-claude"\ngrants = ["coder-like"]\n',
)

_CONTAMINATED_RESOURCE = (
    "# Loader contract (REQUIREMENTS)\n\n"
    "Derived from TASK-01-002 / SPEC-01-002; see DEC-001.\n"
    "Read protocol/CONVENTIONS.md for the governing lifecycle.\n\n"
    "```json\n"
    '{"functional": ["FR-001", "FR-002"], "review": "## Review checklist"}\n'
    "```\n\n"
    "Trailing-whitespace line:   \n"
)

_DELIVERABLE_LOGICAL = "project:resources/loader-contract.md"

_DELIVERABLE_TASK = _FULL_TASK.replace(
    "Evidence gate: required\n",
    f"Deliverable: {_DELIVERABLE_LOGICAL}\nEvidence gate: required\n",
)


def _build_full_with_deliverable(scaffold) -> Path:
    _prepare_work_root(scaffold)
    task_path = scaffold.write(
        "tasks/in-progress/TASK-01-002.md", _DELIVERABLE_TASK
    )
    scaffold.write("specs/SPEC-01-002.md", _FULL_SPEC)
    scaffold.write("STANDARDS.md", _STANDARDS)
    scaffold.write("resources/loader-contract.md", _CONTAMINATED_RESOURCE)
    _capture(scaffold, "REQUEST-001", "task:TASK-01-002", _OPERATOR_REQUEST)
    return task_path


class TestTypedInputPayloadFlow(unittest.TestCase):
    """Deliverable inputs are an opaque machine channel, not instructions."""

    def test_contaminated_deliverable_composes_and_round_trips(self) -> None:
        from cli import assignment_inputs

        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_full_with_deliverable(scaffold)
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])
            prompt = record["assignee_prompt"]
            self.assertIn("## Existing deliverable input", prompt)
            (entry,) = assignment_inputs.extract_payload_blocks(prompt)
            self.assertTrue(entry["verified"])
            self.assertEqual(entry["channel"], assignment_inputs.CHANNEL_EXISTING)
            self.assertEqual(entry["logical"], _DELIVERABLE_LOGICAL)
            # Byte-exact round trip, including fenced JSON, Cartopian
            # identifiers, trailing whitespace, and the final newline.
            self.assertEqual(entry["content"], _CONTAMINATED_RESOURCE)
            # The machine binding rides in the trace receipt too.
            (manifest_entry,) = record["trace_receipt"]["input_payloads"]
            self.assertEqual(
                manifest_entry["content_sha256"], entry["declared_sha256"]
            )

    def test_same_content_authored_still_fails_validation(self) -> None:
        contract = prompt_composer.load_contract()
        body = (
            "# Prompt\n\n## Your task\n\nUpdate the contract below.\n\n"
            + _CONTAMINATED_RESOURCE
        )
        codes = {
            item["code"]
            for item in prompt_composer.validate_prompt(
                body, contract, structural=False
            )
        }
        self.assertIn("raw-diagnostic-json", codes)
        self.assertIn("pm-identifier-present", codes)
        self.assertIn("blanket-governance-read", codes)

    def test_composed_write_then_handoff_preflight_passes(self) -> None:
        import tempfile

        from cli.commands import handoff_packet

        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_full_with_deliverable(scaffold)
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])
            composed_path = scaffold.root / "composed.json"
            composed_path.write_text(
                json.dumps({"action": "compose-assignment-prompt", **record}),
                encoding="utf-8",
            )
            args = _write_prompt_args(
                scaffold, task_path, composed_file=str(composed_path)
            )
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = write_prompt.handler(args)
            self.assertEqual(code, EXIT_OK, err.getvalue())

            packet_args = argparse.Namespace(
                task_path=str(task_path), role="coder"
            )
            out, err = io.StringIO(), io.StringIO()
            with tempfile.TemporaryDirectory() as home:
                with mock.patch(
                    "cli.commands.handoff_packet.Path.home",
                    return_value=Path(home),
                ):
                    with contextlib.redirect_stdout(out), \
                            contextlib.redirect_stderr(err):
                        rc = handoff_packet.handler(packet_args)
            self.assertEqual(rc, EXIT_OK, err.getvalue())
            packet = json.loads(out.getvalue().splitlines()[0])
            requirement = packet["existing_deliverable_input"]
            self.assertTrue(requirement["required"])
            self.assertIs(requirement["ok"], True)
            self.assertEqual(requirement["prompt_payload"], "bound")
            self.assertTrue(packet["input_payload_audit"]["ok"])

    def test_mutated_resource_after_write_fails_preflight(self) -> None:
        import tempfile

        from cli.commands import handoff_packet

        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_full_with_deliverable(scaffold)
            record = prompt_composer.compose(task_path, "coder")
            composed_path = scaffold.root / "composed.json"
            composed_path.write_text(
                json.dumps({"action": "compose-assignment-prompt", **record}),
                encoding="utf-8",
            )
            args = _write_prompt_args(
                scaffold, task_path, composed_file=str(composed_path)
            )
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(write_prompt.handler(args), EXIT_OK)
            # The resource moves on after the prompt is written: the stale
            # payload must fail preflight, not silently launch.
            scaffold.write(
                "resources/loader-contract.md",
                _CONTAMINATED_RESOURCE + "\nnew requirement\n",
            )
            out, err = io.StringIO(), io.StringIO()
            with tempfile.TemporaryDirectory() as home:
                with mock.patch(
                    "cli.commands.handoff_packet.Path.home",
                    return_value=Path(home),
                ):
                    with contextlib.redirect_stdout(out), \
                            contextlib.redirect_stderr(err):
                        rc = handoff_packet.handler(
                            argparse.Namespace(
                                task_path=str(task_path), role="coder"
                            )
                        )
            self.assertEqual(rc, EXIT_FAIL)
            self.assertIn(
                "existing-deliverable-input-unavailable", err.getvalue()
            )

    def test_authored_write_materializes_payload_sections(self) -> None:
        from cli import assignment_inputs

        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_full_with_deliverable(scaffold)
            args = _write_prompt_args(
                scaffold,
                task_path,
                content=(
                    "# Update the loader contract\n\n"
                    "## Your task\n\nRevise the contract per the goal.\n"
                ),
            )
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = write_prompt.handler(args)
            self.assertEqual(code, EXIT_OK, err.getvalue())
            written = (scaffold.prompts / "PROMPT-01-002.md").read_text(
                encoding="utf-8", newline=""
            )
            self.assertIn("## Existing deliverable input", written)
            (entry,) = assignment_inputs.extract_payload_blocks(written)
            self.assertTrue(entry["verified"])
            self.assertEqual(entry["content"], _CONTAMINATED_RESOURCE)
            details = json.loads(out.getvalue().splitlines()[-1])["details"]
            (manifest_entry,) = details["input_payloads"]
            self.assertEqual(
                manifest_entry["logical"], _DELIVERABLE_LOGICAL
            )

    def test_crlf_resource_composes_writes_and_passes_preflight(self) -> None:
        import tempfile

        from cli import assignment_inputs
        from cli.commands import handoff_packet

        crlf_bytes = (
            b"# Contract\r\n\r\n"
            b"CRLF line one\r\n"
            b"caf\xc3\xa9 \xe2\x80\x94 accented\r\n"
        )
        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_full_with_deliverable(scaffold)
            (scaffold.project_root / "resources" / "loader-contract.md").write_bytes(
                crlf_bytes
            )
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])
            (entry,) = assignment_inputs.extract_payload_blocks(
                record["assignee_prompt"]
            )
            self.assertTrue(entry["verified"])
            self.assertEqual(entry["content"].encode("utf-8"), crlf_bytes)

            composed_path = scaffold.root / "composed.json"
            composed_path.write_text(
                json.dumps({"action": "compose-assignment-prompt", **record}),
                encoding="utf-8",
            )
            args = _write_prompt_args(
                scaffold, task_path, composed_file=str(composed_path)
            )
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                self.assertEqual(
                    write_prompt.handler(args), EXIT_OK, err.getvalue()
                )

            out, err = io.StringIO(), io.StringIO()
            with tempfile.TemporaryDirectory() as home:
                with mock.patch(
                    "cli.commands.handoff_packet.Path.home",
                    return_value=Path(home),
                ):
                    with contextlib.redirect_stdout(out), \
                            contextlib.redirect_stderr(err):
                        rc = handoff_packet.handler(
                            argparse.Namespace(
                                task_path=str(task_path), role="coder"
                            )
                        )
            self.assertEqual(rc, EXIT_OK, err.getvalue())
            packet = json.loads(out.getvalue().splitlines()[0])
            self.assertEqual(
                packet["existing_deliverable_input"]["prompt_payload"], "bound"
            )
            self.assertTrue(packet["input_payload_audit"]["ok"])

    def test_shared_dependency_deliverable_renders_one_payload(self) -> None:
        from cli import assignment_inputs
        from cli.commands import handoff_packet
        from cli.commands.resolve_config import _load_toml

        shared_logical = "project:resources/shared-contract.md"
        dep_task = (
            "# {task_id}: Upstream contract\n\n"
            "Phase: PHASE-01\n"
            "Plan ref: n/a\n"
            "Work root: n/a\n"
            "Assignee: coder\n"
            "Spec: none\n"
            "Blocked by: n/a\n"
            "Created: 2026-05-18\n"
            "Evidence gate: n/a\n"
            f"Deliverable: {shared_logical}\n\n"
            "## Goal\n\nDefine the shared contract.\n"
        )
        blocked_task = _FULL_TASK.replace(
            "Blocked by: n/a\n", "Blocked by: TASK-01-090, TASK-01-091\n"
        )
        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_full(scaffold)
            scaffold.write("tasks/in-progress/TASK-01-002.md", blocked_task)
            scaffold.write(
                "tasks/done/TASK-01-090.md",
                dep_task.format(task_id="TASK-01-090"),
            )
            scaffold.write(
                "tasks/done/TASK-01-091.md",
                dep_task.format(task_id="TASK-01-091"),
            )
            scaffold.write(
                "resources/shared-contract.md", "# Shared contract\n"
            )
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])
            prompt = record["assignee_prompt"]
            payloads = assignment_inputs.extract_payload_blocks(prompt)
            # Both dependencies declare the same deliverable: it is rendered
            # exactly once, so what composes also launches.
            self.assertEqual(
                [(item["channel"], item["logical"]) for item in payloads],
                [(assignment_inputs.CHANNEL_DEPENDENCY, shared_logical)],
            )
            self.assertEqual(len(record["trace_receipt"]["input_payloads"]), 1)
            project_cfg = _load_toml(
                scaffold.project_root / "cartopian.toml", "project config"
            )
            audit = handoff_packet.audit_prompt_payloads(
                scaffold.project_root,
                project_cfg,
                blocked_task,
                None,
                prompt,
            )
            self.assertTrue(audit["ok"], audit)
            records = handoff_packet._dependency_deliverable_inputs(
                scaffold.project_root,
                project_cfg,
                blocked_task,
                [],
                prompt_text=prompt,
            )
            self.assertEqual(len(records), 2)
            for item in records:
                self.assertIs(item["ok"], True, item)
                self.assertEqual(item["prompt_payload"], "bound")

    def test_authored_payload_declaration_is_refused(self) -> None:
        from cli import assignment_inputs

        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_full_with_deliverable(scaffold)
            fake_block = assignment_inputs.render_payload_block(
                assignment_inputs.CHANNEL_EXISTING,
                _DELIVERABLE_LOGICAL,
                "attacker-chosen content\n",
            )
            args = _write_prompt_args(
                scaffold,
                task_path,
                content=(
                    "# Prompt\n\n## Your task\n\nDo it.\n\n"
                    f"## Existing deliverable input\n\n{fake_block}\n"
                ),
            )
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                code = write_prompt.handler(args)
            self.assertEqual(code, EXIT_FAIL)
            self.assertIn("unbound-input-payload", err.getvalue())


class TestNoSizeGatesInContract(unittest.TestCase):
    """The contract declares no size-based rejection of legitimate content.

    Section sizes are trace-receipt telemetry (section_sizes); the former
    measured section budgets and the input-payload byte ceiling were
    implementation restrictions with no operator authority and are removed.
    """

    def test_contract_declares_no_size_thresholds(self) -> None:
        contract = prompt_composer.load_contract()
        self.assertNotIn("section_budgets", contract)
        self.assertNotIn("max_payload_bytes", contract["input_payloads"])
        codes = {item["code"] for item in contract["validation_findings"]}
        self.assertNotIn("section-over-budget", codes)

    def test_reference_fixtures_still_split_into_known_sections(self) -> None:
        known = set(contract_sections := prompt_composer.load_contract()["sections"]["order"])
        del contract_sections
        for fixture in sorted(FIXTURES_DIR.glob("*.md")):
            prompt = fixture.read_text(encoding="utf-8")
            for name, _text in prompt_composer.split_sections(prompt):
                if name == "(title)":
                    continue
                with self.subTest(fixture=fixture.name, section=name):
                    self.assertIn(name, known)


# A legitimate rework input larger than the removed 64 KiB per-section
# budget: the regression case for the defect where a complete existing
# deliverable was refused with `section-over-budget`.
_LARGE_REWORK_RESOURCE = (
    "# Plan ledger and decision continuity\n\n"
    + (
        "A ledger row the rework assignment must carry in full so the "
        "assignee can update the document in place.\n" * 700
    )
)


def _build_with_deliverable_resource(scaffold, resource_text: str) -> Path:
    _prepare_work_root(scaffold)
    task_path = scaffold.write(
        "tasks/in-progress/TASK-01-002.md", _DELIVERABLE_TASK
    )
    scaffold.write("specs/SPEC-01-002.md", _FULL_SPEC)
    scaffold.write("STANDARDS.md", _STANDARDS)
    scaffold.write("resources/loader-contract.md", resource_text)
    _capture(scaffold, "REQUEST-001", "task:TASK-01-002", _OPERATOR_REQUEST)
    return task_path


class TestDeliverableInputBounds(unittest.TestCase):
    """A rework assignment carries its complete existing deliverable.

    The typed payload channel has no byte ceiling of any kind: a governed
    resource is embedded complete and byte-exact whatever its size, and only
    an unreadable or non-UTF-8 resource fails closed.
    """

    def test_rework_deliverable_above_64kib_composes_and_writes(self) -> None:
        from cli import assignment_inputs

        self.assertGreater(
            len(_LARGE_REWORK_RESOURCE.encode("utf-8")), 65536
        )
        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_with_deliverable_resource(
                scaffold, _LARGE_REWORK_RESOURCE
            )
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])
            self.assertEqual(
                [f for f in record["findings"]
                 if f["code"] == "section-over-budget"],
                [],
            )
            (entry,) = assignment_inputs.extract_payload_blocks(
                record["assignee_prompt"]
            )
            self.assertTrue(entry["verified"])
            self.assertEqual(entry["content"], _LARGE_REWORK_RESOURCE)
            # The byte measurement stays visible in the trace receipt.
            sizes = {
                item["section"]: item["bytes"]
                for item in record["section_sizes"]
            }
            self.assertGreater(sizes["Existing deliverable input"], 65536)
            # The composed record writes through the mediated writer.
            composed_path = scaffold.root / "composed.json"
            composed_path.write_text(
                json.dumps({"action": "compose-assignment-prompt", **record}),
                encoding="utf-8",
            )
            args = _write_prompt_args(
                scaffold, task_path, composed_file=str(composed_path)
            )
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = write_prompt.handler(args)
            self.assertEqual(code, EXIT_OK, err.getvalue())

    def test_small_deliverable_composes_clean(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_with_deliverable_resource(
                scaffold, "# Loader contract\n\nOne short section.\n"
            )
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])

    def test_resource_above_former_1mib_ceiling_composes_byte_exact(self) -> None:
        from cli import assignment_inputs

        # One byte past the removed 1 MiB (1048576) ceiling: the payload is
        # embedded complete and byte-exact, proving the bound is gone rather
        # than raised.
        beyond = "# Ledger\n" + "x" * (1048576 + 1 - len("# Ledger\n"))
        self.assertEqual(len(beyond.encode("utf-8")), 1048577)
        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_with_deliverable_resource(scaffold, beyond)
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])
            (entry,) = assignment_inputs.extract_payload_blocks(
                record["assignee_prompt"]
            )
            self.assertTrue(entry["verified"])
            self.assertEqual(entry["content"], beyond)

    def test_materialized_authored_inputs_carry_any_size(self) -> None:
        from cli import assignment_inputs

        beyond = "# Ledger\n" + "x" * (1048576 + 1 - len("# Ledger\n"))
        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_with_deliverable_resource(scaffold, beyond)
            extended_body, _manifest = prompt_composer.materialize_input_sections(
                scaffold.project_root,
                task_path,
                "# Prompt\n\n## Your task\n\nUpdate the contract.\n",
            )
            (entry,) = assignment_inputs.extract_payload_blocks(extended_body)
            self.assertTrue(entry["verified"])
            self.assertEqual(entry["content"], beyond)

    def test_cli_and_mcp_agree_on_large_rework_compose(self) -> None:
        from mcp_server import server

        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            task_path = _build_with_deliverable_resource(
                scaffold, _LARGE_REWORK_RESOURCE
            )
            cli_records, _stderr, cli_code = _invoke_cli(
                str(task_path), "coder"
            )
            self.assertEqual(cli_code, EXIT_OK)
            result = server._invoke_cli(
                "compose-assignment-prompt",
                [str(task_path), "--role", "coder"],
            )
            self.assertEqual(result["exit_code"], EXIT_OK)
            self.assertEqual(cli_records[0], result["records"][0])

    def test_large_prose_section_composes_with_size_telemetry(self) -> None:
        # Legitimate task prose past the removed 16 KiB implementation
        # contract budget composes cleanly; its measured size stays visible
        # in the trace receipt as nonblocking telemetry.
        big_notes = (
            "## Notes\n\n"
            + (
                "Context the composer must carry verbatim into the "
                "implementation contract section.\n" * 260
            )
            + "\n"
        )
        oversized_task = _DELIVERABLE_TASK.replace(
            "## Acceptance", big_notes + "## Acceptance"
        )
        with project_scaffold(cartopian_toml=_TOML_CONTAINED_CODER) as scaffold:
            _prepare_work_root(scaffold)
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-002.md", oversized_task
            )
            scaffold.write("specs/SPEC-01-002.md", _FULL_SPEC)
            scaffold.write("STANDARDS.md", _STANDARDS)
            scaffold.write(
                "resources/loader-contract.md",
                "# Loader contract\n\nOne short section.\n",
            )
            _capture(
                scaffold, "REQUEST-001", "task:TASK-01-002", _OPERATOR_REQUEST
            )
            record = prompt_composer.compose(task_path, "coder")
            self.assertEqual(record["outcome"], "composed", record["findings"])
            sizes = {
                item["section"]: item["bytes"]
                for item in record["section_sizes"]
            }
            self.assertGreater(sizes["Implementation contract"], 16384)


class TestCliMcpEquivalence(unittest.TestCase):
    def test_cli_and_mcp_produce_equivalent_projections(self) -> None:
        from mcp_server import server

        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task_path = _build_full(scaffold)
            cli_records, _stderr, cli_code = _invoke_cli(str(task_path), "coder")
            self.assertEqual(cli_code, EXIT_OK)
            result = server._invoke_cli(
                "compose-assignment-prompt",
                [str(task_path), "--role", "coder"],
            )
            self.assertEqual(result["exit_code"], EXIT_OK)
            self.assertEqual(len(cli_records), 1)
            self.assertEqual(len(result["records"]), 1)
            self.assertEqual(cli_records[0], result["records"][0])


class TestProjectionUnits(unittest.TestCase):
    def test_risk_projection_excludes_review_expectation(self) -> None:
        result = risk_contract.classify_risk(
            [
                {"observation": obs, "state": states[0], "supporting_fact": "fact"}
                for obs, states in risk_contract.observation_choices().items()
            ]
        )
        projection = risk_contract.assignee_projection(result)
        self.assertNotIn("review_expectation", projection)
        self.assertEqual(projection["band"], result["band"])
        self.assertTrue(projection["reasons"])

    def test_judgment_projection_filters_inactive_rows(self) -> None:
        result = judgment_guidance.select_judgment_guidance(
            {
                "lifecycle_boundaries": ["evidence-and-review-gate"],
                "open_failure_conditions": ["evidence-self-certified-or-missing"],
            }
        )
        self.assertEqual(result["outcome"], "active")
        projection = judgment_guidance.assignee_projection(result)
        self.assertIn("evidence-self-certified-or-missing", projection["instructions"])
        self.assertNotIn("inferred-intent-not-confirmed", projection["instructions"])
        self.assertNotIn("---", projection["instructions"].splitlines()[0])

    def test_judgment_projection_none_outcome(self) -> None:
        result = judgment_guidance.select_judgment_guidance(
            {"lifecycle_boundaries": [], "open_failure_conditions": []}
        )
        projection = judgment_guidance.assignee_projection(result)
        self.assertEqual(projection, {"outcome": "none", "holds": [], "instructions": None})

    def test_pack_projection_capsule_and_sources(self) -> None:
        result = practice_packs.select_practice_pack(
            {
                "primary_outcomes": ["software-behavior-change"],
                "artifact_kinds": [],
                "incidental_terms": [],
                "exclusions": [],
                "lifecycle_substrate_activities": [],
                "domain_scopes": [],
                "authorized_profile_hint": None,
            }
        )
        self.assertEqual(result["outcome"], "selected")
        projection = practice_packs.assignee_projection(result)
        self.assertEqual(projection["pack_id"], "software-delivery")
        self.assertIn("Decision Gates", projection["capsule"])
        self.assertNotIn("Principles And Heuristics", projection["capsule"])
        self.assertTrue(projection["applicable_sources"])
        for source in projection["applicable_sources"]:
            self.assertEqual(
                set(source),
                {"title", "context", "governed_scope", "applicability_boundary"},
            )

    def test_spec_assignment_projection_receipt(self) -> None:
        projection, receipt = deidentify.assignment_spec_projection(_FULL_SPEC)
        self.assertNotIn("Status: locked", projection)
        self.assertNotIn("Review checklist", projection)
        self.assertNotIn("Open questions", projection)
        self.assertNotIn("SPEC-01-002", projection)
        self.assertIn("## Interface", projection)
        self.assertIn("Review checklist", receipt["dropped_sections"])
        self.assertEqual(receipt["open_question_lines"], [])

    def test_standards_projection_by_tags(self) -> None:
        projected, receipt = prompt_composer.project_standards(
            _STANDARDS, ["software-behavior-change"]
        )
        self.assertIn("RED/GREEN TDD", projected)
        self.assertIn("Python 3.11+", projected)
        self.assertNotIn("Marketing voice", projected)
        self.assertNotIn("Applies to:", projected)
        self.assertEqual(
            [item["heading"] for item in receipt["excluded_sections"]],
            ["Marketing voice"],
        )


if __name__ == "__main__":
    unittest.main()
