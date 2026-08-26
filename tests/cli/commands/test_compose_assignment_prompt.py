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


class TestBudgetsAnchoredToFixtures(unittest.TestCase):
    """The contract's budgets stay anchored to the approved fixtures."""

    def test_reference_fixtures_fit_budgets(self) -> None:
        contract = prompt_composer.load_contract()
        budgets = contract["section_budgets"]["budgets"]
        known = set(contract["sections"]["order"])
        self.assertEqual(set(budgets), known)
        for fixture in sorted(FIXTURES_DIR.glob("*.md")):
            prompt = fixture.read_text(encoding="utf-8")
            for name, text in prompt_composer.split_sections(prompt):
                if name == "(title)":
                    continue
                with self.subTest(fixture=fixture.name, section=name):
                    self.assertIn(name, budgets)
                    self.assertLessEqual(
                        len(text.encode("utf-8")), budgets[name]
                    )


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
