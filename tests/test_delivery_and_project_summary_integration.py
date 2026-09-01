"""Integrated proof: the delivery gate and the plain project summary together.

The two halves are proven separately elsewhere — `test_delivery_gate.py` holds
the validator to its semantics, `tests/cli/test_project_summary.py` holds the
summary to its writer and its one reader, and `test_operator_assent_evidence.py`
holds a low-information assent to its antecedent. What is proven *here* is what
only shows up when they run as one system:

* a delivery scenario of each shape — publication, transition, adoption,
  rollback or correction — carries its immediate verification and its follow-up
  through **closeout**, and a complete artifact whose outcome is unverified
  still cannot close;
* the summary is an **artifact, never an outcome**: writing it moves no
  delivery verdict at all, in either direction;
* every automatic lifecycle surface — startup, status, audit, composition,
  assignment, and task execution — is *summary-blind*, proven twice over: the
  emitted record is byte-identical with the summary present and absent, and the
  artifact is never opened at all while they run;
* the explicit retrieval command *does* open it, which is what makes the zero
  above mean something rather than being a watcher that cannot fire;
* CLI and MCP agree on both summary commands, present and absent; and
* nothing in the shipped tree reads the artifact except the two commands that
  are allowed to, so no replacement automatic mechanism slipped in behind the
  removed one.

The absence observations are exact rather than approximate on purpose: a
`assertEqual` on the whole record, and a count of open calls, are the two
observations that can actually fail if continuity context returns.
"""
from __future__ import annotations

import argparse
import builtins
import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.commands import capture_request
from cli.main import SUBCOMMANDS, build_parser
from tests.cli.commands.test_compose_assignment_prompt import (
    _OPERATOR_ANTECEDENT,
    _OPERATOR_ANTECEDENT_SCOPE,
    _OPERATOR_ANTECEDENT_SOURCE,
    _OPERATOR_RESPONSE_SOURCE,
    _TOML_REVIEW_REQUIRED,
    _build_full,
)
from tests.mcp_result import tool_records
from tests.scaffold import project_scaffold

REPO_ROOT = Path(__file__).resolve().parents[1]
DELIVERY_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "delivery"

ARTIFACT = "CONTINUITY.md"

_TOML = (
    "[project]\n"
    'id = "integration-proj"\n'
    'name = "Integration Project"\n'
    'project_schema_version = "v0.12.0"\n'
)

# A technical and a nontechnical closeout summary. Both are plain prose: no
# format header, no schema line, no row grammar, no index — whatever the
# closeout wrote is the artifact.
_TECHNICAL_SUMMARY = (
    "# Project summary\n"
    "\n"
    "The first plan shipped the interval scheduler to the operations tenant\n"
    "and left month-end drift as the open question for the next plan.\n"
)
_NONTECHNICAL_SUMMARY = (
    "# Project summary\n"
    "\n"
    "The first plan carried the retention notice to every office in the\n"
    "district and closed the desk audit that followed it. Custody transition\n"
    "is the opening question of the next plan.\n"
)

# The same neutrality bar the delivery fixtures are held to: a nontechnical
# closeout must be sayable without borrowing software words.
_SOFTWARE_VOCABULARY = re.compile(
    r"\b(deploy|deployment|repository|commit|server|codebase|api)\b"
)

# The four complete delivery fixtures, named by the delivery shape each one is.
DELIVERY_SCENARIOS = {
    "publication": "complete-publication-correction",
    "transition": "complete-transition-alternate",
    "adoption": "complete-adoption-followup-closed",
    "rollback": "complete-technical-rollback",
}


def fixture(name: str) -> str:
    return (DELIVERY_FIXTURES / f"{name}.md").read_text(encoding="utf-8")


def run_cli(*argv):
    """Drive the real CLI parser in-process; return (exit_code, records, stderr)."""
    parser = build_parser()
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
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


def write_summary(project_root, body=_TECHNICAL_SUMMARY):
    code, records, err = run_cli(
        "write-continuity", str(project_root), "--content", body
    )
    assert code == 0, err
    return records


def tree_digest(root: Path) -> dict:
    """Hash every file and mark every directory under ``root``."""
    digest = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            digest[relative] = f"<symlink:{os.readlink(path)}>"
        elif path.is_dir():
            digest[relative + "/"] = "<dir>"
        else:
            digest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


