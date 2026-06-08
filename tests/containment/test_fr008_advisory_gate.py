"""FR-008 advisory-tier notice, acknowledgment & persisted decision (TASK-02-002).

The lifecycle behavior on top of TASK-02-001 detection: a PM harness that
classifies **tier-3** (Cartopian cannot prove it can constrain it to Tier 1/2)
must not proceed silently, but it also must not make project orientation
dependent on a terminal-only operator acknowledgment. Lifecycle entry proceeds
under a visible advisory banner. A recorded acknowledgment is optional audit
trail that annotates the banner; revoked or mismatched records are treated as no
record. Tier-1/2 (or no configured harness) is unaffected.

Red-before-green
----------------
:class:`TestRedNoAdvisoryGateBaseline` pins the pre-task baseline: detection
alone proves the harness is unconstrainable (tier-3), yet the only launch block
that existed before this task — the FR-013 contained-PM git guard — does not
fire for a tier-3 harness whose config has no ``pm_owns_product_branches`` combo.
So *without* this gate a tier-3 PM reaches lifecycle entry with **no block**
(the silent-unconstrained-continue FR-008 forbids). The green classes then
assert the gate closes exactly that hole. This is the documented in-module red
baseline (naive / pre-guard / fail-closed framing) for the manifest.

The advisory is exercised at both lifecycle-entry surfaces a PM reaches through
the Cartopian toolset — ``resolve-config`` and ``next-action`` — via the real
``bin/cartopian`` entrypoint, and the optional acknowledgment command remains
tested separately as an audit helper.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

# A real tier-3 harness in this checkout: no wrappers/bin/cartopian-cascade-pm
# and no native-sandbox depth profile exist, so detection classifies it tier-3.
TIER3_HARNESS = "cascade"
# A real tier-1-2 harness: Claude Code ships both required assets.
TIER12_AGENT = "cartopian-claude-pm"

_PROJECT_HEADER = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'protocol_version = "v0.2.0"\n'
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _Sandbox:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "proj"
        self.home.mkdir()
        self.project.mkdir()

    def write_project(self, *, agent=None, pm_owns=None):
        body = _PROJECT_HEADER
        if agent is not None:
            body += f'\n[handoffs.pm]\nagent = "{agent}"\n'
        if pm_owns is not None:
            body += f"\n[git]\npm_owns_product_branches = {str(bool(pm_owns)).lower()}\n"
        _write(self.project / "cartopian.toml", body)

    def env(self, *, contained=False):
        env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
        if contained:
            env["CARTOPIAN_PM_CONTAINED"] = "1"
        return env

    def run(self, command, *, contained=False):
        return subprocess.run(
            [sys.executable, str(ENTRYPOINT), command, str(self.project)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=self.env(contained=contained),
        )

    def acknowledge(self, *extra):
        return subprocess.run(
            [sys.executable, "-m", "cli.commands.acknowledge_harness",
             str(self.project), *extra],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=self.env(),
        )

    def read_ledger(self):
        return (self.project / "COMPATIBILITY.md").read_text(encoding="utf-8")

    def cleanup(self):
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.cleanup()


def _ack_valid(sb, *, harness=TIER3_HARNESS):
    """Record a valid acknowledgment through the real operator command."""
    res = sb.acknowledge(
        "--harness", harness,
        "--acknowledged-by", "operator:scott",
        "--rationale", "local dev — accept unconstrained Tier-3 risk",
        "--acknowledged-on", "2026-06-01",
    )
    assert res.returncode == 0, f"ack failed: {res.stderr!r}\n{res.stdout!r}"
    return res


def _assert_blocked(tc, result):
    tc.assertNotEqual(result.returncode, 0,
                      msg=f"expected non-zero exit, got 0\nstdout={result.stdout!r}")
    tc.assertIn("[guard]", result.stderr, msg=f"stderr missing [guard]: {result.stderr!r}")
    # No lifecycle action proceeds: nothing on stdout.
    tc.assertEqual(result.stdout.strip(), "",
                   msg=f"a lifecycle record was emitted despite the block: {result.stdout!r}")


def _assert_proceeds(tc, result):
    tc.assertEqual(result.returncode, 0,
                   msg=f"expected exit 0, got {result.returncode}\nstderr={result.stderr!r}")
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    tc.assertEqual(len(lines), 1, msg=f"expected one NDJSON record, got: {result.stdout!r}")
    json.loads(lines[0])


class TestRedNoAdvisoryGateBaseline(unittest.TestCase):
    """RED baseline: detection proves tier-3, but no pre-task block covers it.

    Documents the silent-unconstrained-continue hole this task closes. We assert
    the unconstrainable condition is real (detection → tier-3) and that the only
    prior launch block (FR-013) yields no guard for this config — i.e. before the
    FR-008 gate a tier-3 PM had nothing stopping it at lifecycle entry.
    """

    def test_detection_classifies_tier3_but_fr013_does_not_block(self):
        from cli.commands._harness_tier import classify_pm_tier_from_paths
        from cli.commands._containment import (
            contained_pm_owned_git_block_message,
            resolve_pm_owns_from_paths,
        )
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            tier = classify_pm_tier_from_paths(sb.project, home=sb.home)
            # The unconstrainable condition is real.
            self.assertEqual(tier.tier, "tier-3")
            self.assertEqual(tier.harness, "cascade")
            # The pre-task FR-013 guard does NOT fire here (not the pm-owns combo,
            # not contained): lifecycle orientation must therefore rely on the
            # advisory surface rather than a hard block.
            self.assertIsNone(
                contained_pm_owned_git_block_message(
                    resolve_pm_owns_from_paths(sb.project, home=sb.home),
                    contained=False,
                ),
                msg="red: before FR-008 nothing blocked a tier-3 PM at lifecycle entry",
            )


class TestTier3NoRecordProceedsWithAdvisory(unittest.TestCase):
    """GREEN gate (1): tier-3 + no record → lifecycle proceeds with advisory."""

    def _assert_advisory_names_essentials(self, result, harness=TIER3_HARNESS):
        self.assertIn("[advisory]", result.stderr,
                      msg=f"stderr missing [advisory]: {result.stderr!r}")
        self.assertIn(harness, result.stderr, msg="advisory must name the harness")
        # Names the missing assets (the floor / depth profile paths from detection).
        self.assertIn("profile", result.stderr,
                      msg=f"advisory must name the missing assets: {result.stderr!r}")
        self.assertIn("Continuing lifecycle entry", result.stderr)
        self.assertNotIn("python", result.stderr.lower())
        self.assertNotIn("[guard]", result.stderr)

    def test_resolve_config_proceeds(self):
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            result = sb.run("resolve-config")
        _assert_proceeds(self, result)
        self._assert_advisory_names_essentials(result)

    def test_next_action_proceeds(self):
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            result = sb.run("next-action")
        _assert_proceeds(self, result)
        self._assert_advisory_names_essentials(result)

    def test_arbitrary_agent_value_proceeds(self):
        with _Sandbox() as sb:
            sb.write_project(agent="agent")
            result = sb.run("next-action")
        _assert_proceeds(self, result)
        self._assert_advisory_names_essentials(result, harness="agent")


class TestAcknowledgedProceeds(unittest.TestCase):
    """GREEN gate (2): tier-3 + valid record → lifecycle proceeds + advisory."""

    def _assert_advisory(self, result):
        self.assertIn("[advisory]", result.stderr,
                      msg=f"expected a persistent advisory banner: {result.stderr!r}")
        self.assertIn("cascade", result.stderr)
        self.assertIn("Tier-3", result.stderr)

    def test_resolve_config_proceeds_with_advisory(self):
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            _ack_valid(sb)
            result = sb.run("resolve-config")
        _assert_proceeds(self, result)
        self._assert_advisory(result)

    def test_next_action_proceeds_with_advisory(self):
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            _ack_valid(sb)
            result = sb.run("next-action")
        _assert_proceeds(self, result)
        self._assert_advisory(result)

    def test_no_reprompt_idempotent_across_sessions(self):
        """Two consecutive launches both proceed identically — the record is the
        audit trail; the gate never re-prompts or mutates state."""
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            _ack_valid(sb)
            ledger_before = sb.read_ledger()
            first = sb.run("resolve-config")
            second = sb.run("resolve-config")
            ledger_after = sb.read_ledger()
        _assert_proceeds(self, first)
        _assert_proceeds(self, second)
        self._assert_advisory(first)
        self._assert_advisory(second)
        self.assertEqual(ledger_before, ledger_after,
                         msg="the gate must not mutate the record on launch")


class TestRevokedOrMismatchedRecordsProceed(unittest.TestCase):
    """GREEN gate (2, negative): revoked / mismatched record is treated as no record."""

    def _assert_unrecorded_advisory(self, result):
        _assert_proceeds(self, result)
        self.assertIn("[advisory]", result.stderr)
        self.assertIn("Continuing lifecycle entry", result.stderr)
        self.assertNotIn("[guard]", result.stderr)

    def test_revoked_record_proceeds_with_unrecorded_advisory(self):
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            _ack_valid(sb)
            rev = sb.acknowledge("--harness", TIER3_HARNESS, "--revoke")
            self.assertEqual(rev.returncode, 0, msg=rev.stderr)
            result = sb.run("resolve-config")
        self._assert_unrecorded_advisory(result)

    def test_mismatched_harness_proceeds_with_unrecorded_advisory(self):
        """A record for a different harness does not satisfy the gate."""
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            # Acknowledge a *different* tier-3 harness, not the configured one.
            # (devin is still tier-3; gemini was promoted to tier-1-2 in TASK-03-002.)
            res = sb.acknowledge(
                "--harness", "devin",
                "--acknowledged-by", "operator",
                "--rationale", "other harness",
                "--acknowledged-on", "2026-06-01",
            )
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            result = sb.run("resolve-config")
        self._assert_unrecorded_advisory(result)

    def test_mismatched_project_proceeds_with_unrecorded_advisory(self):
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            res = sb.acknowledge(
                "--harness", TIER3_HARNESS,
                "--project-id", "some-other-project",
                "--acknowledged-by", "operator",
                "--rationale", "wrong project",
                "--acknowledged-on", "2026-06-01",
            )
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            result = sb.run("resolve-config")
        self._assert_unrecorded_advisory(result)


class TestNoRegressionTier12(unittest.TestCase):
    """GREEN gate (3) / NF-004: tier-1/2 and unconfigured harnesses are unaffected."""

    def test_tier12_harness_proceeds_without_advisory(self):
        with _Sandbox() as sb:
            sb.write_project(agent=TIER12_AGENT)
            result = sb.run("resolve-config")
        _assert_proceeds(self, result)
        self.assertNotIn("[advisory]", result.stderr,
                         msg="a constrained harness must not emit an advisory")
        self.assertNotIn("[guard]", result.stderr)

    def test_no_configured_harness_proceeds(self):
        """No [handoffs.pm].agent → default constrained launch; today's behavior."""
        with _Sandbox() as sb:
            sb.write_project(agent=None)
            rc = sb.run("resolve-config")
            na = sb.run("next-action")
        _assert_proceeds(self, rc)
        _assert_proceeds(self, na)
        self.assertNotIn("[advisory]", rc.stderr)
        self.assertNotIn("[guard]", rc.stderr)

    def test_fr013_guard_still_fires(self):
        """The FR-013 contained-PM git guard is unchanged (no regression)."""
        with _Sandbox() as sb:
            sb.write_project(agent=TIER12_AGENT, pm_owns=True)
            result = sb.run("resolve-config", contained=True)
        _assert_blocked(self, result)
        self.assertIn("pm_owns_product_branches", result.stderr)


