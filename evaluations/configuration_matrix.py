"""Deterministic end-to-end configuration compatibility and safety matrix."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any
from unittest import mock

from cli import config_migration, protocol_gate, version_identities
from cli.capabilities import resolve_grants
from cli.commands import dispatch
from cli.config_schema import (
    SCOPES,
    ConfigDiagnostic,
    resolve_configuration,
    validate_authored_config,
)
from cli.config_surface_parity import (
    check_surface_registry,
    guidance_hygiene,
    load_registry,
    registry_inventory_evidence,
)
from cli.deidentify import deidentify_spec, list_identifiers
from mcp_server import server

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = Path(__file__).with_name("configuration-matrix.json")
FIXTURES = ROOT / "tests" / "fixtures" / "config_migration"
ENTRYPOINT = ROOT / "bin" / "cartopian"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree_identity(root: Path) -> str:
    """Hash every fixture node by relative path, kind, and content/target."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            kind = b"link"
            payload = os.readlink(path).encode("utf-8")
        elif path.is_dir():
            kind = b"directory"
            payload = b""
        else:
            kind = b"file"
            payload = path.read_bytes()
        for value in (kind, relative, payload):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.hexdigest()


def _seed(root: Path, fixture_name: str) -> tuple[Path, Path]:
    home = root / "home"
    project = root / "project"
    cartopian_home = home / ".cartopian"
    cartopian_home.mkdir(parents=True)
    project.mkdir()
    fixture = FIXTURES / fixture_name
    for source, target in (
        (fixture / "global.toml", cartopian_home / "cartopian.toml"),
        (fixture / "project.toml", project / "cartopian.toml"),
        (fixture / "local.toml", project / "cartopian.local.toml"),
    ):
        if source.exists():
            shutil.copyfile(source, target)
    project_config = tomllib.loads(
        (project / "cartopian.toml").read_text(encoding="utf-8")
    )
    project_id = project_config.get("project", {}).get("id", "matrix-project")
    (cartopian_home / "projects.json").write_text(
        json.dumps([{"id": project_id, "path": str(project)}]) + "\n",
        encoding="utf-8",
    )
    return home, project


def _identities(home: Path, project: Path) -> dict[str, str | None]:
    paths = {
        "global": home / ".cartopian" / "cartopian.toml",
        "project": project / "cartopian.toml",
        "machine-local": project / "cartopian.local.toml",
    }
    return {
        scope: _sha256(path.read_bytes()) if path.is_file() else None
        for scope, path in paths.items()
    }


def _migration(case: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="cartopian-config-matrix-") as raw:
        home, project = _seed(Path(raw), case["fixture"])
        before = _identities(home, project)
        probes: list[str] = []
        plan = config_migration.plan_configuration_migration(
            project, home_root=home
        )
        probes.append("plan_configuration_migration")
        diagnostics = list(plan.diagnostics)
        if plan.status in {"refused", "pending"}:
            after = _identities(home, project)
            observed = plan.status
            invariants = {
                "input_hashes_unchanged": before == after,
                "no_partial_canonicalization": before == after,
                "diagnostics_stably_ordered": diagnostics
                == sorted(
                    diagnostics,
                    key=lambda item: (
                        item.get("scope", ""),
                        item.get("field", ""),
                        item.get("code", ""),
                    ),
                ),
            }
            rerun = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            probes.append("plan_configuration_migration")
            rerun_comparison = {
                "diagnostics_equal": rerun.diagnostics == plan.diagnostics,
                "identities_equal": _identities(home, project) == before,
            }
        else:
            result = config_migration.execute_configuration_migration(
                project, plan, home_root=home
            )
            probes.append("execute_configuration_migration")
            after = _identities(home, project)
            rerun_plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            probes.append("plan_configuration_migration")
            rerun_before = _identities(home, project)
            rerun_result = config_migration.execute_configuration_migration(
                project, rerun_plan, home_root=home
            )
            probes.append("execute_configuration_migration")
            observed = result["status"]
            marker_steps = [step.kind for step in plan.steps]
            invariants = {
                "semantic_equivalence": plan.equivalence["status"] == "passed",
                "marker_advanced_last": (
                    not marker_steps or marker_steps[-1] == "update-marker"
                ),
                "canonical_rerun": rerun_plan.status == "noop",
                "operator_bytes_stable_on_rerun": _identities(home, project)
                == rerun_before,
            }
            rerun_comparison = {
                "plan_status": rerun_plan.status,
                "result_status": rerun_result["status"],
                "identities_equal": _identities(home, project) == rerun_before,
            }
        expected_classifications = tuple(case.get("expected_classifications", ()))
        observed_classifications = tuple(
            item.get("classification") for item in diagnostics
        )
        invariants.update(
            {
                "planner_status_exact": plan.status
                == case["expected_plan_status"],
                "diagnostic_classification_exact": observed_classifications
                == expected_classifications,
            }
        )
        return {
            "observed": observed,
            "probes": probes,
            "invariants": invariants,
            "filesystem_identities": {"before": before, "after": after},
            "diagnostics": diagnostics,
            "rerun_comparison": {
                **rerun_comparison,
                "planner_status": plan.status,
                "compatibility_state": plan.compatibility_state,
                "diagnostic_classifications": observed_classifications,
            },
        }