class SummaryOpenWatch:
    """Record every attempt to *open* the summary artifact, by any route.

    ``builtins.open``, ``io.open`` (a separate module attribute bound to the
    same function, and the one ``Path.open`` resolves) and ``os.open`` are the
    three doors to a file's bytes. Anything that reads the summary — directly,
    through ``Path.read_text``, or through a helper — goes through one of them,
    so an empty ``paths`` list is a real observation of "not read", not an
    absence of instrumentation.
    """

    def __init__(self) -> None:
        self.paths: list = []

    def _note(self, target) -> None:
        try:
            candidate = os.fspath(target)
        except TypeError:
            return
        if isinstance(candidate, bytes):
            candidate = candidate.decode("utf-8", "replace")
        if os.path.basename(candidate) == ARTIFACT:
            self.paths.append(candidate)

    def __enter__(self) -> "SummaryOpenWatch":
        self._builtin_open = builtins.open
        self._io_open = io.open
        self._os_open = os.open

        def spy_open(file, *args, **kwargs):
            self._note(file)
            return self._builtin_open(file, *args, **kwargs)

        def spy_os_open(path, *args, **kwargs):
            self._note(path)
            return self._os_open(path, *args, **kwargs)

        builtins.open = spy_open
        io.open = spy_open
        os.open = spy_os_open
        return self

    def __exit__(self, *exc) -> None:
        builtins.open = self._builtin_open
        io.open = self._io_open
        os.open = self._os_open


def capture_assent(
    scaffold,
    *,
    text,
    request_id,
    antecedent=None,
    scope=None,
):
    """Offer one operator message to the real capture gate; return its verdict.

    Built from the same provenance pair the composition fixtures use, so a
    refusal here is about the assent's antecedent and scope and nothing else.
    """
    source = scaffold.root / f"assent-{request_id}.txt"
    source.write_text(text, encoding="utf-8")
    antecedent_path = None
    if antecedent is not None:
        antecedent_path = scaffold.root / f"assent-{request_id}-antecedent.txt"
        antecedent_path.write_text(antecedent, encoding="utf-8")
    host, conversation, message = _OPERATOR_ANTECEDENT_SOURCE
    reply_host, reply_conversation, reply_message = _OPERATOR_RESPONSE_SOURCE
    bound = antecedent_path is not None
    args = argparse.Namespace(
        project_root=str(scaffold.project_root),
        request_id=request_id,
        unit="task:TASK-01-002",
        content_file=str(source),
        antecedent_file=str(antecedent_path) if bound else None,
        antecedent_scope=scope,
        antecedent_host=host if bound else None,
        antecedent_conversation=conversation if bound else None,
        antecedent_message=message if bound else None,
        antecedent_order=11 if bound else None,
        response_host=reply_host if bound else None,
        response_conversation=reply_conversation if bound else None,
        response_message=reply_message if bound else None,
        response_order=12 if bound else None,
        correction_of=None,
        captured_at="2026-07-27T12:00:00Z",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in capture_request.NON_OPERATOR_MARKERS
    }
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch.dict(os.environ, env, clear=True),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        code = capture_request.handler(args)
    return code, err.getvalue()


@contextlib.contextmanager
def isolated_home():
    """Keep the developer's real ``~/.cartopian`` out of role resolution."""
    with tempfile.TemporaryDirectory(prefix="cartopian-home-") as tmp:
        with mock.patch.object(Path, "home", return_value=Path(tmp)):
            yield


# ---------------------------------------------------------------------------
# Delivery scenarios, carried through closeout
# ---------------------------------------------------------------------------


