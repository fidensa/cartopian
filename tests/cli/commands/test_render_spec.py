"""Tests for `cartopian render-spec` — deidentified spec rendering for coders."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

_SPEC = (
    "# SPEC-01-002: Widget rendering contract\n"
    "\n"
    "Status: locked\n"
    "Plan refs: BUILD-01-003, BUILD-01-004\n"
    "\n"
    "## Problem\n"
    "\n"
    "The widget must render per FR-003 and the decision in DEC-001.\n"
    "\n"
    "## Interface\n"
    "\n"
    "- FR-003: A valid consumer can call render(x) (see SPEC-01-001).\n"
    "\n"
    "```python\n"
    "def render(x):  # FR-003\n"
    "    return x\n"
    "```\n"
    "\n"
    "## References\n"
    "\n"
    "- Prior spec SPEC-01-001 and decision DEC-001.\n"
    "\n"
    "## Test vectors / acceptance\n"
    "\n"
    "- A valid consumer can render a widget.\n"
)


def _run(*cli_args):
    env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", "")}
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "render-spec", *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


class TestRenderSpec(unittest.TestCase):
    def test_assignment_projection_drops_planning_material(self):
        spec = _SPEC.replace(
            "## References",
            "## Review checklist\n\n- [ ] Scope checked.\n\n## Open questions\n\n## References",
        )
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "SPEC-01-002-widget.md"
            spec_path.write_text(spec, encoding="utf-8")
            proc = _run(str(spec_path), "--projection", "assignment")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertEqual(record["projection"], "assignment")
            assignment = record["assignment_spec"]
            self.assertNotIn("Status: locked", assignment)
            self.assertNotIn("Review checklist", assignment)
            self.assertNotIn("Open questions", assignment)
            self.assertNotIn("SPEC-01-002", assignment)
            self.assertIn("## Interface", assignment)
            receipt = record["assignment_receipt"]
            self.assertIn("Review checklist", receipt["dropped_sections"])
            self.assertEqual(receipt["open_question_lines"], [])
            # The default rendering is still present for existing consumers.
            self.assertIn("deidentified_spec", record)

    def test_default_projection_field_is_deidentified(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "SPEC-01-002-widget.md"
            spec_path.write_text(_SPEC, encoding="utf-8")
            proc = _run(str(spec_path))
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertEqual(record["projection"], "deidentified")
            self.assertNotIn("assignment_spec", record)

    def test_happy_path_strips_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "SPEC-01-002-widget.md"
            spec_path.write_text(_SPEC, encoding="utf-8")
            proc = _run(str(spec_path))
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertEqual(record["action"], "render-spec")
            body = record["deidentified_spec"]
            # No PM identifier survives anywhere — including inside the code fence.
            for token in (
                "SPEC-01-002", "SPEC-01-001", "FR-003", "DEC-001",
                "BUILD-01-003", "BUILD-01-004",
            ):
                self.assertNotIn(token, body, msg=f"{token} leaked into rendering")
            # Work-contract prose is preserved.
            self.assertIn("Widget rendering contract", body)
            self.assertIn("A valid consumer can render a widget", body)
            self.assertIn("def render(x):", body)  # code body kept, comment scrubbed
            # The References section and Plan refs line are gone.
            self.assertNotIn("## References", body)
            self.assertNotIn("Plan refs", body)
            # Redactions report every identifier that was present.
            self.assertEqual(
                set(record["redactions"]),
                {"SPEC-01-002", "SPEC-01-001", "FR-003", "DEC-001",
                 "BUILD-01-003", "BUILD-01-004"},
            )

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run(str(Path(tmp) / "nope.md"))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("[error]", proc.stderr)

    def test_relative_path_rejected(self):
        proc = _run("specs/SPEC-01-001.md")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[usage]", proc.stderr)


if __name__ == "__main__":
    unittest.main()
