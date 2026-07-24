"""Tests for `cartopian validate-task-readiness`."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"


def _run(*cli_args, home=None, cwd=None):
    env = {
        "HOME": str(home) if home is not None else "/tmp",
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "validate-task-readiness", *cli_args],
        cwd=str(cwd) if cwd is not None else str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


BARE_PROJECT_TOML = (
    '[project]\n'
    'id = "demo"\n'
    'name = "Demo"\n'
    'protocol_version = "v0.2.0"\n'
)


def _make_project(root: Path, *, work_roots=None, plan_refs=("P01-BUILD-007",)):
    body = BARE_PROJECT_TOML
    if work_roots is not None:
        body += "work_roots = [" + ", ".join(f'"{n}"' for n in work_roots) + "]\n"
    _write(root / "cartopian.toml", body)
    if work_roots:
        mapping = "\n".join(f'{n} = "/tmp/{n}-dir"' for n in work_roots)
        _write(root / "cartopian.local.toml", f"[work_roots]\n{mapping}\n")
    _write(
        root / "phases" / "PHASE-01-substrate-build.md",
        "# PHASE-01-substrate-build\n",
    )
    _write(
        root / "IMPLEMENTATION_PLAN.md",
        "\n".join(plan_refs) + "\n",
    )
    for sub in ("done", "open", "in-progress", "in-review"):
        (root / "tasks" / sub).mkdir(parents=True, exist_ok=True)


def _task_body(
    *,
    phase="PHASE-01-substrate-build",
    plan_ref="P01-BUILD-007",
    work_root=None,
    blocked_by=None,
    evidence_gate="required",
    use_legacy_test_gate=False,
    include_acceptance_header=True,
    acceptance_items=("- [ ] something to do",),
    omit_work_root_line=False,
):
    lines = ["# TASK-XX-XXX: example", "", f"Phase: {phase}", f"Plan ref: {plan_ref}"]
    if not omit_work_root_line:
        if work_root is not None:
            lines.append(f"Work root: {work_root}")
        else:
            lines.append("Work root: n/a")
    if blocked_by is not None:
        lines.append(f"Blocked by: {blocked_by}")
    if use_legacy_test_gate:
        lines.append(f"Test gate: {evidence_gate}")
    else:
        lines.append(f"Evidence gate: {evidence_gate}")
    lines.append("")
    if include_acceptance_header:
        lines.append("## Acceptance")
        lines.append("")
        for item in acceptance_items:
            lines.append(item)
        lines.append("")
    lines.append("## Goal")
    lines.append("")
    lines.append("Body.")
    lines.append("")
    return "\n".join(lines)


class _Sandbox:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "proj"
        self.home.mkdir()

    def make(self, **kw):
        _make_project(self.project, **kw)

    def write_task(self, filename: str, body: str, status="open") -> Path:
        path = self.project / "tasks" / status / filename
        _write(path, body)
        return path

    def cleanup(self):
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.cleanup()


CHECK_NAMES_IN_ORDER = [
    "phase-exists",
    "plan-ref-exists",
    "blocked-by-complete",
    "evidence-gate-valid",
    "acceptance-present",
    "work-root-names-valid",
    "deliverable-valid",
]


def _parse_single_record(result):
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise AssertionError(
            f"expected 1 stdout line, got {len(lines)}: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return json.loads(lines[0])


class TestHappyPath(unittest.TestCase):
    def test_well_formed_task_validates_ready_true(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task("TASK-01-007-x.md", _task_body())
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        record = _parse_single_record(result)
        self.assertEqual(list(record.keys()), ["task_path", "ready", "checks"])
        self.assertEqual(record["task_path"], str(task.resolve()))
        self.assertTrue(record["ready"])
        self.assertEqual(
            [c["name"] for c in record["checks"]],
            CHECK_NAMES_IN_ORDER,
        )
        for check in record["checks"]:
            self.assertTrue(check["pass"], msg=check)
            self.assertIsNone(check["reason"])


class TestPhaseExistsFails(unittest.TestCase):
    def test_unknown_phase_blocks(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(phase="PHASE-99-missing"),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 1)
        record = _parse_single_record(result)
        self.assertFalse(record["ready"])
        check = next(c for c in record["checks"] if c["name"] == "phase-exists")
        self.assertFalse(check["pass"])
        self.assertIsNotNone(check["reason"])
        self.assertIn("[validation]", result.stderr)


class TestPlanRefExistsFails(unittest.TestCase):
    def test_unknown_plan_ref_blocks(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(plan_ref="P99-MISSING-000"),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 1)
        record = _parse_single_record(result)
        self.assertFalse(record["ready"])
        check = next(c for c in record["checks"] if c["name"] == "plan-ref-exists")
        self.assertFalse(check["pass"])
        self.assertIsNotNone(check["reason"])
        self.assertIn("[validation]", result.stderr)


class TestBlockedByComplete(unittest.TestCase):
    def test_blocked_by_in_open_fails(self):
        with _Sandbox() as sb:
            sb.make()
            sb.write_task(
                "TASK-01-099-other.md",
                _task_body(),
                status="open",
            )
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(blocked_by="TASK-01-099"),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 1)
        record = _parse_single_record(result)
        self.assertFalse(record["ready"])
        check = next(c for c in record["checks"] if c["name"] == "blocked-by-complete")
        self.assertFalse(check["pass"])
        self.assertIn("TASK-01-099", check["reason"])
        self.assertIn("[validation]", result.stderr)

    def test_blocked_by_in_done_passes(self):
        with _Sandbox() as sb:
            sb.make()
            sb.write_task(
                "TASK-01-099-other.md",
                _task_body(),
                status="done",
            )
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(blocked_by="TASK-01-099"),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        check = next(c for c in record["checks"] if c["name"] == "blocked-by-complete")
        self.assertTrue(check["pass"])

    def test_blocked_by_none_sentinel_passes(self):
        # The TASK template documents `Blocked by: <... | none>`, so `none`
        # must be treated as the no-blockers sentinel — not a literal task id.
        for sentinel in ("none", "None", "NONE", "n/a"):
            with self.subTest(sentinel=sentinel):
                with _Sandbox() as sb:
                    sb.make()
                    task = sb.write_task(
                        "TASK-01-007-x.md",
                        _task_body(blocked_by=sentinel),
                    )
                    result = _run(str(task), home=sb.home)
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                record = _parse_single_record(result)
                check = next(
                    c for c in record["checks"] if c["name"] == "blocked-by-complete"
                )
                self.assertTrue(check["pass"], msg=check)
                self.assertIsNone(check["reason"])


class TestEvidenceGateValid(unittest.TestCase):
    def test_post_dec002_required_passes(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(evidence_gate="required"),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        check = next(c for c in record["checks"] if c["name"] == "evidence-gate-valid")
        self.assertTrue(check["pass"])

    def test_legacy_test_gate_required_passes(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(use_legacy_test_gate=True, evidence_gate="required"),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        check = next(c for c in record["checks"] if c["name"] == "evidence-gate-valid")
        self.assertTrue(check["pass"])

    def test_evidence_gate_maybe_fails(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(evidence_gate="maybe"),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 1)
        record = _parse_single_record(result)
        check = next(c for c in record["checks"] if c["name"] == "evidence-gate-valid")
        self.assertFalse(check["pass"])
        self.assertIsNotNone(check["reason"])
        self.assertIn("[validation]", result.stderr)


class TestAcceptancePresent(unittest.TestCase):
    def test_missing_acceptance_section_fails(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(include_acceptance_header=False),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 1)
        record = _parse_single_record(result)
        check = next(c for c in record["checks"] if c["name"] == "acceptance-present")
        self.assertFalse(check["pass"])
        self.assertIsNotNone(check["reason"])
        self.assertIn("[validation]", result.stderr)

    def test_acceptance_section_without_checkboxes_fails(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(
                    acceptance_items=("Just prose, no checkbox.",),
                ),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 1)
        record = _parse_single_record(result)
        check = next(c for c in record["checks"] if c["name"] == "acceptance-present")
        self.assertFalse(check["pass"])


class TestWorkRootNamesValidUnknown(unittest.TestCase):
    def test_unknown_work_root_blocks_with_work_root_prefix(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(work_root="foo"),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 1)
        record = _parse_single_record(result)
        self.assertFalse(record["ready"])
        check = next(c for c in record["checks"] if c["name"] == "work-root-names-valid")
        self.assertFalse(check["pass"])
        self.assertEqual(check["reason"], "unknown work-root name: foo")
        self.assertIn("[work-root]", result.stderr)
        self.assertNotIn("[validation] work-root-names-valid", result.stderr)


class TestWorkRootNamesValidMultiValued(unittest.TestCase):
    def test_multi_valued_passes_with_warning(self):
        with _Sandbox() as sb:
            sb.make(work_roots=["alpha", "beta"])
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(work_root="alpha, beta"),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertTrue(record["ready"])
        check = next(c for c in record["checks"] if c["name"] == "work-root-names-valid")
        self.assertTrue(check["pass"])
        self.assertIn("[work-root] warning:", result.stderr)


class TestWorkRootSkipCases(unittest.TestCase):
    def test_work_root_na_passes(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(work_root="n/a"),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        check = next(c for c in record["checks"] if c["name"] == "work-root-names-valid")
        self.assertTrue(check["pass"])

    def test_work_root_empty_passes(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(work_root=""),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        check = next(c for c in record["checks"] if c["name"] == "work-root-names-valid")
        self.assertTrue(check["pass"])

    def test_no_work_root_header_passes(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task(
                "TASK-01-007-x.md",
                _task_body(omit_work_root_line=True),
            )
            result = _run(str(task), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        check = next(c for c in record["checks"] if c["name"] == "work-root-names-valid")
        self.assertTrue(check["pass"])


class TestMissingTaskFile(unittest.TestCase):
    def test_missing_file_exits_fail_with_no_record(self):
        with _Sandbox() as sb:
            sb.make()
            missing = sb.project / "tasks" / "open" / "TASK-01-007-x.md"
            result = _run(str(missing), home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("[error] task file not found:", result.stderr)


class TestRelativePathRejected(unittest.TestCase):
    def test_relative_task_path_exits_usage(self):
        with _Sandbox() as sb:
            sb.make()
            sb.write_task("TASK-01-007-x.md", _task_body())
            result = _run(
                "TASK-01-007-x.md",
                home=sb.home,
                cwd=sb.project / "tasks" / "open",
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(
            "[usage] task_path must be an absolute path; got: TASK-01-007-x.md",
            result.stderr,
        )


class TestDeterminism(unittest.TestCase):
    def test_same_fixture_byte_equal_stdout(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task("TASK-01-007-x.md", _task_body())
            first = _run(str(task), home=sb.home)
            second = _run(str(task), home=sb.home)
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        self.assertEqual(first.stdout, second.stdout)


class TestCheckOrdering(unittest.TestCase):
    def test_checks_in_spec_locked_order(self):
        with _Sandbox() as sb:
            sb.make()
            task = sb.write_task("TASK-01-007-x.md", _task_body())
            result = _run(str(task), home=sb.home)
        record = _parse_single_record(result)
        self.assertEqual(
            [c["name"] for c in record["checks"]],
            CHECK_NAMES_IN_ORDER,
        )


class TestHelpListsCommand(unittest.TestCase):
    def test_help_lists_validate_task_readiness_without_placeholder(self):
        env = {
            "HOME": "/tmp",
            "PATH": os.environ.get("PATH", ""),
        }
        result = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "--help"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("validate-task-readiness", result.stdout)
        # The placeholder marker no longer applies to this command.
        lines = [
            ln for ln in result.stdout.splitlines()
            if "validate-task-readiness" in ln
        ]
        for ln in lines:
            self.assertNotIn("not yet implemented", ln, msg=ln)


if __name__ == "__main__":
    unittest.main()