class DeliveryScenariosCloseAcrossDomainsTest(unittest.TestCase):
    """One gate, four delivery shapes, technical and nontechnical alike."""

    def _close_audit(self, plan_fixture: str):
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            path = planned_project(scaffold, plan_fixture)
            code, records, err = run_cli("close-audit", path)
            self.assertEqual(code, 0, err)
            return records[0]

    def test_every_delivery_shape_satisfies_the_gate_at_closeout(self) -> None:
        for shape, name in DELIVERY_SCENARIOS.items():
            with self.subTest(delivery_shape=shape):
                record = self._close_audit(name)
                self.assertTrue(record["closable"], msg=record["blocking_reasons"])
                delivery = record["delivery"]
                self.assertEqual(delivery["gate"], "pass")
                self.assertEqual(delivery["artifact_state"], "complete")
                # The two scenario semantics the gate exists to keep separate
                # from "the artifact is done".
                self.assertEqual(delivery["outcome_state"], "verified")
                self.assertIn(
                    delivery["follow_up_state"],
                    {"scheduled", "satisfied", "not-applicable"},
                )

    def test_immediate_verification_and_follow_up_are_each_load_bearing(self) -> None:
        """Dropping either row takes a scenario from closable to blocked."""
        record = self._close_audit("incomplete-technical")
        self.assertFalse(record["closable"])
        missing = {
            finding["semantic"]
            for finding in record["delivery"]["ordered_findings"]
            if finding["code"] == "delivery-row-missing"
        }
        self.assertEqual(missing, {"immediate-verification", "follow-up"})

    def test_a_complete_artifact_with_an_unverified_outcome_cannot_close(self) -> None:
        """The failure the whole gate exists for, observed at the closeout."""
        record = self._close_audit("artifact-complete-outcome-unverified")
        self.assertEqual(record["delivery"]["artifact_state"], "complete")
        self.assertEqual(record["delivery"]["outcome_state"], "unverified")
        self.assertFalse(record["closable"])
        self.assertIn(
            "delivery-verification-not-run",
            [finding["code"] for finding in record["delivery"]["ordered_findings"]],
        )

    def test_a_nontechnical_scenario_blocks_through_the_same_vocabulary(self) -> None:
        """Domain neutrality where it counts. The two domains do not fail the
        same way — they fail through the same declared registry, and the
        nontechnical closeout says so without borrowing software words."""
        declared = {
            item["code"]
            for item in json.loads(
                (REPO_ROOT / "protocol" / "delivery-contract.json").read_text(
                    encoding="utf-8"
                )
            )["failures"]
        }
        codes = {}
        for domain, name in (
            ("technical", "incomplete-technical"),
            ("nontechnical", "incomplete-nontechnical"),
        ):
            record = self._close_audit(name)
            self.assertFalse(record["closable"])
            emitted = {
                item["code"] for item in record["delivery"]["ordered_findings"]
            }
            self.assertTrue(emitted)
            self.assertEqual(emitted - declared, set())
            codes[domain] = (record, emitted)

        nontechnical, _emitted = codes["nontechnical"]
        for reason in nontechnical["blocking_reasons"]:
            self.assertIsNone(_SOFTWARE_VOCABULARY.search(reason.casefold()), reason)
        self.assertIsNone(
            _SOFTWARE_VOCABULARY.search(fixture("incomplete-nontechnical").casefold())
        )


# ---------------------------------------------------------------------------
# The summary is an artifact, never an outcome
# ---------------------------------------------------------------------------


class SummaryIsNeverAnOutcomeTest(unittest.TestCase):
    """A written summary must not read as delivery having happened."""

    def _audit_pair(self, plan_fixture: str, body=_TECHNICAL_SUMMARY):
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            path = planned_project(scaffold, plan_fixture)
            before = run_cli("close-audit", path)
            write_summary(path, body)
            after = run_cli("close-audit", path)
            return before, after

    def test_writing_the_summary_cannot_unblock_a_delivery(self) -> None:
        before, after = self._audit_pair("artifact-complete-outcome-unverified")
        self.assertFalse(before[1][0]["closable"])
        self.assertEqual(before, after)

    def test_writing_the_summary_cannot_disturb_a_passing_delivery(self) -> None:
        before, after = self._audit_pair("complete-adoption-followup-closed")
        self.assertTrue(before[1][0]["closable"])
        self.assertEqual(before, after)

    def test_the_summary_body_is_not_read_as_delivery_evidence(self) -> None:
        """Even a summary that *claims* the delivery moves nothing: the gate
        reads the plan's declared record, and never the closeout prose."""
        claiming = (
            "# Project summary\n"
            "\n"
            "Delivery is complete and verified; the tenant accepted the "
            "scheduler and every follow-up is closed.\n"
        )
        before, after = self._audit_pair(
            "artifact-complete-outcome-unverified", claiming
        )
        self.assertEqual(before, after)
        self.assertFalse(after[1][0]["closable"])


# ---------------------------------------------------------------------------
# A plain summary, in a technical and a nontechnical project
# ---------------------------------------------------------------------------


