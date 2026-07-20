"""Tests for `cartopian handoff-packet`.

Covers the happy path (NDJSON contract), no-plan project, missing
config → EXIT_ENV, missing [handoffs.<role>] guard, and read-only invariant.
"""
import argparse
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.commands import handoff_packet
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "test-proj"\n'
    'name = "Test Project"\n'
    'protocol_version = "v0.5.0"\n'
    'work_roots = ["tool-repo"]\n'
    "\n"
    "[roles]\n"
    'coder = "Implements tasks per spec."\n'
    "\n"
    "[handoffs.coder]\n"
    'agent = "cartopian-claude"\n'
    'model = "claude-opus-4-8"\n'
    'effort = "high"\n'
    "auto_start_tasks = true\n"
    'timeout = "30m"\n'
    "\n"
    "[reviews]\n"
    'planning = "off"\n'
    'task_closure = "off"\n'
)


def _invoke(task_path: str, role: str):
    """Invoke handler and capture serialized stdout+stderr; return (stdout, stderr, exit_code).

    Captures real stdout bytes so assertions verify the NDJSON machine surface
    rather than handler-internal Python objects. Patches
    ``pathlib.Path.home`` to a missing directory so the user's real
    ``~/.cartopian/cartopian.toml`` cannot leak into the test record.
    """
    args = argparse.Namespace(task_path=task_path, role=role)
    out = io.StringIO()
    err = io.StringIO()
    with tempfile.TemporaryDirectory(prefix="cartopian-fake-home-") as fake_home:
        with mock.patch("cli.commands.handoff_packet.Path.home", return_value=Path(fake_home)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = handoff_packet.handler(args)
    return out.getvalue(), err.getvalue(), rc


def _snapshot_tree(root: Path) -> dict:
    """Return a {relative_path: (size, sha256, mtime_ns)} map for every file under ``root``.

    Used to assert the read-only NFR-001 invariant: no file under the
    project tree may be created, modified, or deleted by a single handoff-
    packet invocation.
    """
    snapshot: dict = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        stat = path.stat()
        snapshot[rel] = (
            stat.st_size,
            hashlib.sha256(data).hexdigest(),
            stat.st_mtime_ns,
        )
    return snapshot


class TestHandoffPacketHappyPath(unittest.TestCase):
    def test_emits_ndjson_record_to_stdout(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            work_root = scaffold.root / "tool-repo"
            work_root.mkdir()
            scaffold.write(
                "cartopian.local.toml",
                f"[work_roots]\ntool-repo = \"{work_root}\"\n",
            )
            task_path = scaffold.write(
                "tasks/open/TASK-01-002-example.md",
                (
                    "# TASK-01-002: Example\n\n"
                    "Phase: PHASE-01-foundation\n"
                    "Plan ref: n/a\n"
                    "Work root: tool-repo\n"
                    "Assignee: coder\n"
                    "Spec: none\n"
                    "Depends on: n/a\n"
                    "Blocked by: n/a\n"
                    "Created: 2026-05-18\n"
                    "Evidence gate: n/a\n\n"
                    "## Goal\n\nExample goal.\n"
                ),
            )

            stdout, _stderr, rc = _invoke(str(task_path), "coder")

            self.assertEqual(rc, EXIT_OK)
            # NDJSON contract: exactly one non-empty line terminated by `\n`.
            self.assertTrue(
                stdout.endswith("\n"),
                msg=f"expected trailing newline; got: {stdout!r}",
            )
            lines = [ln for ln in stdout.split("\n") if ln]
            self.assertEqual(
                len(lines), 1,
                msg=f"expected exactly one NDJSON line; got: {stdout!r}",
            )
            rec = json.loads(lines[0])

            for field in (
                "task_id",
                "task_title",
                "task_path",
                "role",
                "role_description",
                "handoff_target",
                "model",
                "effort",
                "auto_start_tasks",
                "auto_start_reviews",
                "timeout",
                "work_roots",
                "expected_report_path",
                "git_versioning",
                "git_policy",
                "automation_policy",
                "reviews",
            ):
                self.assertIn(field, rec, msg=f"missing field: {field}")

            self.assertEqual(rec["task_id"], "TASK-01-002")
            self.assertEqual(rec["task_title"], "TASK-01-002: Example")
            self.assertEqual(rec["role"], "coder")
            self.assertEqual(rec["role_description"], "Implements tasks per spec.")
            self.assertEqual(rec["handoff_target"], "cartopian-claude")
            self.assertEqual(rec["model"], "claude-opus-4-8")
            self.assertEqual(rec["effort"], "high")
            self.assertTrue(rec["auto_start_tasks"])
            self.assertFalse(rec["auto_start_reviews"])
            self.assertNotIn("auto_start", rec)
            self.assertEqual(rec["timeout"], "30m")
            self.assertEqual(
                rec["work_roots"],
                [{"name": "tool-repo", "absolute_path": str(work_root)}],
            )
            self.assertTrue(rec["expected_report_path"].endswith("/reports/REPORT-01-002.md"))
            self.assertFalse(rec["git_versioning"])
            self.assertIsNone(rec["git_policy"])
            self.assertEqual(
                rec["automation_policy"],
                {
                    "initiation": "operator",
                    "confirmation": "each-handoff",
                    "max_handoffs_per_run": 1,
                },
            )
            self.assertEqual(rec["reviews"]["planning"]["mode"], "off")
            self.assertEqual(rec["reviews"]["task_closure"]["mode"], "off")


class TestHandoffPacketNoPlanState(unittest.TestCase):
    """No IMPLEMENTATION_PLAN.md and no role/git/automation extras — every
    nullable field must be serialized as JSON ``null`` rather than elided.
    The PM relies on a stable field set across project shapes.
    """

    _MIN_TOML = (
        "[project]\n"
        'id = "min-proj"\n'
        'name = "Minimal"\n'
        'protocol_version = "v0.5.0"\n'
        "\n"
        "[handoffs.coder]\n"
        'agent = "cartopian-claude"\n'
    )

    def test_emits_null_for_unset_optional_fields(self) -> None:
        with project_scaffold(cartopian_toml=self._MIN_TOML) as scaffold:
            # Remove the scaffolded phases dir so neither the phases dir
            # nor IMPLEMENTATION_PLAN.md exists — exercises the "no plan"
            # fallback in _find_project_root.
            (scaffold.project_root / "phases").rmdir()
            task_path = scaffold.write(
                "tasks/open/TASK-09-007-no-plan.md",
                "# TASK-09-007: No Plan\n",
            )

            stdout, _stderr, rc = _invoke(str(task_path), "coder")

            self.assertEqual(rc, EXIT_OK)
            rec = json.loads(stdout.strip())

            # Nullable fields must be present and explicitly null,
            # not omitted from the record.
            raw = stdout.strip()
            self.assertIn('"role_description":null', raw)
            self.assertIn('"model":null', raw)
            self.assertIn('"effort":null', raw)
            self.assertIn('"auto_start_tasks":null', raw)
            self.assertIn('"auto_start_reviews":null', raw)
            self.assertIn('"timeout":null', raw)
            self.assertIn('"git_policy":null', raw)

            self.assertIsNone(rec["role_description"])
            self.assertEqual(rec["handoff_target"], "cartopian-claude")
            self.assertIsNone(rec["model"])
            self.assertIsNone(rec["effort"])
            self.assertIsNone(rec["auto_start_tasks"])
            self.assertIsNone(rec["auto_start_reviews"])
            self.assertIsNone(rec["timeout"])
            self.assertEqual(rec["work_roots"], [])
            self.assertFalse(rec["git_versioning"])
            self.assertIsNone(rec["git_policy"])
            # Automation policy is always populated from protocol defaults.
            self.assertEqual(
                rec["automation_policy"],
                {
                    "initiation": "operator",
                    "confirmation": "each-handoff",
                    "max_handoffs_per_run": 1,
                },
            )
            self.assertEqual(rec["task_id"], "TASK-09-007")
            self.assertTrue(
                rec["expected_report_path"].endswith("/reports/REPORT-09-007.md")
            )


class TestHandoffPacketGitOperatingModel(unittest.TestCase):
    def test_enabled_git_policy_exposes_product_branch_ownership_and_defaults(self) -> None:
        configured = (
            _TOML
            + "\n[defaults]\ngit_versioning = true\n"
            + "\n[git]\npm_owns_product_branches = false\n"
        )
        with project_scaffold(cartopian_toml=configured) as scaffold:
            work_root = scaffold.root / "tool-repo"
            work_root.mkdir()
            scaffold.write(
                "cartopian.local.toml",
                f'[work_roots]\ntool-repo = "{work_root}"\n',
            )
            task_path = scaffold.write(
                "tasks/open/TASK-01-003-verify.md",
                "# TASK-01-003: Verify\n\nWork root: tool-repo\n",
            )

            stdout, stderr, rc = _invoke(str(task_path), "coder")

            self.assertEqual(rc, EXIT_OK, msg=stderr)
            record = json.loads(stdout)
            self.assertTrue(record["git_versioning"])
            self.assertEqual(
                record["git_policy"],
                {
                    "pm_owns_product_branches": False,
                    "default_branch_pattern": "task/{task_id}-{slug}",
                    "default_merge_strategy": "merge",
                },
            )


class TestHandoffPacketMissingConfig(unittest.TestCase):
    """No ancestor ``cartopian.toml`` exists → EXIT_ENV (3) with an
    ``[error]`` stderr line. Tests the environment-failure contract.
    """

    def test_exits_env_when_no_cartopian_toml(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-no-config-") as tmp:
            tmp_root = Path(tmp)
            task_path = tmp_root / "TASK-01-099-orphan.md"
            task_path.write_text("# TASK-01-099: Orphan\n", encoding="utf-8")

            stdout, stderr, rc = _invoke(str(task_path), "coder")

            self.assertEqual(rc, EXIT_ENV)
            self.assertEqual(stdout, "")
            self.assertIn("[error]", stderr)
            self.assertIn("project config not found", stderr)


class TestHandoffPacketMissingHandoffBlock(unittest.TestCase):
    """Requesting a role with no ``[handoffs.<role>]`` block (project or
    global) must fail non-zero with a ``[guard]`` stderr line.
    """

    _TOML_NO_HANDOFFS = (
        "[project]\n"
        'id = "no-handoff-proj"\n'
        'name = "No Handoffs"\n'
        'protocol_version = "v0.5.0"\n'
        "\n"
        "[roles]\n"
        'coder = "Implements tasks per spec."\n'
    )

    def test_emits_guard_when_role_block_missing(self) -> None:
        with project_scaffold(cartopian_toml=self._TOML_NO_HANDOFFS) as scaffold:
            task_path = scaffold.write(
                "tasks/open/TASK-01-050-noblock.md",
                "# TASK-01-050: No Block\n",
            )

            stdout, stderr, rc = _invoke(str(task_path), "coder")

            self.assertNotEqual(rc, EXIT_OK)
            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(stdout, "")
            self.assertIn("[guard]", stderr)
            self.assertIn("[handoffs.coder]", stderr)


class TestHandoffPacketReadOnlyInvariant(unittest.TestCase):
    """NFR-001: ``handoff-packet`` must not write, move, rename, or delete
    any file. Hash every file in the project tree before and after the
    invocation and assert nothing changed.
    """

    def test_does_not_mutate_project_tree(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            work_root = scaffold.root / "tool-repo"
            work_root.mkdir()
            scaffold.write(
                "cartopian.local.toml",
                f"[work_roots]\ntool-repo = \"{work_root}\"\n",
            )
            task_path = scaffold.write(
                "tasks/open/TASK-01-002-example.md",
                (
                    "# TASK-01-002: Example\n\n"
                    "Phase: PHASE-01-foundation\n"
                    "## Goal\n\nExample goal.\n"
                ),
            )
            # Seed sibling files so a stray write/move/delete is detectable
            # anywhere in the tree, not just on the task file itself.
            scaffold.write("reports/.keep", "")
            scaffold.write("prompts/.keep", "")
            scaffold.write("reviews/.keep", "")

            before = _snapshot_tree(scaffold.project_root)
            before_count = len(before)
            self.assertGreater(before_count, 0)

            _stdout, _stderr, rc = _invoke(str(task_path), "coder")
            self.assertEqual(rc, EXIT_OK)

            after = _snapshot_tree(scaffold.project_root)

            # Compare path sets first for clearer failure messages.
            self.assertEqual(
                set(before.keys()),
                set(after.keys()),
                msg="handoff-packet created or removed files under the project tree",
            )
            for rel, fingerprint in before.items():
                self.assertEqual(
                    fingerprint,
                    after[rel],
                    msg=f"handoff-packet mutated {rel}: {fingerprint} -> {after[rel]}",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
