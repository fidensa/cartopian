"""Acceptance coverage for truthful installed-versus-running restart state."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.install_workflow import (
    _required_surface,
    _restart_projection_for_result,
    apply_workflow,
    plan_workflow,
)
from cli.restart_state import (
    CLIENT_RESTART_INSTRUCTIONS,
    RESTART_REASON_CODES,
    RESTART_STATUSES,
    RUNNING_SERVER_ENV,
    client_context_from_environment,
    evaluate_restart,
    normalize_client_context,
    running_server_from_environment,
)
from cli.version_identities import (
    installed_content,
    mcp_content_identity,
    running_server,
)
from mcp_server import server

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads(
    (
        REPO_ROOT
        / "tests"
        / "fixtures"
        / "restart_state"
        / "scenarios.json"
    ).read_text(encoding="utf-8")
)


def _write_mcp_surface(root: Path) -> None:
    (root / "mcp_server").mkdir(parents=True)
    (root / "mcp_server" / "a.py").write_text("a\n", encoding="utf-8")
    (root / "mcp_server" / "b.py").write_text("b\n", encoding="utf-8")
    (root / "cli").mkdir()
    (root / "cli" / "main.py").write_text("cli\n", encoding="utf-8")
    (root / "bin").mkdir()
    (root / "bin" / "cartopian-mcp").write_text(
        "unix\n", encoding="utf-8"
    )
    (root / "bin" / "cartopian-mcp.cmd").write_text(
        "windows\n", encoding="utf-8"
    )


class RestartStateFixtureTests(unittest.TestCase):
    def test_separate_disk_process_surface_and_client_facts(self) -> None:
        for name, scenario in SCENARIOS.items():
            with self.subTest(scenario=name):
                result = evaluate_restart(
                    installed=scenario["installed"],
                    running=scenario["running"],
                    affected_surfaces=scenario["affected_surfaces"],
                    client=scenario["client"],
                    prior_process=scenario["prior_process"],
                )
                self.assertEqual(result["status"], scenario["expected_status"])
                self.assertEqual(result["reason_code"], scenario["expected_reason"])
                self.assertEqual(
                    result["activation_claim_allowed"],
                    scenario["activation_claim_allowed"],
                )
                self.assertIn(result["status"], RESTART_STATUSES)
                self.assertIn(result["reason_code"], RESTART_REASON_CODES)

    def test_no_activation_claim_precedes_fresh_instance_proof(self) -> None:
        for name, scenario in SCENARIOS.items():
            result = evaluate_restart(
                installed=scenario["installed"],
                running=scenario["running"],
                affected_surfaces=scenario["affected_surfaces"],
                client=scenario["client"],
                prior_process=scenario["prior_process"],
            )
            with self.subTest(scenario=name):
                if result["status"] != "current":
                    self.assertFalse(result["activation_claim_allowed"])
                    self.assertEqual(result["activation_claim"], "none")
                else:
                    self.assertEqual(
                        result["fresh_proof"]["verification"], "verified"
                    )
                    self.assertTrue(result["fresh_proof"]["new_process"])
                    self.assertTrue(
                        result["fresh_proof"]["loaded_content_matches"]
                    )


class RestartAdapterTruthTests(unittest.TestCase):
    def _root(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        _write_mcp_surface(root)
        return root

    def test_readable_copy_reaches_installed_and_running_unverified(self) -> None:
        content = installed_content(self._root())
        self.assertEqual(content["mcp_completeness"], "complete")
        self.assertEqual(content["mcp_verification"], "unverified")
        self.assertEqual(content["mcp_state"], "unverified")

        loaded = running_server(
            content, process_id=3100, instance_id="process:3100"
        )
        self.assertEqual(loaded["state"], "unknown")
        self.assertEqual(
            loaded["loaded_content"]["mcp_verification"], "unverified"
        )
        self.assertEqual(
            loaded["loaded_content"]["mcp_completeness"], "complete"
        )

        installed_unverified = evaluate_restart(
            installed={
                "identity": content["mcp_identity"],
                "state": content["mcp_state"],
                "verification": content["mcp_verification"],
                "completeness": content["mcp_completeness"],
            },
            running={
                "process_id": 3100,
                "loaded_identity": content["mcp_identity"],
                "state": "current",
                "verification": "verified",
            },
            affected_surfaces={
                "mcp_affecting_change": True,
                "verification": "verified",
            },
            client=normalize_client_context("codex"),
        )
        self.assertEqual(
            installed_unverified["reason_code"],
            "installed_content_unverified",
        )

        running_unverified = evaluate_restart(
            installed={
                "identity": content["mcp_identity"],
                "state": "verified",
                "verification": "verified",
                "completeness": "complete",
            },
            running=loaded,
            affected_surfaces={
                "mcp_affecting_change": True,
                "verification": "verified",
            },
            client=normalize_client_context("codex"),
        )
        self.assertEqual(
            running_unverified["reason_code"],
            "running_content_unverified",
        )

    def test_git_provenance_preserves_verified_then_dirty(self) -> None:
        root = self._root()
        subprocess.run(
            ["git", "init", "-q", str(root)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Cartopian Test",
                "-c",
                "user.email=cartopian@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
            capture_output=True,
        )
        clean = installed_content(root)
        self.assertEqual(clean["verification"], "verified")
        self.assertEqual(clean["mcp_verification"], "verified")

        (root / "mcp_server" / "a.py").write_text(
            "dirty\n", encoding="utf-8"
        )
        dirty = installed_content(root)
        self.assertEqual(dirty["verification"], "dirty")
        self.assertEqual(dirty["mcp_verification"], "dirty")

    def test_mid_read_oserror_cannot_return_partial_identity(self) -> None:
        root = self._root()
        failed_path = root / "mcp_server" / "b.py"
        original = Path.read_bytes

        def flaky_read(path: Path) -> bytes:
            if path == failed_path:
                raise OSError("fixture read failure")
            return original(path)

        with patch.object(Path, "read_bytes", flaky_read):
            observation = mcp_content_identity(root)
        self.assertEqual(observation["completeness"], "incomplete")
        self.assertEqual(observation["state"], "incomplete")
        self.assertEqual(observation["verification"], "unverified")
        self.assertIsNone(observation["identity"])

    def test_environment_adapter_new_process_without_content_is_pending(
        self,
    ) -> None:
        raw = json.dumps(
            {
                "process_id": 4200,
                "instance_id": "process:4200:new",
                "loaded_content": None,
                "state": "unknown",
                "verification": "unknown",
            }
        )
        with patch.dict(os.environ, {RUNNING_SERVER_ENV: raw}):
            running = running_server_from_environment()
        result = evaluate_restart(
            installed={
                "identity": "sha256:current",
                "state": "verified",
                "verification": "verified",
                "completeness": "complete",
            },
            running=running,
            affected_surfaces={
                "mcp_affecting_change": True,
                "verification": "verified",
            },
            client=normalize_client_context("codex"),
            prior_process={
                "process_id": 4100,
                "instance_id": "process:4100:old",
            },
        )
        self.assertEqual(result["status"], "verification_pending")
        self.assertEqual(
            result["reason_code"], "fresh_process_content_unknown"
        )


class ClientInstructionTests(unittest.TestCase):
    def test_supported_client_selection_is_deterministic(self) -> None:
        aliases = {
            "codex-mcp-client": "codex",
            "Codex": "codex",
            "claude-code": "claude-code",
            "antigravity-client": "antigravity",
            "Devin": "devin",
            "Windsurf": "windsurf",
            "Claude Desktop": "claude-desktop",
            "Cursor": "cursor",
            "Hermes": "hermes",
        }
        for raw, expected in aliases.items():
            with self.subTest(raw=raw):
                first = normalize_client_context(raw, platform="darwin")
                second = normalize_client_context(raw, platform="darwin")
                self.assertEqual(first, second)
                self.assertEqual(first["id"], expected)
                self.assertEqual(first["state"], "supported")

    def test_host_marker_beats_client_info(self) -> None:
        """A well-formed registration-injected CARTOPIAN_MCP_HOST marker names
        the client even when clientInfo is the unmatchable SDK default."""
        with patch.dict(
            os.environ,
            {
                "CARTOPIAN_MCP_HOST": "hermes",
                "CARTOPIAN_MCP_CLIENT": "mcp",
                "CARTOPIAN_MCP_CONNECTED": "1",
            },
            clear=False,
        ):
            context = client_context_from_environment()
        self.assertEqual(context["id"], "hermes")
        self.assertEqual(context["state"], "supported")
        self.assertEqual(context["source"], "registration-env-marker")

    def test_unknown_host_marker_falls_through_to_client_info(self) -> None:
        """A marker outside the closed supported set is ignored — resolution
        falls through to clientInfo and stays fail-closed."""
        with patch.dict(
            os.environ,
            {
                "CARTOPIAN_MCP_HOST": "rogue-host",
                "CARTOPIAN_MCP_CLIENT": "mcp",
                "CARTOPIAN_MCP_CONNECTED": "1",
            },
            clear=False,
        ):
            context = client_context_from_environment()
        self.assertEqual(context["id"], "unsupported")
        self.assertEqual(context["state"], "unsupported")

    def test_each_supported_client_gets_exactly_one_direct_instruction(self) -> None:
        for client, mapping in CLIENT_RESTART_INSTRUCTIONS.items():
            with self.subTest(client=client):
                scenario = SCENARIOS["installed-current-running-stale"]
                result = evaluate_restart(
                    installed=scenario["installed"],
                    running=scenario["running"],
                    affected_surfaces=scenario["affected_surfaces"],
                    client=normalize_client_context(client, platform="linux"),
                    prior_process=scenario["prior_process"],
                )
                self.assertEqual(result["status"], "restart_required")
                self.assertEqual(result["instruction"], mapping)
                self.assertEqual(
                    list(result["instruction"]),
                    [
                        "class",
                        "action",
                        "expected_proof",
                        "evidence",
                        "residual_risk",
                    ],
                )
                self.assertTrue(result["instruction"]["action"].endswith("."))

    def test_codex_instruction_is_direct_and_has_expected_proof(self) -> None:
        scenario = SCENARIOS["installed-current-running-stale"]
        result = evaluate_restart(
            installed=scenario["installed"],
            running=scenario["running"],
            affected_surfaces=scenario["affected_surfaces"],
            client=normalize_client_context("codex-mcp-client"),
            prior_process=scenario["prior_process"],
        )
        self.assertEqual(result["instruction"]["action"], "Restart Codex.")
        proof = result["instruction"]["expected_proof"].lower()
        self.assertIn("new server process", proof)
        self.assertIn("matching installed mcp content", proof)

    def test_unsupported_client_never_gets_invented_guidance(self) -> None:
        scenario = SCENARIOS["installed-current-running-stale"]
        result = evaluate_restart(
            installed=scenario["installed"],
            running=scenario["running"],
            affected_surfaces=scenario["affected_surfaces"],
            client=normalize_client_context("unknown-ide"),
            prior_process=scenario["prior_process"],
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_code"], "client_unsupported")
        self.assertIsNone(result["instruction"])
        self.assertFalse(result["activation_claim_allowed"])

    def test_platform_parity_is_static_only_and_deterministic(self) -> None:
        for platform in ("darwin", "linux", "win32"):
            for client, instruction in CLIENT_RESTART_INSTRUCTIONS.items():
                with self.subTest(platform=platform, client=client):
                    context = normalize_client_context(
                        client, platform=platform
                    )
                    self.assertEqual(context["state"], "supported")
                    self.assertEqual(instruction["evidence"], "static-only")
                    self.assertTrue(instruction["residual_risk"])


class WorkflowRestartIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install_root = self.root / "install"
        self.client_home = self.root / "home"
        self.client_home.mkdir()
        apply_workflow(
            plan_workflow(
                source_root=REPO_ROOT,
                install_root=self.install_root,
                operation="fresh-install",
                client_home=self.client_home,
                clients=("codex",),
            )
        )

    @staticmethod
    def _running(
        identity, process_id: int, instance_id: str, *, verification="verified"
    ):
        return {
            "process_id": process_id,
            "instance_id": instance_id,
            "loaded_identity": identity,
            "state": "current" if identity else "unknown",
            "verification": verification,
            "authority": "connected-mcp-process",
        }

    def _update_with_stale_running_process(self):
        target = self.install_root / "mcp_server" / "server.py"
        target.write_text("# old connected server fixture\n", encoding="utf-8")
        inventory = plan_workflow(
            source_root=REPO_ROOT,
            install_root=self.install_root,
            operation="update",
            client_home=self.client_home,
            clients=("codex",),
        )
        old_identity = next(
            item
            for item in inventory["surfaces"]
            if item["kind"] == "mcp-server-files"
        )["observed_content_identity"]
        return apply_workflow(
            plan_workflow(
                source_root=REPO_ROOT,
                install_root=self.install_root,
                operation="update",
                client_home=self.client_home,
                clients=("codex",),
                running_server_fact=self._running(
                    old_identity, 5100, "process:5100:old"
                ),
                client_context=normalize_client_context(
                    "codex-mcp-client"
                ),
            )
        )

    def test_update_result_persists_restart_required_without_activation(self) -> None:
        result = self._update_with_stale_running_process()
        self.assertEqual(result["state"], "restart-required")
        self.assertTrue(result["outcome"]["restart_required"])
        self.assertFalse(result["outcome"]["fully_updated"])
        self.assertEqual(len(result["restarts"]), 1)
        restart = result["restarts"][0]
        self.assertEqual(restart["status"], "restart_required")
        self.assertEqual(restart["reason_code"], "running_content_stale")
        self.assertEqual(restart["instruction"]["action"], "Restart Codex.")
        self.assertFalse(restart["activation_claim_allowed"])

        persisted = json.loads(
            (self.install_root / "install-update-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(persisted["restarts"], result["restarts"])

    def test_fresh_matching_process_closes_persisted_restart(self) -> None:
        stale = self._update_with_stale_running_process()
        installed_identity = stale["restarts"][0]["installed_identity"]
        verified = apply_workflow(
            plan_workflow(
                source_root=REPO_ROOT,
                install_root=self.install_root,
                operation="verification",
                client_home=self.client_home,
                clients=("codex",),
                running_server_fact=self._running(
                    installed_identity, 5200, "process:5200:new"
                ),
                client_context=normalize_client_context(
                    "codex-mcp-client"
                ),
            )
        )
        restart = verified["restarts"][0]
        self.assertEqual(restart["status"], "current")
        self.assertEqual(restart["reason_code"], "fresh_process_current")
        self.assertTrue(restart["fresh_proof"]["new_process"])
        self.assertTrue(restart["activation_claim_allowed"])
        self.assertFalse(verified["outcome"]["restart_required"])
        self.assertIn(
            verified["outcome"]["status"],
            ("complete", "complete-qualified"),
        )

    def test_running_unknown_after_mcp_update_requires_restart(self) -> None:
        target = self.install_root / "mcp_server" / "server.py"
        target.write_text("# unknown running fixture\n", encoding="utf-8")
        result = apply_workflow(
            plan_workflow(
                source_root=REPO_ROOT,
                install_root=self.install_root,
                operation="update",
                client_home=self.client_home,
                clients=("codex",),
                running_server_fact=self._running(
                    None,
                    5100,
                    "process:5100:old",
                    verification="unknown",
                ),
                client_context=normalize_client_context(
                    "codex-mcp-client"
                ),
            )
        )
        self.assertEqual(
            result["restarts"][0]["reason_code"],
            "running_content_unknown",
        )
        self.assertEqual(result["state"], "restart-required")

    def test_verified_mcp_unrelated_change_does_not_require_restart(self) -> None:
        target = self.install_root / "protocol" / "INSTALL_VERIFICATION.md"
        target.write_text("# unrelated documentation drift\n", encoding="utf-8")
        result = apply_workflow(
            plan_workflow(
                source_root=REPO_ROOT,
                install_root=self.install_root,
                operation="update",
                client_home=self.client_home,
                clients=("codex",),
                running_server_fact=self._running(
                    None,
                    5100,
                    "process:5100:old",
                    verification="unknown",
                ),
                client_context=normalize_client_context(
                    "codex-mcp-client"
                ),
            )
        )
        restart = result["restarts"][0]
        self.assertEqual(restart["status"], "no_restart_needed")
        self.assertEqual(
            restart["reason_code"], "mcp_surface_unaffected"
        )
        self.assertIsNone(restart["instruction"])
        self.assertFalse(result["outcome"]["restart_required"])

    def test_partial_surface_observation_reaches_affected_unknown(self) -> None:
        failed_path = self.install_root / "mcp_server" / "server.py"
        original = Path.read_bytes

        def flaky_read(path: Path) -> bytes:
            if path == failed_path:
                raise OSError("fixture surface read failure")
            return original(path)

        with patch.object(Path, "read_bytes", flaky_read):
            surface = _required_surface(
                "mcp-server-files",
                REPO_ROOT,
                self.install_root,
            )
        self.assertEqual(surface["completeness"], "incomplete")
        self.assertEqual(surface["verification"], "unverified")
        projection = _restart_projection_for_result(
            mcp_surface=surface,
            mcp_affecting_change=True,
            running_fact=self._running(
                "sha256:running", 5100, "process:5100:old"
            ),
            client_context=normalize_client_context("codex-mcp-client"),
            prior_process={
                "process_id": 5100,
                "instance_id": "process:5100:old",
            },
        )
        self.assertEqual(
            projection["reason_code"], "affected_surface_unknown"
        )

    def test_mcp_verification_tool_persists_and_closes_restart_state(self) -> None:
        target = self.install_root / "mcp_server" / "server.py"
        target.write_text("# old tool-call fixture\n", encoding="utf-8")
        inventory = plan_workflow(
            source_root=REPO_ROOT,
            install_root=self.install_root,
            operation="update",
            client_home=self.client_home,
            clients=("codex",),
        )
        old_identity = next(
            item
            for item in inventory["surfaces"]
            if item["kind"] == "mcp-server-files"
        )["observed_content_identity"]
        apply_workflow(inventory)

        prior_client = server._client_info
        prior_running = server._RUNNING_SERVER_FACT
        server._client_info = {"name": "codex-mcp-client", "title": "Codex"}
        self.addCleanup(setattr, server, "_client_info", prior_client)
        self.addCleanup(setattr, server, "_RUNNING_SERVER_FACT", prior_running)
        server._RUNNING_SERVER_FACT = {
            "process_id": 7100,
            "instance_id": "process:7100:old",
            "loaded_content": {
                "mcp_identity": old_identity,
                "mcp_verification": "verified",
            },
            "state": "current",
            "authority": "connected-mcp-process",
        }
        response = server.call_tool(
            "verify_restart_state",
            {
                "install_root": str(self.install_root),
                "mcp_affecting_change": True,
            },
        )
        self.assertEqual(response["structuredContent"]["exit_code"], 0)
        first = response["structuredContent"]["records"][0]
        self.assertEqual(
            first["restart_state"]["status"], "restart_required"
        )

        current_identity = mcp_content_identity(self.install_root)["identity"]
        server._RUNNING_SERVER_FACT = {
            "process_id": 7200,
            "instance_id": "process:7200:new",
            "loaded_content": {
                "mcp_identity": current_identity,
                "mcp_verification": "verified",
            },
            "state": "current",
            "authority": "connected-mcp-process",
        }
        response = server.call_tool(
            "verify_restart_state",
            {"install_root": str(self.install_root)},
        )
        self.assertEqual(response["structuredContent"]["exit_code"], 0)
        second = response["structuredContent"]["records"][0]
        self.assertEqual(second["restart_state"]["status"], "current")
        self.assertTrue(
            second["restart_state"]["activation_claim_allowed"]
        )


class McpInstallContextRestartTests(unittest.TestCase):
    @staticmethod
    def _identities(installed_identity, running_identity, process_id=6100):
        return {
            "release_version": {"value": "v1", "state": "known"},
            "installed_content": {
                "revision": "revision-new",
                "materialization": "copy",
                "verification": "verified",
                "authority": "installed-or-materialized-content",
                "mcp_identity": installed_identity,
                "mcp_state": "verified",
                "mcp_verification": "verified",
            },
            "project_schema_version": {},
            "running_server": {
                "process_id": process_id,
                "instance_id": f"process:{process_id}",
                "loaded_content": {
                    "mcp_identity": running_identity,
                    "mcp_verification": "verified",
                },
                "state": (
                    "current"
                    if installed_identity == running_identity
                    else "stale-runtime"
                ),
                "authority": "connected-mcp-process",
            },
            "mcp_protocol_version": {},
        }

    def test_install_context_reports_one_codex_action_for_stale_server(self):
        identities = self._identities("sha256:new", "sha256:old")
        prior_client = server._client_info
        server._client_info = {"name": "codex-mcp-client", "title": "Codex"}
        self.addCleanup(setattr, server, "_client_info", prior_client)
        with (
            patch.object(server, "_identity_records", return_value=identities),
            patch.object(
                server,
                "_persisted_restart_baseline",
                return_value={
                    "process_id": 6100,
                    "instance_id": "process:6100",
                },
            ),
        ):
            restart = server._restart_projection()
            text = "\n".join(server._install_context_lines())
        self.assertEqual(restart["status"], "restart_required")
        self.assertEqual(text.count("Restart Codex."), 1)
        self.assertIn("Activation: `not claimed`", text)
        self.assertIn("Expected post-restart proof:", text)

    def test_install_context_claims_activation_only_for_fresh_matching_server(self):
        identities = self._identities(
            "sha256:new", "sha256:new", process_id=6200
        )
        prior_client = server._client_info
        server._client_info = {"name": "codex-mcp-client", "title": "Codex"}
        self.addCleanup(setattr, server, "_client_info", prior_client)
        with (
            patch.object(server, "_identity_records", return_value=identities),
            patch.object(
                server,
                "_persisted_restart_baseline",
                return_value={
                    "process_id": 6100,
                    "instance_id": "process:6100",
                },
            ),
        ):
            restart = server._restart_projection()
            text = "\n".join(server._install_context_lines())
        self.assertEqual(restart["status"], "current")
        self.assertTrue(restart["activation_claim_allowed"])
        self.assertIn("Activation: `active`", text)
        self.assertNotIn("Required action:", text)

    def test_initialize_instructions_have_one_upgrade_skill_line(self):
        lines = [
            line
            for line in server._server_instructions().splitlines()
            if line.startswith("- Upgrade skill")
        ]
        self.assertEqual(
            lines,
            [
                "- Upgrade skill (MCP prompt/resource): "
                "`check_for_updates` / "
                "`cartopian://skills/check_for_updates`"
            ],
        )


if __name__ == "__main__":
    unittest.main()