class PlainSummaryAcrossDomainsTest(unittest.TestCase):
    def test_both_domains_write_and_read_the_same_plain_artifact(self) -> None:
        for domain, body in (
            ("technical", _TECHNICAL_SUMMARY),
            ("nontechnical", _NONTECHNICAL_SUMMARY),
        ):
            with self.subTest(domain=domain):
                with project_scaffold(cartopian_toml=_TOML) as scaffold:
                    root = str(scaffold.project_root)
                    write_summary(root, body)
                    artifact = scaffold.project_root / ARTIFACT
                    self.assertEqual(artifact.read_bytes(), body.encode("utf-8"))

                    code, records, err = run_cli("read-continuity", root)
                    self.assertEqual(code, 0, err)
                    self.assertTrue(records[0]["present"])
                    self.assertEqual(records[0]["content"], body)

    def test_a_nontechnical_summary_needs_no_software_vocabulary(self) -> None:
        self.assertIsNone(_SOFTWARE_VOCABULARY.search(_NONTECHNICAL_SUMMARY.casefold()))

    def test_the_artifact_carries_no_schema_row_or_index_grammar(self) -> None:
        """"Plain Markdown" is a claim about the bytes on disk, so it is read
        off the bytes: nothing frames the body as a machine record."""
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            write_summary(scaffold.project_root, _NONTECHNICAL_SUMMARY)
            text = (scaffold.project_root / ARTIFACT).read_text(encoding="utf-8")
            for token in (
                "Format:",
                "Schema:",
                "continuity-v1",
                "| ---",
                "## Index",
                "Cold index",
            ):
                self.assertNotIn(token, text)

    def test_ordinary_archives_are_byte_identical_with_and_without_a_summary(
        self,
    ) -> None:
        """Two projects built the same way, one of which also wrote a summary,
        must archive to the same snapshot — and neither snapshot may contain
        the artifact."""
        snapshots = {}
        for with_summary in (False, True):
            with project_scaffold(cartopian_toml=_TOML) as scaffold:
                scaffold.capture_request(
                    request_id="REQUEST-001",
                    unit="project",
                    text="Build the requested project.",
                )
                scaffold.write("REQUIREMENTS.md", "# reqs\n")
                scaffold.write("IMPLEMENTATION_PLAN.md", "# plan\n")
                scaffold.write("STANDARDS.md", "# standards\n")
                scaffold.write("phases/PHASE-01.md", "# PHASE-01\n")
                root = str(scaffold.project_root)
                if with_summary:
                    write_summary(root)

                code, records, err = run_cli(
                    "archive-plan",
                    root,
                    "--closed",
                    "2026-08-31",
                    "--summary",
                    "first plan",
                    "--content",
                    "# CLOSEOUT\n",
                )
                self.assertEqual(code, 0, err)
                archive = Path(records[-1]["details"]["archive_path"])
                self.assertFalse((archive / ARTIFACT).exists())
                snapshots[with_summary] = tree_digest(archive)

        self.assertEqual(snapshots[False], snapshots[True])


# ---------------------------------------------------------------------------
# Every automatic lifecycle surface is summary-blind
# ---------------------------------------------------------------------------


def lifecycle_surfaces(root: str, task: str):
    """The read-only automatic surfaces a session drives without being asked.

    Startup and status, the closeout audit, review and trace context,
    assignment composition and its packet, and the records a task execution is
    handed. None of them is an explicit summary request, so none may read the
    summary.

    ``plan-audit`` is deliberately absent: it reports the mediated-write
    provenance ledger, which *any* mediated write moves, so it is held to a
    narrower claim in its own test rather than to record identity.
    """
    return (
        ("startup:next-action", ("next-action", root)),
        ("status:compose-state", ("compose-state", root)),
        ("status:list-tasks", ("list-tasks", "--project", root)),
        ("audit:close-audit", ("close-audit", root)),
        ("assignment:task-bundle", ("task-bundle", task)),
        ("assignment:validate-task-readiness", ("validate-task-readiness", task)),
        (
            "composition:compose-assignment-prompt",
            ("compose-assignment-prompt", task, "--role", "coder"),
        ),
        ("assignment:handoff-packet", ("handoff-packet", task, "--role", "coder")),
        (
            "review:review-context",
            ("review-context", root, "--review-kind", "task-closure", "--task", task),
        ),
        ("trace:acceptance-trace", ("acceptance-trace", root, "--task", task)),
        ("execution:report-skeleton", ("report-skeleton", task)),
    )