class TestAcknowledgmentCommand(unittest.TestCase):
    """The operator-only command: schema-complete record, tier-3 only, FR-014 surface."""

    def test_records_schema_complete_entry(self):
        from cli.commands import _compatibility
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            res = _ack_valid(sb)
            # FR-014: a single NDJSON record on stdout.
            record = json.loads(res.stdout.strip())
            self.assertEqual(record["action"], "acknowledge-harness")
            # The persisted ledger entry carries every SPEC-02-002 field.
            recs = _compatibility.parse_ledger(sb.read_ledger())
            self.assertEqual(len(recs), 1)
            r = recs[0]
            self.assertEqual(r.harness, "cascade")
            self.assertEqual(r.project_id, "demo")
            self.assertEqual(r.tier, "tier-3")
            self.assertTrue(r.missing_assets)
            self.assertEqual(r.acknowledged_by, "operator:scott")
            self.assertEqual(r.acknowledged_on, "2026-06-01")
            self.assertTrue(r.rationale)
            self.assertFalse(r.revoked)

    def test_refuses_to_acknowledge_a_constrained_harness(self):
        """Fail-closed: you cannot record a phantom ack for a tier-1/2 harness."""
        with _Sandbox() as sb:
            sb.write_project(agent=TIER12_AGENT)
            res = sb.acknowledge(
                "--harness", TIER12_AGENT,
                "--acknowledged-by", "operator",
                "--rationale", "should be refused",
                "--acknowledged-on", "2026-06-01",
            )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("[guard]", res.stderr)
        self.assertIn("not-tier-3", res.stderr)
        self.assertFalse((sb.project / "COMPATIBILITY.md").exists(),
                         msg="no ledger may be written when the harness is constrained")

    def test_acknowledge_requires_explicit_inputs(self):
        """Acknowledgment is a separate explicit action — never a bare default."""
        with _Sandbox() as sb:
            sb.write_project(agent=TIER3_HARNESS)
            res = sb.acknowledge("--harness", TIER3_HARNESS)  # no --acknowledged-by/--rationale
        self.assertEqual(res.returncode, 2)
        self.assertIn("[usage]", res.stderr)


class TestAcknowledgmentNotOnPmSurface(unittest.TestCase):
    """The acknowledgment command must NOT reach the contained PM's tool surface.

    The MCP server auto-exposes every cli.main.SUBCOMMANDS entry as a tool; if
    this command were registered there the contained PM could acknowledge its own
    unconstrained risk, making the FR-008 gate self-bypassable. It is therefore
    deliberately unregistered (operator-only), mirroring the mediated-write shim.
    """

    def test_absent_from_cli_subcommands(self):
        from cli import main as cli_main
        for name in ("acknowledge-harness", "acknowledge_harness", "ack-harness"):
            self.assertNotIn(name, cli_main.SUBCOMMANDS)
        self.assertNotIn("acknowledge-harness", cli_main._real_handlers())

    def test_absent_from_mcp_tool_registry(self):
        from mcp_server import server
        names = {t["name"] for t in server.list_tools()}
        self.assertNotIn("acknowledge_harness", names)
        self.assertNotIn("acknowledge-harness", names)


if __name__ == "__main__":
    unittest.main()