def _interruption(case: dict[str, Any]) -> dict[str, Any]:
    boundaries: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="cartopian-config-reference-") as clean_raw:
        clean_home, clean_project = _seed(Path(clean_raw), case["fixture"])
        clean_plan = config_migration.plan_configuration_migration(
            clean_project, home_root=clean_home
        )
        config_migration.execute_configuration_migration(
            clean_project, clean_plan, home_root=clean_home
        )
        clean_identities = _identities(clean_home, clean_project)
        step_ids = [step.step_id for step in clean_plan.steps]
    for step_id in step_ids:
        with tempfile.TemporaryDirectory(
            prefix="cartopian-config-interruption-"
        ) as raw:
            home, project = _seed(Path(raw), case["fixture"])
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            interrupted = False
            try:
                config_migration.execute_configuration_migration(
                    project,
                    plan,
                    home_root=home,
                    interrupt_after_step=step_id,
                )
            except config_migration.MigrationInterrupted:
                interrupted = True
            checkpoint = project / ".cartopian" / "config-migration.json"
            evidence = json.loads(checkpoint.read_text(encoding="utf-8"))
            resumed = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            config_migration.execute_configuration_migration(
                project, resumed, home_root=home
            )
            boundaries.append(
                {
                    "step": step_id,
                    "interrupted": interrupted,
                    "checkpoint_status": evidence["status"],
                    "evidenced_steps": [
                        item["id"] for item in evidence["completed_steps"]
                    ],
                    "converged": _identities(home, project) == clean_identities,
                    "checkpoint_removed": not checkpoint.exists(),
                    "rerun_status": config_migration.plan_configuration_migration(
                        project, home_root=home
                    ).status,
                }
            )
    return {
        "observed": "complete",
        "probes": ["every stateful migration boundary"],
        "invariants": {
            "all_interruptions_evidenced": all(
                item["interrupted"] and item["checkpoint_status"] == "in-progress"
                for item in boundaries
            ),
            "all_resumptions_converged": all(
                item["converged"] and item["checkpoint_removed"]
                for item in boundaries
            ),
            "all_reruns_noop": all(
                item["rerun_status"] == "noop" for item in boundaries
            ),
        },
        "filesystem_identities": {"canonical": clean_identities},
        "diagnostics": [],
        "rerun_comparison": {"boundaries": boundaries},
    }


def _surface_parity(_case: dict[str, Any]) -> dict[str, Any]:
    diagnostics = [item.as_record() for item in check_surface_registry(ROOT)]
    inventory_evidence = registry_inventory_evidence(
        load_registry(ROOT / "config-surfaces.json")
    )
    return {
        "observed": "complete" if not diagnostics else "failed",
        "probes": ["check_surface_registry"],
        "invariants": {
            "registry_is_inventory_not_authority": inventory_evidence[
                "inventory_only"
            ],
            "all_declared_parity_checks_pass": not diagnostics,
        },
        "filesystem_identities": {
            "registry": _sha256((ROOT / "config-surfaces.json").read_bytes())
        },
        "diagnostics": diagnostics,
        "rerun_comparison": {
            "diagnostics_equal": diagnostics
            == [item.as_record() for item in check_surface_registry(ROOT)],
            "inventory_evidence": inventory_evidence,
        },
    }