class AutomaticSurfacesAreSummaryBlindTest(unittest.TestCase):
    """The load-bearing exclusion, observed two independent ways."""

    def setUp(self) -> None:
        home = self.enterContext(isolated_home())  # noqa: F841
        self.scaffold = self.enterContext(
            project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED)
        )
        self.task = str(_build_full(self.scaffold))
        self.root = str(self.scaffold.project_root)
        self.scaffold.write("IMPLEMENTATION_PLAN.md", "# plan\n\nBUILD-01-002\n")
        self.scaffold.write("phases/PHASE-01.md", "# PHASE-01: Foundation\n")
        self.scaffold.write("REQUIREMENTS.md", "# reqs\n")
        self.surfaces = lifecycle_surfaces(self.root, self.task)

    def _run_all(self):
        return {label: run_cli(*argv) for label, argv in self.surfaces}

    def test_every_surface_emits_the_same_record_with_and_without_a_summary(
        self,
    ) -> None:
        """Two runs before the summary exists establish that each surface is
        idempotent — without that control, an equal pair afterwards could just
        be a surface that never repeats itself. Then the summary is written and
        the third run must match the second exactly: same exit code, same
        records, same stderr, key for key and in order."""
        first = self._run_all()
        control = self._run_all()
        write_summary(self.root)
        after = self._run_all()

        for label, _argv in self.surfaces:
            with self.subTest(surface=label):
                self.assertEqual(first[label], control[label], "surface is not idempotent")
                self.assertEqual(control[label], after[label])

    def test_no_surface_opens_the_artifact_at_all(self) -> None:
        write_summary(self.root)
        with SummaryOpenWatch() as watch:
            self._run_all()
        self.assertEqual(watch.paths, [])

    def test_no_surface_names_or_quotes_the_summary(self) -> None:
        distinctive = "interval scheduler to the operations tenant"
        write_summary(self.root)
        records = self._run_all()
        for label, _argv in self.surfaces:
            with self.subTest(surface=label):
                blob = json.dumps(records[label][1])
                self.assertNotIn("CONTINUITY", blob)
                self.assertNotIn("continuity", blob)
                self.assertNotIn(distinctive, blob)

    def test_an_absent_summary_adds_no_context_and_fails_nothing(self) -> None:
        """Absence is the ordinary state, not a degraded one: nothing is even
        looked for, and every surface returns the code it returns with one."""
        self.assertFalse((self.scaffold.project_root / ARTIFACT).exists())
        with SummaryOpenWatch() as watch:
            absent = self._run_all()
        self.assertEqual(watch.paths, [])

        write_summary(self.root)
        present = self._run_all()
        for label, _argv in self.surfaces:
            with self.subTest(surface=label):
                self.assertEqual(absent[label][0], present[label][0])

    def test_a_damaged_summary_cannot_reach_an_automatic_surface(self) -> None:
        """The fail-closed refusal belongs to the explicit path alone."""
        clean = self._run_all()
        (self.scaffold.project_root / ARTIFACT).write_bytes(b"\xff\xfe\x00bad")
        self.assertEqual(self._run_all(), clean)

    def test_task_execution_moves_a_task_without_touching_the_summary(self) -> None:
        """``move-task`` mutates, so it is observed by what it opens rather
        than by record identity."""
        write_summary(self.root)
        moving = self.scaffold.write(
            "tasks/open/TASK-01-003.md",
            "# TASK-01-003: Next\n\nPhase: PHASE-01\nAssignee: coder\n",
        )
        with SummaryOpenWatch() as watch:
            code, _records, err = run_cli("move-task", str(moving), "in-progress")
        self.assertEqual(code, 0, err)
        self.assertEqual(watch.paths, [])
        self.assertEqual(
            (self.scaffold.project_root / ARTIFACT).read_text(encoding="utf-8"),
            _TECHNICAL_SUMMARY,
        )

    def test_composed_session_state_never_restates_the_summary(self) -> None:
        """``write-state`` composes ``STATE.md`` itself, so it is the one place
        a summary could be laundered into durable context. The composed bytes
        must be identical with and without it, and the artifact untouched."""
        code, _records, err = run_cli("write-state", self.root)
        self.assertEqual(code, 0, err)
        without = (self.scaffold.project_root / "STATE.md").read_bytes()

        write_summary(self.root)
        with SummaryOpenWatch() as watch:
            code, _records, err = run_cli("write-state", self.root)
        self.assertEqual(code, 0, err)
        with_summary = (self.scaffold.project_root / "STATE.md").read_bytes()

        self.assertEqual(watch.paths, [])
        self.assertEqual(without, with_summary)
        self.assertNotIn(b"CONTINUITY", with_summary)
        self.assertNotIn(b"interval scheduler", with_summary)

    def test_the_plan_audit_delta_is_write_accounting_not_continuity(self) -> None:
        """``plan-audit`` reports the mediated-write provenance ledger, and the
        summary is written through the same mediated writer every root artifact
        uses. So its record does move — but only in the ledger section, and
        never by naming, quoting, or opening the summary. Dropping the
        provenance block makes the two records identical again."""
        before = run_cli("plan-audit", self.root)
        write_summary(self.root)
        with SummaryOpenWatch() as watch:
            after = run_cli("plan-audit", self.root)

        self.assertEqual(watch.paths, [])
        self.assertNotIn("CONTINUITY", json.dumps(after[1]))
        self.assertNotIn("interval scheduler", json.dumps(after[1]))
        self.assertEqual(
            [{k: v for k, v in record.items() if k != "provenance"} for record in before[1]],
            [{k: v for k, v in record.items() if k != "provenance"} for record in after[1]],
        )
        self.assertNotEqual(
            before[1][0]["provenance"]["baseline"],
            after[1][0]["provenance"]["baseline"],
        )
        # The summary is not a governed plan artifact: the audit's inventory
        # of what it governs is unchanged by its existence.
        self.assertEqual(
            before[1][0]["provenance"]["governed_files"],
            after[1][0]["provenance"]["governed_files"],
        )

    def test_state_composition_carries_no_summary_prose_in_any_shape(self) -> None:
        """compose-state renders prose, which is where injected continuity
        would actually be readable. Every shape it has — no plan, live plan,
        and a plan whose delivery gate is blocking — renders the same body
        with and without the summary."""
        shapes = {}
        for label, seed in (
            ("live-plan", None),
            (
                "delivery-blocked",
                lambda: self.scaffold.write(
                    "IMPLEMENTATION_PLAN.md",
                    fixture("artifact-complete-outcome-unverified"),
                ),
            ),
        ):
            if seed is not None:
                seed()
            before = run_cli("compose-state", self.root)
            write_summary(self.root)
            after = run_cli("compose-state", self.root)
            shapes[label] = (before, after)
            (self.scaffold.project_root / ARTIFACT).unlink()

        for label, (before, after) in shapes.items():
            with self.subTest(shape=label):
                self.assertEqual(before, after)
                body = after[1][0]["rendered_body"] or ""
                self.assertNotIn("CONTINUITY", body)
                self.assertNotIn("interval scheduler", body)


