"""Tests for the universal raw-edit detection floor.

The floor must detect any change to a governed artifact that did not pass
through a mediated writer, on a harness with **no native interception** — here
modeled by calling the ordinary ``mediated_write`` primitive and the
``cartopian plan-audit`` CLI directly, with no PreToolUse hook in the loop.

Coverage:

- a mediated write records provenance and audits clean (no guard, no advisory);
- a **raw edit** to a previously-mediated governed artifact fires a ``guard``;
- a **raw revert** to a superseded mediated version fires a ``guard`` (only the
  latest mediated write is the artifact's authorized state), while reverting
  *through* a writer audits clean;
- a governed artifact created out of band (never mediated) fires an
  ``advisory``;
- a ``move-task`` relocation carries provenance, so the moved file stays clean
  and a raw edit *after* the move still fires a ``guard``;
- with no write log at all the floor emits a single ``no-provenance-baseline``
  advisory and no guard (NF-004: pre-adoption projects are not flagged);
- end-to-end through ``cartopian plan-audit``: a seeded raw edit yields exit 1,
  a ``[guard]`` stderr line, and a structured NDJSON record with a non-empty
  ``provenance.guard``; a mediated change passes silently (exit 0, clean).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from cli.mediated_write import mediated_write  # noqa: E402
from cli.provenance import (  # noqa: E402
    LOG_RELPATH,
    audit_provenance,
    governed_files,
    hash_bytes,
)

_PROJECT_TOML = (
    "[project]\n"
    'id = "floor-fixture"\n'
    'name = "Floor Fixture"\n'
    'protocol_version = "v0.4.0"\n'
    "\n[defaults]\ngit_versioning = false\n"
)


class _ProjectFixture(unittest.TestCase):
    """A throwaway Cartopian project root with the lifecycle dirs created."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._tmp.name)
        (Path(self.root) / "cartopian.toml").write_text(_PROJECT_TOML, encoding="utf-8")
        for sub in ("tasks/open", "tasks/in-progress", "specs", "phases",
                    "prompts", "reports", "reviews", "decisions"):
            (Path(self.root) / sub).mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._tmp.cleanup)


class TestGovernedSet(_ProjectFixture):
    def test_governed_files_are_enumerated_root_and_dirs(self):
        (Path(self.root) / "STATE.md").write_text("s\n", encoding="utf-8")
        (Path(self.root) / "IMPLEMENTATION_PLAN.md").write_text("p\n", encoding="utf-8")
        (Path(self.root) / "tasks/open/sample-a.md").write_text("t\n", encoding="utf-8")
        (Path(self.root) / "decisions/sample-b.md").write_text("d\n", encoding="utf-8")
        # A non-governed file must not be enumerated.
        (Path(self.root) / "README.md").write_text("r\n", encoding="utf-8")
        rels = {os.path.relpath(p, self.root).replace(os.sep, "/") for p in governed_files(self.root)}
        self.assertIn("STATE.md", rels)
        self.assertIn("IMPLEMENTATION_PLAN.md", rels)
        self.assertIn("tasks/open/sample-a.md", rels)
        self.assertIn("decisions/sample-b.md", rels)
        self.assertNotIn("README.md", rels)


