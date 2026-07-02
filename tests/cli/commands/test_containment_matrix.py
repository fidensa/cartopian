"""Unit tests for `containment-matrix` — the honest per-host containment matrix.

Each supported host renders the *floor* of its static tier ceiling (the
authoritative operator-acceptance clearance source, encoded in code) and the
runtime evidence tier for the target project. The matrix must never overstate:
a gated ceiling is never exceeded, an ungated project renders advisory
everywhere, and every advisory row plainly names the detection-floor residual
(out-of-band writes detected after the fact, not prevented at the point of
write).
"""
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cli.main import build_parser
from tests.scaffold import project_scaffold

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK_PATH = REPO_ROOT / "cli" / "claude_hook.py"

_ACTIVATED_TOML = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'protocol_version = "v0.3.0"\n'
    "\n"
    "[roles.coder]\n"
    'description = "Implements tasks per spec."\n'
    'grants = ["coder-like"]\n'
)

_UNGATED_TOML = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'protocol_version = "v0.3.0"\n'
    "\n"
    "[roles]\n"
    'pm = "Plans the work."\n'
    'coder = "Writes code."\n'
)


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


class _Fixture(unittest.TestCase):
    """Scaffolded project + isolated HOME (no ambient global config leaks in)."""

    def setUp(self):
        self.scaffold = project_scaffold(cartopian_toml=_ACTIVATED_TOML)
        self.addCleanup(self.scaffold.cleanup)
        self.root = str(self.scaffold.project_root)
        fake_home = self.scaffold.root / "home"
        fake_home.mkdir()
        env_patch = patch.dict(os.environ, {"HOME": str(fake_home)})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    # The full matcher the installer writes (read + write tools) and the
    # pre-read-boundary form that intercepts only the mutation tools.
    FULL_MATCHER = "Read|NotebookRead|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit"
    WRITE_ONLY_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"

    def register_hook(self, matcher=FULL_MATCHER):
        """Register the real Claude Code refusal-adapter hook for this project."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": matcher,
                        "hooks": [
                            {
                                "type": "command",
                                "command": f'"python3" "{HOOK_PATH}"',
                            }
                        ],
                    }
                ]
            }
        }
        self.scaffold.write(
            ".claude/settings.json", json.dumps(settings, indent=2) + "\n"
        )

    def rows(self):
        code, recs, err = run_cli("containment-matrix", self.root)
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(len(recs), 1, msg="expected exactly one NDJSON record")
        record = recs[0]
        return record, {row["host"]: row for row in record["hosts"]}


class TestHonestTiersFromEvidence(_Fixture):
    def test_activated_project_with_registered_hook_renders_contained(self):
        self.register_hook()
        record, rows = self.rows()
        claude = rows["claude-code"]
        self.assertEqual(claude["tier"], "contained")
        self.assertEqual(claude["ceiling"], "contained")
        self.assertTrue(claude["interception_present"])
        self.assertTrue(claude["interception_registered"])
        self.assertTrue(claude["activated"])
        self.assertIsNone(claude["disclosure"])

    def test_detection_floor_only_host_renders_advisory(self):
        self.register_hook()
        _, rows = self.rows()
        for host in (
            "claude-desktop",
            "chatgpt-app",
            "antigravity-tui",
            "antigravity-ide",
            "devin",
        ):
            with self.subTest(host=host):
                self.assertEqual(rows[host]["tier"], "advisory+detection")
                self.assertFalse(rows[host]["interception_registered"])

    def test_activated_project_without_registration_degrades_to_advisory(self):
        # No .claude/settings.json written: the interception is not registered
        # for this project, so even the contained-ceiling host degrades.
        _, rows = self.rows()
        claude = rows["claude-code"]
        self.assertEqual(claude["tier"], "advisory+detection")
        self.assertFalse(claude["interception_registered"])
        self.assertIsNotNone(claude["disclosure"])

    def test_all_seven_hosts_present_with_assigned_ceilings(self):
        self.register_hook()
        _, rows = self.rows()
        expected_ceilings = {
            "claude-code": "contained",
            "codex-cli": "contained-partial",
            "antigravity-tui": "advisory+detection",
            "claude-desktop": "advisory+detection",
            "chatgpt-app": "advisory+detection",
            "antigravity-ide": "advisory+detection",
            "devin": "advisory+detection",
        }
        self.assertEqual(
            {host: row["ceiling"] for host, row in rows.items()}, expected_ceilings
        )


class TestReadBoundaryTiers(_Fixture):
    """The matrix renders the read boundary per host, honestly: enforced only
    where the interception point actually intercepts the read tools; advisory
    + detection (with a plain disclosure) everywhere else."""

    def test_full_matcher_renders_both_boundaries_contained(self):
        self.register_hook()
        _, rows = self.rows()
        claude = rows["claude-code"]
        self.assertEqual(claude["boundaries"]["write"]["tier"], "contained")
        self.assertEqual(claude["boundaries"]["read"]["tier"], "contained")
        self.assertEqual(claude["tier"], "contained")
        self.assertIsNone(claude["boundaries"]["read"]["disclosure"])

    def test_write_only_matcher_discloses_read_as_advisory(self):
        # A registration that intercepts only the mutation tools cannot claim
        # read enforcement: the read boundary — and therefore the overall
        # tier — degrades, and the disclosure names the read residual.
        self.register_hook(matcher=self.WRITE_ONLY_MATCHER)
        _, rows = self.rows()
        claude = rows["claude-code"]
        self.assertEqual(claude["boundaries"]["write"]["tier"], "contained")
        self.assertEqual(
            claude["boundaries"]["read"]["tier"], "advisory+detection"
        )
        self.assertFalse(claude["boundaries"]["read"]["interception_registered"])
        self.assertEqual(claude["tier"], "advisory+detection")
        read_disclosure = claude["boundaries"]["read"]["disclosure"]
        self.assertIsNotNone(read_disclosure)
        self.assertIn("read", read_disclosure)
        self.assertIsNotNone(claude["disclosure"])
        self.assertIn("read", claude["disclosure"])

    def test_read_boundary_advisory_on_hosts_without_adapter(self):
        self.register_hook()
        _, rows = self.rows()
        for host, row in rows.items():
            if host == "claude-code":
                continue
            with self.subTest(host=host):
                self.assertEqual(
                    row["boundaries"]["read"]["tier"], "advisory+detection"
                )
                self.assertFalse(
                    row["boundaries"]["read"]["interception_registered"]
                )
                self.assertIsNotNone(row["boundaries"]["read"]["disclosure"])

    def test_no_registration_degrades_both_boundaries(self):
        _, rows = self.rows()
        claude = rows["claude-code"]
        for boundary in ("read", "write"):
            with self.subTest(boundary=boundary):
                self.assertEqual(
                    claude["boundaries"][boundary]["tier"], "advisory+detection"
                )


class TestFailClosedGateWiring(_Fixture):
    def test_gated_ceiling_never_renders_contained_via_cli(self):
        self.register_hook()
        _, rows = self.rows()
        for host, row in rows.items():
            if host == "claude-code":
                continue
            with self.subTest(host=host):
                self.assertNotEqual(row["tier"], "contained")

    def test_gated_ceiling_never_renders_contained_even_with_full_evidence(self):
        # Drive the pure tier computation with maximal runtime evidence: an
        # open gate (ceiling below `contained`) must still cap the render.
        from cli.commands.containment_matrix import (
            TIER_ADVISORY,
            TIER_CONTAINED,
            TIER_PARTIAL,
            render_tier,
        )

        for ceiling in (TIER_PARTIAL, TIER_ADVISORY):
            with self.subTest(ceiling=ceiling):
                rendered = render_tier(
                    ceiling,
                    activated=True,
                    interception_present=True,
                    interception_registered=True,
                )
                self.assertNotEqual(rendered, TIER_CONTAINED)
                self.assertEqual(rendered, ceiling)


class TestUngatedProject(_Fixture):
    def setUp(self):
        super().setUp()
        self.scaffold.write("cartopian.toml", _UNGATED_TOML)

    def test_ungated_config_never_renders_contained_even_with_hook(self):
        self.register_hook()
        record, rows = self.rows()
        self.assertFalse(record["activated"])
        for host, row in rows.items():
            with self.subTest(host=host):
                self.assertEqual(row["tier"], "advisory+detection")
                self.assertFalse(row["activated"])

    def test_ungated_disclosure_names_the_config_as_cause(self):
        self.register_hook()
        _, rows = self.rows()
        disclosure = rows["claude-code"]["disclosure"]
        self.assertIsNotNone(disclosure)
        self.assertIn("ungated", disclosure)
        self.assertIn("no capability grants", disclosure)

    def test_ungated_read_boundary_is_advisory_with_ungated_disclosure(self):
        self.register_hook()
        _, rows = self.rows()
        read = rows["claude-code"]["boundaries"]["read"]
        self.assertEqual(read["tier"], "advisory+detection")
        self.assertIn("ungated", read["disclosure"])


class TestAdvisoryDisclosure(_Fixture):
    def test_advisory_rows_plainly_name_the_residual(self):
        self.register_hook()
        _, rows = self.rows()
        advisory_rows = [r for r in rows.values() if r["tier"] == "advisory+detection"]
        self.assertTrue(advisory_rows, msg="expected at least one advisory row")
        for row in advisory_rows:
            with self.subTest(host=row["host"]):
                disclosure = row["disclosure"]
                self.assertIsNotNone(disclosure)
                self.assertIn("detected after the fact", disclosure)
                self.assertIn("not prevented at the point of write", disclosure)

    def test_non_advisory_rows_carry_no_disclosure(self):
        self.register_hook()
        _, rows = self.rows()
        self.assertIsNone(rows["claude-code"]["disclosure"])


class TestUsageGuards(_Fixture):
    def test_relative_path_is_usage_error(self):
        code, recs, err = run_cli("containment-matrix", "relative/path")
        self.assertEqual(code, 2)
        self.assertEqual(recs, [])
        self.assertIn("[usage]", err)

    def test_missing_project_is_error(self):
        missing = str(self.scaffold.root / "nope")
        code, recs, err = run_cli("containment-matrix", missing)
        self.assertEqual(code, 1)
        self.assertEqual(recs, [])


class TestMcpExposure(unittest.TestCase):
    def test_auto_generated_tool_appears_in_tools_list(self):
        from mcp_server import server

        stdin = io.StringIO(
            json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            )
            + "\n"
        )
        stdout = io.StringIO()
        server.run(stdin=stdin, stdout=stdout)
        response = json.loads(stdout.getvalue().splitlines()[0])
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("containment_matrix", names)


if __name__ == "__main__":
    unittest.main()