# ---------------------------------------------------------------------------
# Explicit retrieval — the one reader, and the control on the watcher
# ---------------------------------------------------------------------------


class ExplicitRetrievalIsTheOnlyReaderTest(unittest.TestCase):
    def test_the_explicit_request_does_open_the_artifact(self) -> None:
        """The control for every zero above. If the watcher could not observe
        a read, its silence on the automatic surfaces would prove nothing."""
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            root = str(scaffold.project_root)
            write_summary(root)
            with SummaryOpenWatch() as watch:
                code, records, err = run_cli("read-continuity", root)
            self.assertEqual(code, 0, err)
            self.assertEqual(records[0]["content"], _TECHNICAL_SUMMARY)
            self.assertEqual(len(watch.paths), 1)

    def test_an_absent_summary_is_reported_without_a_body_and_without_failing(
        self,
    ) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            code, records, err = run_cli(
                "read-continuity", str(scaffold.project_root)
            )
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            self.assertEqual(records[0], {"path": ARTIFACT, "present": False})


# ---------------------------------------------------------------------------
# CLI and MCP say the same thing
# ---------------------------------------------------------------------------


class SummaryCliMcpParityTest(unittest.TestCase):
    def setUp(self) -> None:
        from mcp_server import server

        self.server = server
        self.tools = {tool["name"] for tool in server.list_tools()}

    def test_both_summary_commands_are_registered_on_both_surfaces(self) -> None:
        for cli_name, tool_name in (
            ("write-continuity", "write_continuity"),
            ("read-continuity", "read_continuity"),
        ):
            with self.subTest(command=cli_name):
                self.assertIn(cli_name, SUBCOMMANDS)
                self.assertIn(tool_name, self.tools)

    def test_retrieval_records_match_present_and_absent(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            root = str(scaffold.project_root)

            cli_absent = run_cli("read-continuity", root)
            mcp_absent = self.server.call_tool("read_continuity", {"project_root": root})
            self.assertEqual(tool_records(mcp_absent), cli_absent[1])
            self.assertEqual(
                mcp_absent["structuredContent"]["exit_code"], cli_absent[0]
            )
            self.assertFalse(mcp_absent["isError"])

            write_summary(root)
            cli_present = run_cli("read-continuity", root)
            mcp_present = self.server.call_tool(
                "read_continuity", {"project_root": root}
            )
            self.assertEqual(tool_records(mcp_present), cli_present[1])
            self.assertEqual(
                tool_records(mcp_present)[0]["content"], _TECHNICAL_SUMMARY
            )

    def test_the_mcp_writer_produces_the_same_artifact_as_the_cli(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            root = str(scaffold.project_root)
            result = self.server.call_tool(
                "write_continuity",
                {"project_root": root, "content": _NONTECHNICAL_SUMMARY},
            )
            self.assertEqual(result["structuredContent"]["exit_code"], 0)
            record = tool_records(result)[0]
            self.assertEqual(record["action"], "write-continuity")
            self.assertFalse(record["details"]["replaced"])
            self.assertEqual(
                (scaffold.project_root / ARTIFACT).read_bytes(),
                _NONTECHNICAL_SUMMARY.encode("utf-8"),
            )

    def test_the_mcp_automatic_tools_are_summary_blind_too(self) -> None:
        """Parity is not only for the two summary commands: the tools a host
        calls on its own must carry no continuity content either."""
        with isolated_home():
            with project_scaffold(cartopian_toml=_TOML) as scaffold:
                root = str(scaffold.project_root)
                scaffold.write("phases/PHASE-01.md", "# PHASE-01: Foundation\n")
                scaffold.write(
                    "tasks/open/TASK-01-001.md",
                    "# TASK-01-001: First\n\nPhase: PHASE-01\n",
                )
                for tool, payload in (
                    ("next_action", {"project_path": root}),
                    ("compose_state", {"project_path": root}),
                ):
                    with self.subTest(tool=tool):
                        before = tool_records(self.server.call_tool(tool, payload))
                        write_summary(root)
                        with SummaryOpenWatch() as watch:
                            after = tool_records(self.server.call_tool(tool, payload))
                        self.assertEqual(before, after)
                        self.assertEqual(watch.paths, [])
                        (scaffold.project_root / ARTIFACT).unlink()


# ---------------------------------------------------------------------------
# The removed machinery has no surviving consumer anywhere in the tree
# ---------------------------------------------------------------------------


SHIPPED_TREES = ("cli", "mcp_server", "protocol", "skills", "templates", "wrappers", "bin")

# Tokens that only ever existed to support automatic continuity injection.
PROJECTION_TOKENS = (
    "continuity-v1",
    "continuity_projection",
    "CONTINUITY_PROJECTION",
    "PROJECTION_KEY",
    "read_projection",
    "PROJECTION_LIVE_ROW_CAP",
    "compact_row",
    "compact-row",
    "Cold index",
    "cold_index",
    "prune-continuity",
    "prune_continuity",
    "continuity_mode",
    "continuity mode",
)

# The three modules that are allowed to name the artifact at all, plus the
# mediated-write allowlist, which names the destination and reads nothing.
SUMMARY_MODULES = {
    "cli/continuity.py",
    "cli/commands/write_continuity.py",
    "cli/commands/read_continuity.py",
    "cli/mediated_write.py",
    "cli/main.py",
}


# Every way a module can reach the summary: the shared module under either
# import form, an attribute path into it, or the filename itself.
_REACHES_THE_ARTIFACT = re.compile(
    r"cli\.continuity"
    r"|from\s+cli\s+import\s+[^\n]*\bcontinuity\b"
    r"|import\s+cli\.continuity"
    r"|CONTINUITY\.md"
)


def shipped_files():
    for tree in SHIPPED_TREES:
        base = REPO_ROOT / tree
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                yield path.relative_to(REPO_ROOT).as_posix(), path.read_text(
                    encoding="utf-8"
                )
            except (UnicodeDecodeError, OSError):
                continue


class LegacyProjectionMachineryIsGoneTest(unittest.TestCase):
    def test_no_projection_or_index_token_survives_in_the_shipped_tree(self) -> None:
        offenders = [
            f"{relative}: {token}"
            for relative, text in shipped_files()
            for token in PROJECTION_TOKENS
            if token in text
        ]
        self.assertEqual(offenders, [])

    def test_no_projection_only_limit_survives(self) -> None:
        source = (REPO_ROOT / "cli" / "continuity.py").read_text(encoding="utf-8")
        for token in ("MAX_BYTES", "ROW_CAP", "CHAR_MAX", "BYTE_MAX", "LIST_MAX", "LIMIT"):
            self.assertNotIn(token, source)

    def test_no_replacement_automatic_reader_exists(self) -> None:
        """Removal without replacement: outside the two summary commands and
        the module they share, nothing in the shipped Python resolves, opens,
        or imports the artifact — under any import spelling."""
        offenders = [
            relative
            for relative, text in shipped_files()
            if relative.endswith(".py")
            and relative not in SUMMARY_MODULES
            and _REACHES_THE_ARTIFACT.search(text)
        ]
        self.assertEqual(offenders, [])

    def test_the_replacement_reader_check_can_actually_fail(self) -> None:
        """A guard nobody has seen fail is a guard nobody has tested. Each
        import spelling a replacement mechanism could use is checked against
        the same pattern the tree is scanned with."""
        for spelling in (
            "from cli import continuity",
            "from cli import continuity as c",
            "from cli import emit, continuity",
            "import cli.continuity",
            "cli.continuity.read_summary(root)",
            'path = root / "CONTINUITY.md"',
        ):
            with self.subTest(spelling=spelling):
                self.assertIsNotNone(_REACHES_THE_ARTIFACT.search(spelling))

    def test_no_configuration_surface_carries_a_continuity_setting(self) -> None:
        """The removed machinery was mode-configurable. Nothing turns an
        automatic summary load back on, because no surface declares a key that
        could: not the config registry, not its mapping, not the capability
        projection."""
        for name in ("config-surfaces.json", "CONFIG-MAPPING.md", "CAPABILITIES.md"):
            with self.subTest(surface=name):
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
                self.assertNotIn("continuity", text.casefold())

    def test_the_startup_runbook_neither_reads_nor_mentions_the_summary(self) -> None:
        startup = (REPO_ROOT / "skills" / "start-session.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("CONTINUITY", startup)
        self.assertNotIn("continuity", startup)

    def test_the_closeout_skill_is_the_only_skill_that_names_it(self) -> None:
        naming = sorted(
            path.name
            for path in (REPO_ROOT / "skills").glob("*.md")
            if "CONTINUITY.md" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(naming, ["close-plan.md"])


# ---------------------------------------------------------------------------
# A low-information assent, end to end
# ---------------------------------------------------------------------------


class AntecedentBoundAssentTest(unittest.TestCase):
    """Proven where the coder would actually receive it: the assignment."""

    def setUp(self) -> None:
        self.enterContext(isolated_home())
        self.scaffold = self.enterContext(
            project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED)
        )
        self.task = str(_build_full(self.scaffold))
        self.correction = (
            self.scaffold.project_root
            / "requests"
            / "REQUEST-001-CORRECTION-001.json"
        )

    def _compose(self):
        return run_cli("compose-assignment-prompt", self.task, "--role", "coder")

    def _detach(self) -> None:
        record = json.loads(self.correction.read_text(encoding="utf-8"))
        record.pop("antecedent")
        self.correction.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def test_a_bound_assent_reaches_the_coder_with_its_proposal_and_scope(self) -> None:
        """The assignment is composed, then written; the operator-request
        sections are appended at the write. The bare "continue" must arrive in
        the delivered prompt file inseparable from the proposal it answers and
        that proposal's exact quoted scope."""
        code, records, err = self._compose()
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["request_sections_appended_by"], "write-prompt")

        composed = self.scaffold.root / "composed.json"
        composed.write_text(json.dumps(records[0]), encoding="utf-8")
        code, written, err = run_cli(
            "write-prompt",
            str(self.scaffold.project_root),
            "--composed-file",
            str(composed),
            "--prompt-id",
            "PROMPT-01-002",
            "--task",
            self.task,
        )
        self.assertEqual(code, 0, err)
        prompt = Path(written[-1]["details"]["path"]).read_text(encoding="utf-8")
        self.assertIn("continue", prompt)
        self.assertIn(_OPERATOR_ANTECEDENT, prompt)
        self.assertIn(_OPERATOR_ANTECEDENT_SCOPE, prompt)

    def test_a_detached_assent_stops_the_assignment_instead_of_authorizing_it(
        self,
    ) -> None:
        self._detach()
        code, _records, err = self._compose()
        self.assertEqual(code, 1)
        self.assertIn("detached-assent", err)
        self.assertIn("not standalone evidence of intent", err)

    def test_the_capture_gate_refuses_a_scope_the_proposal_never_stated(self) -> None:
        """The other end of the same contract. The assignment surfaces refuse a
        *stored* overreach; the capture command refuses to store one, so an
        assent can never come to authorize a detail absent from the proposal it
        answered."""
        code, err = capture_assent(
            self.scaffold,
            text="yes",
            request_id="REQUEST-002",
            antecedent=_OPERATOR_ANTECEDENT,
            scope="also migrate the existing configuration files to the new schema",
        )
        self.assertEqual(code, 1)
        self.assertIn("scope-exceeds-antecedent", err)
        self.assertIn("absent from what the operator was asked", err)

    def test_the_capture_gate_refuses_a_detached_assent_outright(self) -> None:
        code, err = capture_assent(self.scaffold, text="yes", request_id="REQUEST-003")
        self.assertEqual(code, 1)
        self.assertIn("detached-assent", err)
        self.assertIn("self-contained operator instruction", err)

    def test_a_detached_assent_stops_the_handoff_packet_too(self) -> None:
        """Both dispatch-side surfaces fail closed, so a detached assent cannot
        reach an agent through the packet after the prompt refused it."""
        self._detach()
        code, _records, err = run_cli(
            "handoff-packet", self.task, "--role", "coder"
        )
        self.assertEqual(code, 1)
        self.assertIn("detached-assent", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