class TestAuditLogic(_ProjectFixture):
    def test_mediated_write_audits_clean(self):
        mediated_write(self.root, "task", "open/sample-a.md", "body\n")
        result = audit_provenance(self.root)
        self.assertEqual(result["baseline"], "established")
        self.assertEqual(result["guard"], [])
        self.assertEqual(result["advisory"], [])

    def test_raw_edit_fires_guard(self):
        mediated_write(self.root, "task", "open/sample-a.md", "body\n")
        # Seed an out-of-band raw edit: a plain write that bypassed the writer.
        target = Path(self.root) / "tasks/open/sample-a.md"
        target.write_text("tampered\n", encoding="utf-8")
        result = audit_provenance(self.root)
        self.assertEqual(len(result["guard"]), 1, result)
        g = result["guard"][0]
        self.assertEqual(g["kind"], "raw-edit")
        self.assertEqual(g["relpath"], "tasks/open/sample-a.md")
        self.assertEqual(g["current_hash"], hash_bytes(b"tampered\n"))
        self.assertEqual(result["advisory"], [])

    def test_raw_revert_to_a_superseded_mediated_version_fires_guard(self):
        mediated_write(self.root, "state", "STATE.md", "v1\n")
        mediated_write(self.root, "state", "STATE.md", "v2\n")
        # Raw-edit back to a *superseded* mediated content (v1). The artifact's
        # current authorized state is the latest mediated write (v2); a raw
        # revert is an out-of-band change that never passed through a writer, so
        # the floor must detect it. A legitimate revert would append a fresh
        # mediated entry instead.
        (Path(self.root) / "STATE.md").write_text("v1\n", encoding="utf-8")
        result = audit_provenance(self.root)
        self.assertEqual(len(result["guard"]), 1, result)
        g = result["guard"][0]
        self.assertEqual(g["kind"], "raw-edit")
        self.assertEqual(g["relpath"], "STATE.md")
        self.assertEqual(g["current_hash"], hash_bytes(b"v1\n"))
        self.assertEqual(result["advisory"], [])

    def test_mediated_revert_through_a_writer_audits_clean(self):
        # The companion to the raw-revert guard: reverting *through* a mediated
        # writer appends a new latest entry, so v1-again is the current
        # authorized state and audits clean.
        mediated_write(self.root, "state", "STATE.md", "v1\n")
        mediated_write(self.root, "state", "STATE.md", "v2\n")
        mediated_write(self.root, "state", "STATE.md", "v1\n")
        result = audit_provenance(self.root)
        self.assertEqual(result["guard"], [])
        self.assertEqual(result["advisory"], [])

    def test_untracked_artifact_fires_advisory_not_guard(self):
        # Establish a baseline with one mediated write, then create a second
        # governed artifact entirely out of band.
        mediated_write(self.root, "state", "STATE.md", "s\n")
        (Path(self.root) / "tasks/open/rogue-note.md").write_text(
            "rogue\n", encoding="utf-8"
        )
        result = audit_provenance(self.root)
        self.assertEqual(result["guard"], [])
        self.assertEqual(len(result["advisory"]), 1, result)
        adv = result["advisory"][0]
        self.assertEqual(adv["kind"], "untracked-governed-artifact")
        self.assertEqual(adv["relpath"], "tasks/open/rogue-note.md")

    def test_raw_create_copying_mediated_bytes_is_not_clean(self):
        # Regression: provenance is path-bound. A raw-created governed artifact
        # must not launder itself by copying the exact bytes of a prior mediated
        # write of *another* path. Here STATE.md is mediated, then a brand-new
        # task file is raw-created with STATE.md's identical content. Its hash
        # appears in the log (for STATE.md), but under no entry for the task's
        # own path — so the floor must still flag it, not accept it as clean.
        body = "shared content bytes\n"
        mediated_write(self.root, "state", "STATE.md", body)
        copied = Path(self.root) / "tasks/open/copied-note.md"
        copied.write_text(body, encoding="utf-8")
        # Same bytes, hence same hash, as the mediated STATE.md write.
        self.assertEqual(
            hash_bytes(copied.read_bytes()),
            hash_bytes(body.encode("utf-8")),
        )
        result = audit_provenance(self.root)
        # The audit must NOT be clean: the copied path has no path-bound
        # provenance, so it surfaces as an untracked-governed-artifact advisory.
        self.assertEqual(result["guard"], [])
        self.assertEqual(len(result["advisory"]), 1, result)
        adv = result["advisory"][0]
        self.assertEqual(adv["kind"], "untracked-governed-artifact")
        self.assertEqual(adv["relpath"], "tasks/open/copied-note.md")
        # Not clean: at least one advisory was raised for the laundered file.
        self.assertTrue(result["guard"] or result["advisory"])

    def test_no_log_emits_single_baseline_advisory_no_guard(self):
        (Path(self.root) / "STATE.md").write_text("s\n", encoding="utf-8")
        (Path(self.root) / "tasks/open/sample-a.md").write_text("t\n", encoding="utf-8")
        # No mediated write has ever happened: no log exists.
        self.assertFalse((Path(self.root) / LOG_RELPATH).exists())
        result = audit_provenance(self.root)
        self.assertEqual(result["baseline"], "absent")
        self.assertEqual(result["guard"], [])
        self.assertEqual(len(result["advisory"]), 1)
        self.assertEqual(result["advisory"][0]["kind"], "no-provenance-baseline")


