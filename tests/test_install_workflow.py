"""Acceptance coverage for the coordinated install/update workflow."""
from __future__ import annotations

import json
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cli.main import main as cli_main
from cli.install_state import SURFACE_KINDS
from cli.install_workflow import (
    STATE_FILE,
    SUPPORTED_CLIENTS,
    WorkflowRefusal,
    apply_workflow,
    plan_workflow,
    verify_workflow,
)
from mcp_server import server

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads(
    (
        REPO_ROOT
        / "tests"
        / "fixtures"
        / "install_workflow"
        / "scenarios.json"
    ).read_text(encoding="utf-8")
)


class CoordinatedInstallWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install_root = self.root / "install"
        self.client_home = self.root / "home"
        self.client_home.mkdir()

    def plan(self, **overrides):
        values = {
            "source_root": REPO_ROOT,
            "install_root": self.install_root,
            "operation": "fresh-install",
            "client_home": self.client_home,
            "clients": ("codex",),
        }
        values.update(overrides)
        return plan_workflow(**values)

    def test_fresh_install_plans_every_surface_before_mutation(self) -> None:
        plan = self.plan()
        self.assertFalse(self.install_root.exists())
        self.assertEqual(
            [surface["kind"] for surface in plan["surfaces"]],
            list(SURFACE_KINDS),
        )
        self.assertEqual(plan["state"], "planned")
        self.assertEqual(
            [
                action["surface"]
                for action in plan["internal"]["affected_surface_plan"]
            ],
            list(SURFACE_KINDS),
        )
        affected = {
            item["kind"] for item in plan["surfaces"] if item["affected"]
        }
        self.assertTrue(
            set(SCENARIOS["fresh-install"]["expected_affected"]) <= affected
        )

    def test_apply_then_noop_update_is_byte_stable(self) -> None:
        result = apply_workflow(self.plan())
        self.assertIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        first_noop = apply_workflow(self.plan(operation="update"))
        self.assertIn(
            first_noop["outcome"]["status"],
            ("complete", "complete-qualified"),
        )
        before = {
            path.relative_to(self.install_root).as_posix(): path.read_bytes()
            for path in self.install_root.rglob("*")
            if path.is_file()
        }
        noop = self.plan(operation="update")
        required = [
            action
            for action in noop["internal"]["affected_surface_plan"]
            if action["authorization"] == "required"
            and action["action"] != "verify"
        ]
        self.assertEqual(
            required,
            SCENARIOS["no-op-update"]["expected_required_actions"],
        )
        apply_workflow(noop)
        after = {
            path.relative_to(self.install_root).as_posix(): path.read_bytes()
            for path in self.install_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_wrapper_bridge_and_registration_drift_are_all_planned(self) -> None:
        apply_workflow(self.plan())
        wrapper = self.install_root / "wrappers" / "bin" / "cartopian-codex"
        wrapper.write_text("drift\n", encoding="utf-8")
        bridge = self.client_home / ".codex" / "skills" / "use-cartopian"
        bridge.mkdir(parents=True, exist_ok=True)
        (bridge / "SKILL.md").write_text("custom\n", encoding="utf-8")
        config = self.client_home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            '[mcp_servers.cartopian]\ncommand = "/wrong/cartopian-mcp"\n',
            encoding="utf-8",
        )

        plan = self.plan(operation="update")
        affected = {
            item["kind"] for item in plan["surfaces"] if item["affected"]
        }
        self.assertTrue(
            set(SCENARIOS["multi-surface-update"]["drift"]) <= affected
        )
        offers = {
            item["surface"]: item["state"] for item in plan["choices"]
        }
        self.assertEqual(offers["bridges"], "offered")
        self.assertEqual(offers["client-registrations"], "offered")

    def test_accept_decline_and_defer_are_explicit_results(self) -> None:
        accepted = self.plan(
            decisions={"bridges": "accept"},
        )
        accepted_result = apply_workflow(accepted)
        self.assertTrue(
            (
                self.client_home
                / ".codex"
                / "skills"
                / "use-cartopian"
                / "SKILL.md"
            ).is_file()
        )
        accepted_choice = next(
            item
            for item in accepted_result["choices"]
            if item["surface"] == "bridges"
        )
        self.assertEqual(accepted_choice["state"], "authorized")

        declined_home = self.root / "declined-home"
        declined_home.mkdir()
        declined = self.plan(
            client_home=declined_home,
            decisions={
                "bridges": "decline",
                "client-registrations": "defer",
            }
        )
        choices = {item["surface"]: item["state"] for item in declined["choices"]}
        self.assertEqual(choices["bridges"], "declined")
        self.assertEqual(choices["client-registrations"], "deferred")
        result = apply_workflow(declined)
        self.assertFalse(result["outcome"]["fully_updated"])
        self.assertIn(
            result["outcome"]["status"], ("complete-qualified", "blocked")
        )

    def test_invalid_client_and_unsafe_install_destination_fail_closed(self) -> None:
        with self.assertRaises(WorkflowRefusal):
            self.plan(clients=("unsupported-client",))
        with self.assertRaises(WorkflowRefusal):
            self.plan(install_root=REPO_ROOT / "nested-install")

    def test_verification_failure_does_not_complete_checkpoint(self) -> None:
        result = apply_workflow(self.plan())
        target = self.install_root / "wrappers" / "bin" / "cartopian-codex"
        target.write_text("corrupt\n", encoding="utf-8")
        verified = verify_workflow(result)
        wrapper = next(
            item for item in verified["surfaces"] if item["kind"] == "wrappers"
        )
        checkpoint = next(
            item
            for item in verified["checkpoints"]
            if item["surface"] == "wrappers"
        )
        self.assertEqual(wrapper["state"], "failed")
        self.assertEqual(checkpoint["status"], "failed")
        self.assertEqual(verified["outcome"]["status"], "failed")

    def test_migration_is_offered_but_never_applied(self) -> None:
        project = self.root / "project"
        project.mkdir()
        (project / "cartopian.toml").write_text(
            "[project]\n"
            'id = "fixture"\n'
            'name = "Fixture"\n'
            'project_schema_version = "v0.1.0"\n',
            encoding="utf-8",
        )
        self.install_root.mkdir()
        (self.install_root / "projects.json").write_text(
            json.dumps([{"id": "fixture", "path": str(project)}]) + "\n",
            encoding="utf-8",
        )

        plan = self.plan(operation="update")
        self.assertEqual(len(plan["migrations"]), 1)
        offer = plan["migrations"][0]
        self.assertEqual(offer["choice_state"], "offered")
        self.assertEqual(
            offer["result"], SCENARIOS["migration-offer"]["migration_result"]
        )
        before = (project / "cartopian.toml").read_bytes()
        apply_workflow(plan)
        self.assertEqual((project / "cartopian.toml").read_bytes(), before)

    def test_authorized_registration_preserves_operator_owned_siblings(self) -> None:
        config = self.client_home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            'model = "operator-choice"\n\n'
            "[mcp_servers.other]\n"
            'command = "/operator/other"\n',
            encoding="utf-8",
        )
        apply_workflow(self.plan())
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                str(self.install_root / "bin" / "cartopian-mcp"),
                "/drifted/cartopian-mcp",
            ),
            encoding="utf-8",
        )
        result = apply_workflow(
            self.plan(
                operation="update",
                decisions={"client-registrations": "accept"},
            )
        )
        content = config.read_text(encoding="utf-8")
        self.assertIn('model = "operator-choice"', content)
        self.assertIn("[mcp_servers.other]", content)
        self.assertIn("[mcp_servers.cartopian]", content)
        paired = {
            item["surface"]: item["state"]
            for item in result["choices"]
            if item["surface"]
            in ("client-registrations", "client-configuration")
        }
        self.assertEqual(
            paired,
            {
                "client-registrations": "authorized",
                "client-configuration": "authorized",
            },
        )
        paired_states = {
            item["kind"]: item["state"]
            for item in result["surfaces"]
            if item["kind"]
            in ("client-registrations", "client-configuration")
        }
        self.assertEqual(
            paired_states,
            {
                "client-registrations": "verified",
                "client-configuration": "verified",
            },
        )
        self.assertNotIn(result["outcome"]["status"], ("blocked", "failed"))
        self.assertEqual(result["outcome"]["status"], "complete-qualified")

    def test_either_shared_adapter_surface_authorizes_both_results(self) -> None:
        for decision_surface in (
            "client-registrations",
            "client-configuration",
        ):
            with self.subTest(decision_surface=decision_surface):
                install_root = self.root / f"install-{decision_surface}"
                client_home = self.root / f"home-{decision_surface}"
                client_home.mkdir()
                apply_workflow(
                    self.plan(
                        install_root=install_root,
                        client_home=client_home,
                    )
                )
                config = client_home / ".codex" / "config.toml"
                content = config.read_text(encoding="utf-8")
                config.write_text(
                    content.replace(
                        str(install_root / "bin" / "cartopian-mcp"),
                        "/drifted/cartopian-mcp",
                    ),
                    encoding="utf-8",
                )
                result = apply_workflow(
                    self.plan(
                        install_root=install_root,
                        client_home=client_home,
                        operation="update",
                        decisions={decision_surface: "accept"},
                    )
                )
                choices = {
                    item["surface"]: item["state"]
                    for item in result["choices"]
                }
                self.assertEqual(
                    choices["client-registrations"], "authorized"
                )
                self.assertEqual(
                    choices["client-configuration"], "authorized"
                )
                self.assertNotIn(
                    result["outcome"]["status"], ("blocked", "failed")
                )

    def test_contradictory_shared_adapter_dispositions_are_refused(self) -> None:
        with self.assertRaisesRegex(
            WorkflowRefusal, "cannot receive contradictory dispositions"
        ):
            self.plan(
                decisions={
                    "client-registrations": "accept",
                    "client-configuration": "decline",
                }
            )

    def test_malformed_client_configuration_is_preserved_and_refused(self) -> None:
        config = self.client_home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text("[broken\n", encoding="utf-8")
        before = config.read_bytes()
        with self.assertRaises(WorkflowRefusal):
            apply_workflow(self.plan())
        self.assertEqual(config.read_bytes(), before)
        persisted = json.loads(
            (self.install_root / STATE_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["outcome"]["status"], "blocked")
        checkpoint = next(
            item
            for item in persisted["checkpoints"]
            if item["surface"] == "client-configuration"
        )
        self.assertEqual(checkpoint["status"], "blocked")
        self.assertEqual(
            checkpoint["attempted_action"], "reconfigure-registration"
        )
        self.assertEqual(
            checkpoint["retry_safety"], "inspect-before-retry"
        )
        self.assertTrue(checkpoint["recovery"])
        self.assertEqual(
            checkpoint["recovery_artifact"],
            "operator-client-configuration:preserved",
        )

    def test_update_refusal_replaces_stale_success_without_erasing_content(
        self,
    ) -> None:
        successful = apply_workflow(self.plan())
        self.assertNotIn(
            successful["outcome"]["status"], ("blocked", "failed")
        )
        installed_before = (
            self.install_root / "bin" / "cartopian"
        ).read_bytes()
        operator_file = self.install_root / "cartopian.toml"
        operator_file.write_text(
            '[workspace]\ndefault_pm_role = "operator-owned"\n',
            encoding="utf-8",
        )
        config = self.client_home / ".codex" / "config.toml"
        config.write_text("[broken\n", encoding="utf-8")

        with self.assertRaises(WorkflowRefusal):
            apply_workflow(
                self.plan(
                    operation="update",
                    decisions={"client-configuration": "accept"},
                )
            )

        latest = json.loads(
            (self.install_root / STATE_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(latest["run"]["operation"], "update")
        self.assertEqual(latest["outcome"]["status"], "blocked")
        self.assertIn(
            "client-configuration", latest["outcome"]["blocked_surfaces"]
        )
        self.assertTrue(latest["outcome"]["recovery_guidance"])
        self.assertEqual(
            (self.install_root / "bin" / "cartopian").read_bytes(),
            installed_before,
        )
        self.assertEqual(
            operator_file.read_text(encoding="utf-8"),
            '[workspace]\ndefault_pm_role = "operator-owned"\n',
        )
        self.assertEqual(config.read_text(encoding="utf-8"), "[broken\n")

    @unittest.skipIf(
        os.name == "nt",
        "POSIX directory permissions are required for this regression",
    )
    def test_unwritable_client_destination_replaces_stale_success_record(
        self,
    ) -> None:
        successful = apply_workflow(self.plan())
        stale = (self.install_root / STATE_FILE).read_bytes()
        self.assertNotIn(
            successful["outcome"]["status"], ("blocked", "failed")
        )
        config = self.client_home / ".codex" / "config.toml"
        config.write_text(
            '[mcp_servers.cartopian]\ncommand = "/drifted"\n',
            encoding="utf-8",
        )
        config_before = config.read_bytes()
        config_directory = config.parent
        config_directory.chmod(0o500)
        try:
            with self.assertRaises(PermissionError):
                apply_workflow(
                    self.plan(
                        operation="update",
                        decisions={"client-configuration": "accept"},
                    )
                )
        finally:
            config_directory.chmod(0o700)

        latest_path = self.install_root / STATE_FILE
        self.assertNotEqual(latest_path.read_bytes(), stale)
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        self.assertEqual(latest["run"]["operation"], "update")
        self.assertEqual(latest["state"], "failed")
        self.assertEqual(latest["outcome"]["status"], "failed")
        self.assertEqual(
            {
                item["kind"]: item["state"]
                for item in latest["surfaces"]
                if item["kind"]
                in ("client-registrations", "client-configuration")
            },
            {
                "client-registrations": "failed",
                "client-configuration": "failed",
            },
        )
        checkpoint = next(
            item
            for item in latest["checkpoints"]
            if item["surface"] == "client-configuration"
        )
        self.assertEqual(checkpoint["status"], "failed")
        self.assertEqual(
            checkpoint["attempted_action"], "reconfigure-registration"
        )
        self.assertEqual(checkpoint["mutation_status"], "os-error-preserved")
        self.assertTrue(checkpoint["recovery"])
        self.assertEqual(
            checkpoint["recovery_artifact"],
            "operator-client-configuration:preserved",
        )
        self.assertIn(
            "client-configuration", latest["outcome"]["blocked_surfaces"]
        )
        self.assertTrue(latest["outcome"]["recovery_guidance"])
        self.assertIn(
            "apply-failed",
            {item["code"] for item in latest["diagnostics"]},
        )
        self.assertEqual(config.read_bytes(), config_before)

    def test_secondary_state_write_failure_does_not_mask_apply_os_error(
        self,
    ) -> None:
        plan = self.plan()
        original = PermissionError("original apply failure")
        with (
            patch(
                "cli.install_workflow._apply_bridges",
                side_effect=original,
            ),
            patch(
                "cli.install_workflow._write_state",
                side_effect=OSError("secondary state failure"),
            ),
        ):
            with self.assertRaisesRegex(
                PermissionError, "original apply failure"
            ):
                apply_workflow(plan)

    @unittest.skipIf(
        os.name == "nt",
        "POSIX directory permissions are required for this regression",
    )
    def test_shipped_installer_bounds_unwritable_destination_failure(
        self,
    ) -> None:
        install_root = self.root / "unwritable-install"
        isolated_home = self.root / "unwritable-home"
        config_directory = isolated_home / ".codex"
        config_directory.mkdir(parents=True)
        config_directory.chmod(0o500)
        environment = dict(os.environ)
        environment["HOME"] = str(isolated_home)
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "install.py"),
                    "--source",
                    str(REPO_ROOT),
                    "--prefix",
                    str(install_root),
                    "--client",
                    "codex",
                    "--quiet",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
        finally:
            config_directory.chmod(0o700)

        self.assertEqual(completed.returncode, 1)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertIn(
            "coordinated install/update failed at an operating-system boundary",
            completed.stderr,
        )
        persisted = json.loads(
            (install_root / STATE_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["state"], "failed")
        self.assertEqual(persisted["outcome"]["status"], "failed")

    def test_unchanged_decline_carries_but_defer_and_context_changes_do_not(
        self,
    ) -> None:
        apply_workflow(self.plan())
        config = self.client_home / ".codex" / "config.toml"
        config.write_text(
            '[mcp_servers.cartopian]\ncommand = "/drifted"\n',
            encoding="utf-8",
        )
        declined = apply_workflow(
            self.plan(
                operation="update",
                decisions={"client-registrations": "decline"},
            )
        )
        self.assertEqual(
            {
                item["surface"]: item["state"]
                for item in declined["choices"]
            },
            {
                "client-registrations": "declined",
                "client-configuration": "declined",
            },
        )

        unchanged = self.plan(operation="update")
        self.assertEqual(
            {
                item["surface"]: (
                    item["state"],
                    item["provenance"],
                )
                for item in unchanged["choices"]
            },
            {
                "client-registrations": (
                    "declined",
                    "prior-run-matched-decline",
                ),
                "client-configuration": (
                    "declined",
                    "prior-run-matched-decline",
                ),
            },
        )

        deferred = apply_workflow(
            self.plan(
                operation="update",
                decisions={"client-configuration": "defer"},
            )
        )
        self.assertEqual(
            {item["state"] for item in deferred["choices"]}, {"deferred"}
        )
        reoffered = self.plan(operation="update")
        self.assertEqual(
            {item["state"] for item in reoffered["choices"]}, {"offered"}
        )

        apply_workflow(
            self.plan(
                operation="update",
                decisions={"client-configuration": "decline"},
            )
        )
        config.write_text(
            '[mcp_servers.cartopian]\ncommand = "/different-drift"\n',
            encoding="utf-8",
        )
        changed_observation = self.plan(operation="update")
        self.assertEqual(
            {item["state"] for item in changed_observation["choices"]},
            {"offered"},
        )
        changed_clients = self.plan(
            operation="update", clients=("codex", "gemini")
        )
        self.assertEqual(
            {item["state"] for item in changed_clients["choices"]},
            {"offered"},
        )
        config.write_text(
            '[mcp_servers.cartopian]\ncommand = "/different-drift"\n',
            encoding="utf-8",
        )
        apply_workflow(
            self.plan(
                operation="update",
                decisions={"client-configuration": "decline"},
            )
        )
        with patch(
            "cli.install_workflow._source_identity",
            return_value="sha256:changed-maintainer-source",
        ):
            changed_source = self.plan(operation="update")
        self.assertEqual(
            {item["state"] for item in changed_source["choices"]},
            {"offered"},
        )

    def test_decline_carries_while_another_repair_offer_remains_open(
        self,
    ) -> None:
        apply_workflow(self.plan())
        bridge = (
            self.client_home
            / ".codex"
            / "skills"
            / "use-cartopian"
            / "SKILL.md"
        )
        bridge.write_text("bridge drift\n", encoding="utf-8")
        config = self.client_home / ".codex" / "config.toml"
        config.write_text(
            '[mcp_servers.cartopian]\ncommand = "/registration-drift"\n',
            encoding="utf-8",
        )

        mixed = apply_workflow(
            self.plan(
                operation="update",
                decisions={"bridges": "decline"},
            )
        )
        self.assertEqual(mixed["state"], "repair-offered")
        self.assertEqual(mixed["outcome"]["status"], "in-progress")
        self.assertEqual(
            {
                item["surface"]: item["state"]
                for item in mixed["choices"]
            },
            {
                "bridges": "declined",
                "client-registrations": "offered",
                "client-configuration": "offered",
            },
        )

        resumed = self.plan(operation="update")
        self.assertEqual(
            {
                item["surface"]: (
                    item["state"],
                    item["provenance"],
                )
                for item in resumed["choices"]
            },
            {
                "bridges": (
                    "declined",
                    "prior-run-matched-decline",
                ),
                "client-registrations": (
                    "offered",
                    "coordinated-workflow-detection",
                ),
                "client-configuration": (
                    "offered",
                    "coordinated-workflow-detection",
                ),
            },
        )

    def test_malformed_prior_state_is_ignored_for_decline_carry_forward(
        self,
    ) -> None:
        apply_workflow(self.plan())
        config = self.client_home / ".codex" / "config.toml"
        config.write_text(
            '[mcp_servers.cartopian]\ncommand = "/drifted"\n',
            encoding="utf-8",
        )
        (self.install_root / STATE_FILE).write_text(
            "{not-json\n", encoding="utf-8"
        )
        planned = self.plan(operation="update")
        self.assertEqual(
            {item["state"] for item in planned["choices"]}, {"offered"}
        )

    def test_symlinked_client_configuration_is_preserved_and_refused(self) -> None:
        actual = self.root / "operator-config.toml"
        actual.write_text('model = "owned"\n', encoding="utf-8")
        config = self.client_home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.symlink_to(actual)
        with self.assertRaises(WorkflowRefusal):
            apply_workflow(self.plan())
        self.assertTrue(config.is_symlink())
        self.assertEqual(actual.read_text(encoding="utf-8"), 'model = "owned"\n')

    def test_shipped_installer_converts_legacy_symlinked_surface_to_copy(
        self,
    ) -> None:
        install_root = self.root / "mode-install"
        isolated_home = self.root / "mode-home"
        isolated_home.mkdir()
        environment = dict(os.environ)
        environment["HOME"] = str(isolated_home)
        command = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "install.py"),
            "--source",
            str(REPO_ROOT),
            "--prefix",
            str(install_root),
            "--quiet",
        ]
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertFalse((install_root / "protocol").is_symlink())

        # A legacy symlink-mode install left a linked surface behind; an
        # update must replace it with a real copy.
        shutil.rmtree(install_root / "protocol")
        (install_root / "protocol").symlink_to(
            REPO_ROOT / "protocol", target_is_directory=True
        )
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertFalse((install_root / "protocol").is_symlink())
        self.assertTrue((install_root / "protocol").is_dir())

    def test_persisted_evidence_is_stable_and_portable(self) -> None:
        apply_workflow(self.plan(clients=()))
        persisted = json.loads(
            (self.install_root / STATE_FILE).read_text(encoding="utf-8")
        )
        self.assertNotIn("internal", persisted)
        serialized = json.dumps(persisted)
        for forbidden in (
            "task_id",
            "phase_id",
            "plan_ref",
            "prompt_id",
            "executable_path",
            "destination_path",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_cli_and_mcp_emit_the_same_plan_and_vocabulary(self) -> None:
        cli_install = self.root / "cli-install"
        arguments = [
            "install-workflow",
            str(REPO_ROOT),
            str(cli_install),
            "--operation",
            "fresh-install",
            "--client",
            "codex",
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli_main(arguments)
        self.assertEqual(exit_code, 0)
        cli_record = json.loads(output.getvalue())

        response = server.handle_request(
            "tools/call",
            {
                "name": "install_workflow",
                "arguments": {
                    "source_root": str(REPO_ROOT),
                    "install_root": str(cli_install),
                    "operation": "fresh-install",
                    "client": ["codex"],
                },
            },
        )
        self.assertEqual(response["structuredContent"]["exit_code"], 0)
        mcp_record = response["structuredContent"]["records"][0]
        self.assertEqual(cli_record, mcp_record)
        self.assertEqual(
            [item["surface"] for item in cli_record["affected_surface_plan"]],
            list(SURFACE_KINDS),
        )

    def test_documentation_uses_closed_surface_and_client_vocabularies(self) -> None:
        state_doc = (
            REPO_ROOT / "protocol" / "INSTALL_UPDATE_STATE.md"
        ).read_text(encoding="utf-8")
        registration_doc = (
            REPO_ROOT / "skills" / "register-mcp.md"
        ).read_text(encoding="utf-8").casefold()
        for surface in SURFACE_KINDS:
            self.assertIn(surface, state_doc)
        for client in SUPPORTED_CLIENTS:
            display = client.replace("-", " ")
            self.assertIn(display, registration_doc)
        verification = (
            REPO_ROOT / "protocol" / "INSTALL_VERIFICATION.md"
        ).read_text(encoding="utf-8")
        self.assertNotRegex(
            verification,
            r"\b(?:FR|DEC|TASK|SPEC|PHASE|PROMPT|PLAN)-[0-9]",
        )
        self.assertRegex(
            verification,
            r"not be described\s+as native execution proof",
        )


class OpencodeCoordinatedWorkflowTests(unittest.TestCase):
    """opencode registration + bridge through the full coordinated workflow,
    plus the D9 guarantee: destinations resolve once, at plan time, and an
    environment flip between authorization and apply refuses instead of
    routing an authorized write to a destination the plan never displayed."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.install_root = self.root / "install"
        self.client_home = self.root / "home"
        self.client_home.mkdir()
        # opencode's resolvers are environment-driven; pin the environment so
        # ambient operator settings can neither leak in nor be written to.
        env_patch = patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        for name in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "XDG_CONFIG_HOME"):
            os.environ.pop(name, None)

    def plan(self, **overrides):
        values = {
            "source_root": REPO_ROOT,
            "install_root": self.install_root,
            "operation": "fresh-install",
            "client_home": self.client_home,
            "clients": ("opencode",),
        }
        values.update(overrides)
        return plan_workflow(**values)

    def _config_pair(self):
        base = self.client_home / ".config" / "opencode"
        return base / "opencode.json", base / "opencode.jsonc"

    def _bridge_path(self):
        return (
            self.client_home
            / ".config"
            / "opencode"
            / "commands"
            / "use-cartopian.md"
        )

    def test_fresh_install_registers_and_bridges_then_replans_current(self) -> None:
        result = apply_workflow(self.plan())
        self.assertIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        json_path, jsonc_path = self._config_pair()
        self.assertTrue(json_path.is_file())
        self.assertFalse(jsonc_path.exists())
        data = json.loads(json_path.read_text(encoding="utf-8"))
        entry = data["mcp"]["cartopian"]
        self.assertEqual(entry["type"], "local")
        self.assertEqual(
            entry["command"],
            [str(self.install_root / "bin" / "cartopian-mcp")],
        )
        self.assertEqual(entry["enabled"], True)
        self.assertEqual(entry["timeout"], 600000)
        bridge = self._bridge_path()
        self.assertTrue(bridge.is_file())
        self.assertEqual(
            bridge.read_bytes(),
            (
                REPO_ROOT
                / "templates"
                / "clients"
                / "opencode"
                / "commands"
                / "use-cartopian.md"
            ).read_bytes(),
        )

        replanned = self.plan(operation="update")
        registration = next(
            item
            for item in replanned["surfaces"]
            if item["kind"] == "client-registrations"
        )
        bridges = next(
            item for item in replanned["surfaces"] if item["kind"] == "bridges"
        )
        self.assertEqual(registration["state"], "current")
        self.assertEqual(bridges["state"], "current")

    def test_nonstrict_jsonc_blocks_and_preserves_through_the_workflow(self) -> None:
        json_path, jsonc_path = self._config_pair()
        jsonc_path.parent.mkdir(parents=True)
        jsonc_path.write_text(
            '{\n  // operator comment\n  "theme": "dark",\n}\n', encoding="utf-8"
        )
        before = jsonc_path.read_bytes()
        with self.assertRaisesRegex(WorkflowRefusal, "malformed and was preserved"):
            apply_workflow(self.plan())
        self.assertEqual(jsonc_path.read_bytes(), before)
        self.assertFalse(json_path.exists())

    def test_plan_records_the_resolved_destinations(self) -> None:
        plan = self.plan()
        destinations = plan["internal"]["client_destinations"]["opencode"]
        json_path, jsonc_path = self._config_pair()
        self.assertEqual(
            destinations["registration"], [str(json_path), str(jsonc_path)]
        )
        self.assertEqual(destinations["bridges"], [str(self._bridge_path())])
        registration_choice = next(
            item
            for item in plan["choices"]
            if item["surface"] == "client-registrations"
        )
        self.assertEqual(
            registration_choice["decision_context"]["destinations"]["opencode"],
            destinations,
        )

    def test_environment_flip_between_plan_and_apply_is_refused(self) -> None:
        """Flipping any destination-resolving variable after authorization must
        refuse; no destination the plan did not display may be written."""
        flips = {
            "OPENCODE_CONFIG": str(self.root / "flipped" / "explicit.json"),
            "OPENCODE_CONFIG_DIR": str(self.root / "flipped-dir"),
            "XDG_CONFIG_HOME": str(self.root / "flipped-xdg"),
        }
        for variable, value in flips.items():
            with self.subTest(variable=variable):
                install_root = self.root / f"install-{variable}"
                plan = self.plan(install_root=install_root)
                os.environ[variable] = value
                try:
                    with self.assertRaisesRegex(
                        WorkflowRefusal,
                        "changed between planning and apply",
                    ):
                        apply_workflow(plan)
                finally:
                    os.environ.pop(variable, None)
                flipped = Path(value)
                self.assertFalse(
                    flipped.exists(),
                    msg=f"{variable}: a destination the plan never displayed "
                    "was written",
                )

    def test_unflipped_environment_applies_to_the_planned_destinations(self) -> None:
        """The D9 guard does not disturb an ordinary plan/apply run under a
        redirected-but-stable environment."""
        override = self.root / "opencode-dir"
        os.environ["OPENCODE_CONFIG_DIR"] = override.as_posix()
        try:
            result = apply_workflow(self.plan())
        finally:
            os.environ.pop("OPENCODE_CONFIG_DIR", None)
        self.assertIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        data = json.loads(
            (override / "opencode.json").read_text(encoding="utf-8")
        )
        self.assertIn("cartopian", data["mcp"])
        self.assertTrue((override / "commands" / "use-cartopian.md").is_file())


_HERMES_STUB_TEMPLATE = '''#!/usr/bin/env python3
"""Stateful stub `hermes` for coordinated-workflow tests: `config set` writes
land in a JSON state file that `config get --json` answers from, so a written
registration reads back `current` on re-plan exactly like the real CLI."""
import json
import sys
from pathlib import Path

CTRL = Path(__CTRL__)
args = sys.argv[1:]
# The registration adapter pins reads and writes to the resolved profile
# identity: a root-like home arrives as a leading `-p default`, exactly like
# the real CLI's pre-parse it is stripped before subcommand dispatch.
if args[:1] == ["-p"]:
    args = args[2:]
if args[:1] == ["--version"]:
    print((CTRL / "version").read_text().strip())
    sys.exit(0)
if args[:2] == ["config", "path"]:
    print((CTRL / "config_path").read_text().strip())
    sys.exit(0)
state = CTRL / "entry.json"
if args[:2] == ["config", "get"]:
    if not state.exists():
        print("Config key not set", file=sys.stderr)
        sys.exit(1)
    print(state.read_text())
    sys.exit(0)
if args[:2] == ["config", "set"]:
    key, value = args[2], args[3]
    parts = key.split(".")
    entry = json.loads(state.read_text()) if state.exists() else {}
    node = entry
    for part in parts[2:-1]:
        node = node.setdefault(part, {})
    if value in ("true", "false"):
        coerced = value == "true"
    else:
        try:
            coerced = int(value)
        except ValueError:
            coerced = value
    node[parts[-1]] = coerced
    state.write_text(json.dumps(entry))
    sys.exit(0)
if args[:2] == ["config", "unset"]:
    state.unlink(missing_ok=True)
    sys.exit(0)
sys.exit(0)
'''


class HermesCoordinatedWorkflowTests(unittest.TestCase):
    """Hermes registration + skill bridge through the full coordinated
    workflow, against a stateful stub `hermes` on a restricted PATH. Pins the
    D10 guarantee: destinations, executable, and version freeze at plan time,
    and a profile/config-path change between authorization and apply refuses
    instead of driving a toolchain the plan never displayed."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.install_root = self.root / "install"
        self.client_home = self.root / "home"
        self.client_home.mkdir()
        self.profile_home = self.root / "hermes-profile"
        self.profile_home.mkdir()
        ctrl = self.root / "hermes-ctrl"
        ctrl.mkdir()
        self.ctrl = ctrl
        (ctrl / "version").write_text("hermes 0.20.0 (2026.8.3)", encoding="utf-8")
        (ctrl / "config_path").write_text(
            str(self.profile_home / "config.yaml"), encoding="utf-8"
        )
        bin_dir = self.root / "hermesbin"
        bin_dir.mkdir()
        stub = bin_dir / "hermes"
        stub.write_text(
            _HERMES_STUB_TEMPLATE.replace("__CTRL__", repr(str(ctrl))),
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env_patch = patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ["PATH"] = os.pathsep.join(
            [str(bin_dir), "/usr/bin", "/bin"]
        )
        os.environ.pop("HERMES_HOME", None)

    def plan(self, **overrides):
        values = {
            "source_root": REPO_ROOT,
            "install_root": self.install_root,
            "operation": "fresh-install",
            "client_home": self.client_home,
            "clients": ("hermes",),
        }
        values.update(overrides)
        return plan_workflow(**values)

    def _entry(self):
        state = self.ctrl / "entry.json"
        return json.loads(state.read_text(encoding="utf-8")) if state.exists() else None

    def _bundle(self):
        return self.profile_home / "skills" / "cartopian"

    def test_fresh_install_registers_and_bridges_then_replans_current(self) -> None:
        result = apply_workflow(self.plan())
        self.assertIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        entry = self._entry()
        self.assertEqual(
            entry["command"], str(self.install_root / "bin" / "cartopian-mcp")
        )
        self.assertEqual(entry["timeout"], 3900)
        self.assertEqual(entry["enabled"], True)
        self.assertEqual(entry["env"]["CARTOPIAN_MCP_HOST"], "hermes")
        self.assertEqual(
            entry["env"]["CARTOPIAN_HERMES_HOME"], str(self.profile_home)
        )
        skill = self._bundle() / "use-cartopian" / "SKILL.md"
        description = self._bundle() / "DESCRIPTION.md"
        self.assertTrue(skill.is_file())
        self.assertTrue(description.is_file())
        self.assertEqual(
            skill.read_bytes(),
            (
                REPO_ROOT
                / "templates"
                / "clients"
                / "hermes"
                / "skills"
                / "use-cartopian"
                / "SKILL.md"
            ).read_bytes(),
        )

        replanned = self.plan(operation="update")
        registration = next(
            item
            for item in replanned["surfaces"]
            if item["kind"] == "client-registrations"
        )
        bridges = next(
            item for item in replanned["surfaces"] if item["kind"] == "bridges"
        )
        self.assertEqual(registration["state"], "current")
        self.assertEqual(bridges["state"], "current")

    def test_plan_records_destinations_executable_and_version(self) -> None:
        plan = self.plan()
        destinations = plan["internal"]["client_destinations"]["hermes"]
        self.assertEqual(
            destinations["registration"],
            [str(self.profile_home / "config.yaml")],
        )
        self.assertEqual(
            destinations["bridges"],
            [
                str(self._bundle() / "DESCRIPTION.md"),
                str(self._bundle() / "use-cartopian" / "SKILL.md"),
            ],
        )
        self.assertEqual(
            destinations["executable"],
            [str((self.root / "hermesbin" / "hermes").resolve())],
        )
        self.assertEqual(destinations["version"], ["hermes 0.20.0 (2026.8.3)"])
        registration_choice = next(
            item
            for item in plan["choices"]
            if item["surface"] == "client-registrations"
        )
        self.assertEqual(
            registration_choice["decision_context"]["destinations"]["hermes"],
            destinations,
        )

    def test_profile_flip_between_plan_and_apply_is_refused(self) -> None:
        """A different `hermes config path` answer at apply (profile switch,
        HERMES_HOME change) must refuse; no destination the plan never
        displayed may be written."""
        plan = self.plan()
        flipped = self.root / "flipped-profile"
        flipped.mkdir()
        (self.ctrl / "config_path").write_text(
            str(flipped / "config.yaml"), encoding="utf-8"
        )
        with self.assertRaisesRegex(
            WorkflowRefusal, "changed between planning and apply"
        ):
            apply_workflow(plan)
        self.assertIsNone(self._entry())
        self.assertFalse((flipped / "skills").exists())

    def test_hermes_version_flip_between_plan_and_apply_is_refused(self) -> None:
        """The frozen-fact refusal must fire before EITHER optional surface
        mutates: bridges apply before registrations, so a registration-only
        check would leave the skill bundle installed after the refusal."""
        plan = self.plan()
        (self.ctrl / "version").write_text("hermes 0.21.0", encoding="utf-8")
        with self.assertRaisesRegex(WorkflowRefusal, "version changed"):
            apply_workflow(plan)
        self.assertIsNone(self._entry())
        self.assertFalse(
            self._bundle().exists(),
            "the skill bridge was written before the version-change refusal",
        )

    def test_hermes_config_path_failure_at_plan_time_refuses(self) -> None:
        """A runnable `hermes` that cannot report its config location must
        refuse the plan instead of freezing a guessed profile home."""
        stub = self.root / "hermesbin" / "hermes"
        stub.write_text(
            "#!/bin/sh\n"
            'if [ "$1 $2" = "config path" ]; then\n'
            '  echo "boom" >&2; exit 3\n'
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        with self.assertRaisesRegex(
            WorkflowRefusal, "cannot report its profile-scoped config"
        ):
            self.plan()

    def test_stable_environment_applies_to_the_planned_destinations(self) -> None:
        """The D10 guard does not disturb an ordinary plan/apply run."""
        result = apply_workflow(self.plan())
        self.assertIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertIsNotNone(self._entry())


if __name__ == "__main__":
    unittest.main()