def _launch_separation(_case: dict[str, Any]) -> dict[str, Any]:
    shipped = protocol_gate.read_shipped_project_schema_version()
    with tempfile.TemporaryDirectory(prefix="cartopian-launch-separation-") as raw:
        fixture = Path(raw)
        home = fixture / "home"
        home.mkdir()

        def seed(name: str, auto_launch: bool) -> tuple[Path, Path, Path]:
            project = fixture / name
            for relative in (
                "phases",
                "tasks/open",
                "tasks/in-review",
                "prompts",
                "reports",
            ):
                (project / relative).mkdir(parents=True, exist_ok=True)
            activities = 'auto_launch = ["task_run"]\n' if auto_launch else ""
            (project / "cartopian.toml").write_text(
                "[project]\n"
                f'id = "{name}"\n'
                f'name = "{name}"\n'
                f'project_schema_version = "{shipped}"\n\n'
                "[roles.coder]\n"
                'description = "Implements assigned work."\n'
                f"{activities}\n"
                "[roles.coder.launch]\n"
                'target = "cartopian-matrix-agent"\n\n'
                "[reviews]\n"
                'planning = "off"\n'
                'task_closure = "off"\n\n'
                "[automation]\n"
                'initiation = "operator"\n',
                encoding="utf-8",
            )
            (project / "phases" / "PHASE-01-build.md").write_text(
                "# PHASE-01: Build\n", encoding="utf-8"
            )
            task = project / "tasks" / "open" / "TASK-01-001-build.md"
            task.write_text(
                "# TASK-01-001: Build\n\n"
                "Phase: PHASE-01-build\n"
                "Work root: n/a\n"
                "Assignee: coder\n"
                "Blocked by: n/a\n\n"
                "## Goal\n\nBuild.\n",
                encoding="utf-8",
            )
            review_task = (
                project / "tasks" / "in-review" / "TASK-01-002-review.md"
            )
            review_task.write_text(
                "# TASK-01-002: Review\n\n"
                "Phase: PHASE-01-build\n"
                "Work root: n/a\n"
                "Assignee: coder\n\n"
                "## Goal\n\nReview.\n",
                encoding="utf-8",
            )
            for task_id in ("01-001", "01-002"):
                (project / "prompts" / f"PROMPT-{task_id}.md").write_text(
                    f"# PROMPT-{task_id}\n\n## Your task\n\nProbe.\n",
                    encoding="utf-8",
                )
            return project, task, review_task

        permitted_project, permitted_task, review_task = seed("permitted", True)
        denied_project, denied_task, _ = seed("denied", False)

        selection_records = {}
        for label, project in (
            ("with_permission", permitted_project),
            ("without_permission", denied_project),
        ):
            selection = _run_cli(home, "next-action", str(project))
            selection_records[label] = json.loads(selection.stdout)

        class _FakeProcess:
            pid = 17

        def call_dispatch(task: Path) -> tuple[int, bool, str]:
            args = argparse.Namespace(task_path=str(task), prompt=None, role="coder")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch("cli.commands.dispatch.Path.home", return_value=home),
                mock.patch(
                    "cli.commands.dispatch.shutil.which",
                    return_value="/fixture/cartopian-matrix-agent",
                ),
                mock.patch(
                    "cli.commands.dispatch.subprocess.Popen",
                    return_value=_FakeProcess(),
                ) as popen,
                mock.patch(
                    "cli.commands.dispatch._open_launch_log",
                    return_value=None,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                return dispatch.handler(args), popen.called, stderr.getvalue()

        allowed_rc, allowed_launch, allowed_stderr = call_dispatch(permitted_task)
        denied_rc, denied_launch, denied_stderr = call_dispatch(denied_task)
        review_rc, review_launch, review_stderr = call_dispatch(review_task)

        stop_suite = (
            "tests.test_initiation_intent_static."
            "ConventionsIntentClassificationTest."
            "test_stop_language_overrides_configuration"
        )
        delegated = subprocess.run(
            [sys.executable, "-m", "unittest", "-q", stop_suite],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        resolved = resolve_configuration(
            {},
            tomllib.loads(
                (permitted_project / "cartopian.toml").read_text(encoding="utf-8")
            ),
            {},
        )
        resolved_without_permission = resolve_configuration(
            {},
            tomllib.loads(
                (denied_project / "cartopian.toml").read_text(encoding="utf-8")
            ),
            {},
        )
        role = resolved["roles"]["coder"]
        selected_with = selection_records["with_permission"]["next_open_task"]
        selected_without = selection_records["without_permission"]["next_open_task"]
        invariants = {
            "permission_does_not_enable_review": all(
                item["mode"] == "off" for item in resolved["reviews"].values()
            ),
            "permission_does_not_assign_review_role": all(
                item["role"] is None for item in resolved["reviews"].values()
            ),
            "permission_does_not_initiate_run": (
                selection_records["with_permission"]["automation"]["initiation"]
                == "operator"
            ),
            "selection_is_permission_independent": (
                selected_with["id"] == selected_without["id"] == "TASK-01-001"
            ),
            "permission_gates_real_dispatch": (
                allowed_rc == 0
                and allowed_launch
                and denied_rc != 0
                and not denied_launch
                and "automatic task_run dispatch is not enabled" in denied_stderr
            ),
            "task_permission_does_not_authorize_review_dispatch": (
                review_rc != 0
                and not review_launch
                and "automatic task_review dispatch is not enabled" in review_stderr
            ),
            "permission_does_not_grant_capabilities": (
                role["effective_grants"]
                == resolved_without_permission["roles"]["coder"][
                    "effective_grants"
                ]
            ),
            "delegated_operator_stop_contract_passes": delegated.returncode == 0,
        }
        return {
            "observed": "complete" if all(invariants.values()) else "failed",
            "probes": [
                "next-action CLI with task_run permission",
                "next-action CLI without task_run permission",
                "dispatch.handler task_run permitted",
                "dispatch.handler task_run denied",
                "dispatch.handler task_review denied by task_run-only permission",
                stop_suite,
            ],
            "invariants": invariants,
            "filesystem_identities": {},
            "diagnostics": (
                []
                if all(invariants.values())
                else [
                    {
                        "allowed_stderr": allowed_stderr,
                        "denied_stderr": denied_stderr,
                        "review_stderr": review_stderr,
                        "delegated_stderr": delegated.stderr,
                    }
                ]
            ),
            "rerun_comparison": {
                "selected_task_id": selected_with["id"],
                "selection_equal_without_permission": (
                    selected_with["id"] == selected_without["id"]
                ),
                "dispatch": {
                    "permitted": {
                        "exit_code": allowed_rc,
                        "launch_boundary_reached": allowed_launch,
                    },
                    "permission_absent": {
                        "exit_code": denied_rc,
                        "launch_boundary_reached": denied_launch,
                    },
                    "review_with_task_only_permission": {
                        "exit_code": review_rc,
                        "launch_boundary_reached": review_launch,
                    },
                },
                "delegated_contract": {
                    "suite": stop_suite,
                    "status": "passed" if delegated.returncode == 0 else "failed",
                    "scope": "operator-language stop boundary",
                },
            },
            "limitations": [
                {
                    "id": "dispatch-process-seam",
                    "surface": "dispatch process creation",
                    "status": "simulated/static",
                    "reason": (
                        "the real dispatch handler reaches or refuses the Popen "
                        "boundary; canonical dispatch suites execute native wrappers"
                    ),
                }
            ],
        }


def _version_evidence_is_truthful(
    observations: dict[str, dict[str, Any]],
    limitations: list[dict[str, str]],
) -> bool:
    limitation_ids = {
        item["id"]
        for item in limitations
        if item.get("status") in {"simulated/static", "static/unverified"}
    }
    for record in observations.values():
        execution = record.get("execution")
        if execution != "native" and record.get("limitation") not in limitation_ids:
            return False
        if record.get("verification") in {"unknown", "unverified"} and record.get(
            "state"
        ) not in {"unknown", "unverified"}:
            return False
    return True


def _version_probe(_case: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="cartopian-version-matrix-") as raw:
        root = Path(raw) / "content"
        root.mkdir()
        (root / ".git").mkdir()

        def clean_git(_root: Path, *args: str) -> str:
            return "abc123" if args == ("rev-parse", "HEAD") else ""

        def dirty_git(_root: Path, *args: str) -> str:
            return "abc123" if args == ("rev-parse", "HEAD") else " M cli/main.py"

        with mock.patch.object(version_identities, "_git", clean_git):
            clean = version_identities.installed_content(root)
        with mock.patch.object(version_identities, "_git", dirty_git):
            dirty = version_identities.installed_content(root)
        link = Path(raw) / "linked-content"
        link.symlink_to(root, target_is_directory=True)
        with mock.patch.object(version_identities, "_git", clean_git):
            divergent = version_identities.installed_content(link)
        running = version_identities.running_server(divergent, process_id=7)
        peers = version_identities.version_identities(
            root,
            project_schema={
                "value": "v0.5.0",
                "target": "v0.6.0",
                "state": "older",
                "authority": "project-config-and-shipped-schema",
                "verification": "verified",
                "attribution": "project-config",
            },
            mcp_protocol_version="2025-06-18",
        )
    classifications = {
        value: protocol_gate.classify_project_schema_version(value, "v0.6.0")[
            "status"
        ]
        for value in ("v0.5.0", "v0.6.0", "v9.0.0", "malformed")
    }
    states = {
        "release": peers["release_version"]["state"],
        "clean_installed": clean["state"],
        "dirty_installed": dirty["state"],
        "symlink_installed": divergent["state"],
        "running_symlink": running["state"],
        "project_schema": peers["project_schema_version"]["state"],
        "mcp_transport": peers["mcp_protocol_version"]["state"],
        "schema_classifications": classifications,
    }
    expected = {
        "release": "unknown",
        "clean_installed": "verified",
        "dirty_installed": "dirty",
        "symlink_installed": "symlink-divergent",
        "running_symlink": "stale-runtime",
        "project_schema": "older",
        "mcp_transport": "supported",
        "schema_classifications": {
            "v0.5.0": "older-migratable",
            "v0.6.0": "current",
            "v9.0.0": "unknown-or-newer",
            "malformed": "unknown-or-newer",
        },
    }
    limitations = [
        {
            "id": "patched-git-helper",
            "surface": "installed-content git state synthesis",
            "status": "simulated/static",
            "reason": (
                "clean, dirty, and symlink-divergent records patch the git "
                "inspection seam; canonical version suites cover native execution"
            ),
        }
    ]
    observations = {
        "clean_installed": {
            "state": clean["state"],
            "verification": clean["verification"],
            "execution": "simulated/static",
            "limitation": "patched-git-helper",
        },
        "dirty_installed": {
            "state": dirty["state"],
            "verification": dirty["verification"],
            "execution": "simulated/static",
            "limitation": "patched-git-helper",
        },
        "symlink_installed": {
            "state": divergent["state"],
            "verification": divergent["verification"],
            "execution": "simulated/static",
            "limitation": "patched-git-helper",
        },
        "release": {
            "state": peers["release_version"]["state"],
            "verification": peers["release_version"]["verification"],
            "execution": "native",
        },
        "project_schema": {
            "state": peers["project_schema_version"]["state"],
            "verification": peers["project_schema_version"]["verification"],
            "execution": "native",
        },
        "mcp_transport": {
            "state": peers["mcp_protocol_version"]["state"],
            "verification": peers["mcp_protocol_version"]["verification"],
            "execution": "native",
        },
    }
    return {
        "observed": "complete" if states == expected else "failed",
        "probes": ["version_identities", "protocol_gate"],
        "invariants": {
            "peer_identities_remain_distinct": len(peers) == 5,
            "unavailable_release_is_unknown": states["release"] == "unknown",
            "static_or_unverified_not_silently_passed": (
                _version_evidence_is_truthful(observations, limitations)
            ),
        },
        "filesystem_identities": {},
        "diagnostics": [],
        "rerun_comparison": {
            "states": states,
            "observations": observations,
        },
        "limitations": limitations,
    }


def _run_cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _missing_project_guards(_case: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="cartopian-guard-matrix-") as raw:
        fixture = Path(raw)
        home = fixture / "home"
        project = fixture / "project"
        task = project / "tasks" / "open" / "TASK-01-001-probe.md"
        home.mkdir()
        task.parent.mkdir(parents=True)
        (project / "phases").mkdir()
        (project / "cartopian.toml").write_text("[unknown]\nvalue = true\n")
        task.write_text("# TASK-01-001: Probe\n")
        commands = {
            "resolve-config": ("resolve-config", str(project)),
            "next-action": ("next-action", str(project)),
            "task-bundle": ("task-bundle", str(task)),
            "handoff-packet": ("handoff-packet", str(task), "--role", "coder"),
            "containment-matrix": ("containment-matrix", str(project)),
            "plan-audit": ("plan-audit", str(project)),
        }
        before_tree = _tree_identity(fixture)
        observed = {}
        for name, args in commands.items():
            result = _run_cli(home, *args)
            observed[name] = {
                "exit_code": result.returncode,
                "prefix": result.stderr.split(" ", 1)[0],
                "guard_phrase": "has no [project] table" in result.stderr,
                "stdout_empty": result.stdout == "",
            }
        with mock.patch("pathlib.Path.home", return_value=home):
            mcp = server._invoke_cli("handoff-packet", [str(task), "--role", "coder"])
        observed["mcp-handoff-packet"] = {
            "exit_code": mcp["exit_code"],
            "prefix": mcp["stderr_lines"][0].split(" ", 1)[0],
            "guard_phrase": "has no [project] table" in mcp["stderr_lines"][0],
            "stdout_empty": mcp["stdout_raw"] == "" and not mcp["records"],
        }
        after_tree = _tree_identity(fixture)
    passed = all(
        item
        == {
            "exit_code": 1,
            "prefix": "[guard]",
            "guard_phrase": True,
            "stdout_empty": True,
        }
        for item in observed.values()
    )
    return {
        "observed": "complete" if passed else "failed",
        "probes": list(observed),
        "invariants": {
            "shared_guard_prefix": passed,
            "read_only": before_tree == after_tree,
        },
        "filesystem_identities": {
            "complete_fixture_tree_before": before_tree,
            "complete_fixture_tree_after": after_tree,
        },
        "diagnostics": [] if passed else [{"code": "guard-parity", "observed": observed}],
        "rerun_comparison": {"surface_results": observed},
    }


def _hygiene(_case: dict[str, Any]) -> dict[str, Any]:
    placeholders = (
        "$HOME/project ${HOME}/project /home/runner/work/repository/repository "
        "C:\\Users\\runneradmin\\work\\repository /Users/<name>/work"
    )
    real_values = (
        "/home/alice/private /Users/alice/private C:\\Users\\alice\\private "
        "/home/runner/private C:\\Users\\runneradmin\\private "
        "alice@operator.invalid sk-probe0123456789012345"
    )
    allowed = guidance_hygiene(placeholders)
    rejected = guidance_hygiene(real_values)
    passed = not allowed and set(rejected) == {
        "linux-user-path",
        "macos-user-path",
        "windows-user-path",
        "operator-email",
        "openai-secret",
    }
    return {
        "observed": "complete" if passed else "failed",
        "probes": ["guidance_hygiene"],
        "invariants": {
            "shell_and_hosted_ci_path_shapes_allowed": not allowed,
            "hosted_ci_account_names_outside_workspace_rejected": (
                "linux-user-path" in rejected
                and "windows-user-path" in rejected
            ),
            "real_home_identifiers_and_secrets_rejected": passed,
        },
        "filesystem_identities": {},
        "diagnostics": [{"allowed": list(allowed), "rejected": list(rejected)}],
        "rerun_comparison": {
            "results_equal": (
                allowed == guidance_hygiene(placeholders)
                and rejected == guidance_hygiene(real_values)
            )
        },
    }


def _invalid_diagnostics(_case: dict[str, Any]) -> dict[str, Any]:
    authored = {
        "global": {"mystery": True},
        "project": {
            "project": {
                "id": "invalid-matrix",
                "name": "Invalid Matrix",
                "project_schema_version": "v0.6.0",
            },
            "roles": {
                "coder": {
                    "description": "Implements work.",
                    "grants": ["unknown-grant"],
                }
            },
        },
    }
    before = _sha256(
        json.dumps(authored, sort_keys=True, separators=(",", ":")).encode()
    )
    diagnostics = []
    for scope in SCOPES:
        if scope not in authored:
            continue
        try:
            validate_authored_config(authored[scope], scope)
        except ConfigDiagnostic as exc:
            diagnostics.append(exc.as_record())
    after = _sha256(
        json.dumps(authored, sort_keys=True, separators=(",", ":")).encode()
    )
    expected = [
        ("global", "mystery", "unknown-key"),
        ("project", "roles.coder.grants", "unknown-value"),
    ]
    observed = [
        (item["scope"], item["field"], item["code"]) for item in diagnostics
    ]
    return {
        "observed": "complete" if observed == expected else "failed",
        "probes": ["validate_authored_config per authoritative scope"],
        "invariants": {
            "primary_and_secondary_diagnostics": len(diagnostics) == 2,
            "authority_scope_order": observed == expected,
            "input_hash_unchanged": before == after,
        },
        "filesystem_identities": {"before": before, "after": after},
        "diagnostics": diagnostics,
        "rerun_comparison": {
            "diagnostics_equal": observed
            == [
                ("global", "mystery", "unknown-key"),
                ("project", "roles.coder.grants", "unknown-value"),
            ]
        },
    }


def _safety_boundaries(_case: dict[str, Any]) -> dict[str, Any]:
    gated = resolve_grants(
        {
            "coder": {
                "description": "Implements work.",
                "grants": ["coder-like"],
            },
            "manual": {"description": "Handles manual work."},
        }
    )
    source = (
        "# SPEC-01-001: Product behavior\n\n"
        "Plan refs: P02-TEST-001\n\n"
        "## Goal\n\nShip the behavior (see FR-023).\n"
    )
    scrubbed, redactions = deidentify_spec(source)
    project = {
        "project": {
            "id": "safety-boundaries",
            "name": "Safety Boundaries",
            "project_schema_version": "v0.6.0",
            "work_roots": ["product"],
        },
        "roles": {
            "manual": {"description": "Handles manual work."},
        },
        "defaults": {"git_versioning": False},
        "git": {"pm_owns_product_branches": False},
    }
    resolved = resolve_configuration(
        {},
        project,
        {"work_roots": {"product": "/fixture/product"}},
    )
    invariants = {
        "deidentification_removes_pm_ids": not list_identifiers(scrubbed)
        and redactions == ["FR-023", "P02-TEST-001", "SPEC-01-001"],
        "capability_activation_is_project_wide": gated.activated
        and not gated.role_grants["manual"],
        "preset_expands_without_widening": gated.role_grants["coder"]
        == frozenset({"read:prompts", "read:work-roots", "write:worktree"}),
        "work_root_is_declared_and_mapped": resolved["work_roots"]
        == {"product": "/fixture/product"},
        "manual_fallback_has_no_launch_target": resolved["roles"]["manual"][
            "launch"
        ]["target"]
        is None,
        "pm_scope_has_no_launch_target": resolved["roles"]["pm"]["launch"][
            "target"
        ]
        is None,
        "source_git_is_human_owned": not resolved["git_versioning"]
        and resolved["git"] is None,
    }
    return {
        "observed": "complete" if all(invariants.values()) else "failed",
        "probes": ["deidentify_spec", "resolve_grants", "resolve_configuration"],
        "invariants": invariants,
        "filesystem_identities": {},
        "diagnostics": [],
        "rerun_comparison": {
            "deidentified_bytes_equal": deidentify_spec(source)[0] == scrubbed,
            "resolved_record_equal": resolve_configuration(
                {},
                project,
                {"work_roots": {"product": "/fixture/product"}},
            )
            == resolved,
        },
    }


PROBES = {
    "migration": _migration,
    "interruption": _interruption,
    "surface-parity": _surface_parity,
    "launch-separation": _launch_separation,
    "version-identities": _version_probe,
    "missing-project-guards": _missing_project_guards,
    "hygiene": _hygiene,
    "invalid-diagnostics": _invalid_diagnostics,
    "safety-boundaries": _safety_boundaries,
}


def run_matrix() -> dict[str, Any]:
    specification = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    results = []
    for case in specification["cases"]:
        result = PROBES[case["probe"]](case)
        result = {"case": case["id"], "expected": case["expected"], **result}
        result["matched"] = result["observed"] == result["expected"] and all(
            result["invariants"].values()
        )
        results.append(result)
    return {
        "schema_version": 1,
        "summary": {
            "total": len(results),
            "matched": sum(item["matched"] for item in results),
            "mismatched": sum(not item["matched"] for item in results),
        },
        "cases": results,
        "coverage": specification["coverage"],
        "canonical_suites": specification["canonical_suites"],
        "limitations": [
            {
                "surface": "native client bridges",
                "status": "static/unverified",
                "reason": "ordinary deterministic CI does not launch proprietary native clients"
            }
        ],
    }


def render_machine(result: dict[str, Any]) -> str:
    return json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"


def main() -> int:
    result = run_matrix()
    sys.stdout.write(render_machine(result))
    return 0 if result["summary"]["mismatched"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