class TestMoveTaskProvenance(_ProjectFixture):
    def _run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "cli.main", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_move_carries_provenance_and_post_move_edit_is_guard(self):
        mediated_write(self.root, "task", "open/sample-a.md", "body\n")
        src = Path(self.root) / "tasks/open/sample-a.md"
        proc = self._run_cli("move-task", str(src), "in-progress")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        moved = Path(self.root) / "tasks/in-progress/sample-a.md"
        self.assertTrue(moved.is_file())

        # The moved file (unchanged content, new path) audits clean.
        clean = audit_provenance(self.root)
        self.assertEqual(clean["guard"], [])
        self.assertEqual(clean["advisory"], [])

        # A raw edit *after* the move is still a guard, not a downgraded advisory.
        moved.write_text("tampered after move\n", encoding="utf-8")
        drifted = audit_provenance(self.root)
        self.assertEqual(len(drifted["guard"]), 1, drifted)
        self.assertEqual(drifted["guard"][0]["relpath"], "tasks/in-progress/sample-a.md")


class TestPlanAuditEndToEnd(_ProjectFixture):
    """Exercise the floor through the real `cartopian plan-audit` CLI — the
    portable, no-interception path."""

    def _plan_audit(self):
        return subprocess.run(
            [sys.executable, "-m", "cli.main", "plan-audit", self.root],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def _audit_record(self, stdout):
        for line in stdout.splitlines():
            obj = json.loads(line)
            if obj.get("action") == "plan-audit":
                return obj
        self.fail(f"no plan-audit record in stdout: {stdout!r}")

    def test_mediated_change_passes_silently(self):
        mediated_write(self.root, "state", "STATE.md", "orientation\n")
        proc = self._plan_audit()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rec = self._audit_record(proc.stdout)
        self.assertTrue(rec["clean"], rec)
        self.assertEqual(rec["provenance"]["guard"], [])
        self.assertNotIn("[guard]", proc.stderr)

    def test_raw_create_copying_mediated_bytes_is_detected(self):
        # Regression through the real CLI: a raw-created governed artifact whose
        # bytes are copied from a prior mediated write of another path must be
        # surfaced (path-bound provenance), not silently passed. Before the fix
        # the content-hash match laundered it to a zero-finding clean audit.
        body = "orientation\n"
        mediated_write(self.root, "state", "STATE.md", body)
        # Raw-create a task file with STATE.md's exact bytes.
        (Path(self.root) / "tasks/open/copied-note.md").write_text(
            body, encoding="utf-8"
        )
        proc = self._plan_audit()
        rec = self._audit_record(proc.stdout)
        # Surfaced as a path-bound untracked-artifact advisory, not silent.
        self.assertIn("[advisory]", proc.stderr)
        advisories = rec["provenance"]["advisory"]
        copied = [
            a for a in advisories
            if a["kind"] == "untracked-governed-artifact"
            and a["relpath"] == "tasks/open/copied-note.md"
        ]
        self.assertEqual(len(copied), 1, rec)
        # The mediated STATE.md itself stays attributed (no guard fired).
        self.assertEqual(rec["provenance"]["guard"], [])

    def test_seeded_raw_edit_is_detected(self):
        mediated_write(self.root, "state", "STATE.md", "orientation\n")
        # Seed the out-of-band edit required by the evidence gate.
        (Path(self.root) / "STATE.md").write_text("hand-edited\n", encoding="utf-8")
        proc = self._plan_audit()
        self.assertEqual(proc.returncode, 1, (proc.stdout, proc.stderr))
        self.assertIn("[guard]", proc.stderr)
        rec = self._audit_record(proc.stdout)
        self.assertFalse(rec["clean"], rec)
        guards = rec["provenance"]["guard"]
        self.assertEqual(len(guards), 1, rec)
        self.assertEqual(guards[0]["kind"], "raw-edit")
        self.assertEqual(guards[0]["relpath"], "STATE.md")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
