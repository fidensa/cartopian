"""FR-004 cross-platform smoke (TASK-01-012, NFR-005, P01-BUILD-012).

Exercises each FR-004 command at least once. The module is intentionally
cross-platform-safe so the same source runs unchanged on macOS, Linux,
native Windows PowerShell, and WSL:

- Paths use ``pathlib`` / ``Path`` joining; no ``/`` literals beyond
  CLI subcommand names.
- Subprocesses use ``sys.executable`` + ``str(ENTRYPOINT)`` + ``text=True``
  so the launcher does not depend on the platform shell or a ``#!``
  shebang interpretation.
- Temporary state goes through ``tempfile.TemporaryDirectory`` (the OS
  default temp root); no hard-coded ``/tmp``.
- Assertions cover exit code, NDJSON record presence, and the expected
  ``action`` for write commands. They do not assert any path-byte
  identity across platforms (per SPEC-01-001 NFR-005 — record schema
  is identical across platforms, path strings render in platform-native
  form, byte-equality is not required).
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


def _env_with(home: Path) -> dict:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    # Windows native uses USERPROFILE rather than HOME for home-lookup;
    # mirror HOME there so the smoke is platform-symmetric.
    if os.name == "nt":
        env["USERPROFILE"] = str(home)
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return env


def _run_cli(*cli_args, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=_env_with(home),
        check=False,
    )


class TestFr004Smoke(unittest.TestCase):
    """One smoke invocation per FR-004 command.

    The suite walks the full FR-004 surface in a realistic order:

    - scaffold-project / generate-config / register-project /
      discover-projects / resolve-config (project bring-up);
    - parse-report and validate-task-readiness against minimal seeded
      fixtures;
    - move-task on a seeded task file;
    - unregister-project to tear down the registry entry.
    """

    def test_walks_every_fr004_command_on_this_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = tmp_path / "proj"

            # scaffold-project — create-and-seed path
            scaffold = _run_cli("scaffold-project", str(project), home=home)
            self.assertEqual(scaffold.returncode, 0, msg=scaffold.stderr)
            scaffold_rec = json.loads(scaffold.stdout.strip())
            self.assertEqual(scaffold_rec["action"], "scaffold-project")
            self.assertIn(scaffold_rec["details"]["outcome"], ("scaffolded", "noop"))

            # generate-config — write minimal cartopian.toml
            gen = _run_cli(
                "generate-config", str(project),
                "--name", "Smoke Demo",
                "--id", "smoke-demo",
                home=home,
            )
            self.assertEqual(gen.returncode, 0, msg=gen.stderr)
            gen_rec = json.loads(gen.stdout.strip())
            self.assertEqual(gen_rec["action"], "generate-config")

            # resolve-config — read it back
            resolve = _run_cli("resolve-config", str(project), home=home)
            self.assertEqual(resolve.returncode, 0, msg=resolve.stderr)
            resolve_rec = json.loads(resolve.stdout.strip())
            self.assertEqual(resolve_rec["project_id"], "smoke-demo")
            self.assertEqual(resolve_rec["project_name"], "Smoke Demo")

            # register-project — append registry entry
            register = _run_cli("register-project", str(project), home=home)
            self.assertEqual(register.returncode, 0, msg=register.stderr)
            register_rec = json.loads(register.stdout.strip())
            self.assertEqual(register_rec["action"], "register-project")
            self.assertEqual(register_rec["details"]["id"], "smoke-demo")

            # discover-projects — find the registered entry
            discover = _run_cli("discover-projects", home=home)
            self.assertEqual(discover.returncode, 0, msg=discover.stderr)
            discover_lines = discover.stdout.splitlines()
            self.assertEqual(len(discover_lines), 1)
            discover_rec = json.loads(discover_lines[0])
            self.assertEqual(discover_rec["id"], "smoke-demo")

            # validate-task-readiness — seed a minimal task and validate
            task_path = project / "tasks" / "open" / "TASK-99-999-smoke.md"
            task_body = (
                "# TASK-99-999: smoke fixture\n"
                "\n"
                "Phase: PHASE-99-smoke\n"
                "Plan ref: P99-SMOKE-001\n"
                "Work root: n/a\n"
                "Evidence gate: n/a\n"
                "\n"
                "## Acceptance\n"
                "\n"
                "- [ ] smoke check\n"
            )
            task_path.write_text(task_body, encoding="utf-8")
            # Seed the phase + plan-ref so validation passes the file checks.
            (project / "phases" / "PHASE-99-smoke.md").write_text(
                "# PHASE-99-smoke\n", encoding="utf-8",
            )
            (project / "IMPLEMENTATION_PLAN.md").write_text(
                "P99-SMOKE-001\n", encoding="utf-8",
            )
            validate = _run_cli(
                "validate-task-readiness", str(task_path), home=home,
            )
            self.assertEqual(validate.returncode, 0, msg=validate.stderr)
            validate_rec = json.loads(validate.stdout.strip())
            self.assertTrue(validate_rec["ready"])

            # parse-report — seed a minimal task report and parse it
            report_path = project / "reports" / "REPORT-99-999.md"
            report_path.write_text(
                "# REPORT-99-999\n"
                "\n"
                "Status: complete\n"
                "\n"
                "## Identity\n"
                "\n"
                "- Task ID: TASK-99-999\n"
                "- Prompt path: " + str(project / "prompts" / "p.md") + "\n"
                "- Task path: " + str(task_path) + "\n"
                "- Repo subpath: n/a\n"
                "\n"
                "## Files changed\n"
                "\n"
                "- none\n"
                "\n"
                "## Test evidence\n"
                "\n"
                "n/a\n"
                "\n"
                "## Commit / PR\n"
                "\n"
                "n/a\n"
                "\n"
                "## Remaining risks\n"
                "\n"
                "None.\n"
                "\n"
                "## Ready for review\n"
                "\n"
                "yes\n",
                encoding="utf-8",
            )
            parse = _run_cli("parse-report", str(report_path), home=home)
            self.assertEqual(parse.returncode, 0, msg=parse.stderr)
            parse_rec = json.loads(parse.stdout.strip())
            self.assertEqual(parse_rec["verdict"], "accepted")
            self.assertEqual(parse_rec["variant"], "task")

            # move-task — open → in-progress
            move = _run_cli(
                "move-task", str(task_path), "in-progress", home=home,
            )
            self.assertEqual(move.returncode, 0, msg=move.stderr)
            move_rec = json.loads(move.stdout.strip())
            self.assertEqual(move_rec["action"], "move-task")
            self.assertEqual(move_rec["details"]["from_status"], "open")
            self.assertEqual(move_rec["details"]["to_status"], "in-progress")
            self.assertFalse(task_path.exists())
            self.assertTrue(
                (project / "tasks" / "in-progress" / task_path.name).is_file()
            )

            # list-tasks — enumerate the seeded task; AND-filter happy path
            listing = _run_cli(
                "list-tasks",
                "--project", "smoke-demo",
                "--phase", "PHASE-99-smoke",
                "--status", "in-progress",
                home=home,
            )
            self.assertEqual(listing.returncode, 0, msg=listing.stderr)
            list_lines = listing.stdout.splitlines()
            self.assertEqual(len(list_lines), 1)
            list_rec = json.loads(list_lines[0])
            self.assertEqual(list_rec["task_id"], "TASK-99-999")
            self.assertEqual(list_rec["phase"], "PHASE-99-smoke")
            self.assertEqual(list_rec["status"], "in-progress")

            # delete-prompt — seed a prompt then delete it
            prompt_path = project / "prompts" / "PROMPT-99-999.md"
            prompt_path.write_text("# prompt\n", encoding="utf-8")
            delete_p = _run_cli(
                "delete-prompt", str(prompt_path), home=home,
            )
            self.assertEqual(delete_p.returncode, 0, msg=delete_p.stderr)
            delete_p_rec = json.loads(delete_p.stdout.strip())
            self.assertEqual(delete_p_rec["action"], "delete-prompt")
            self.assertEqual(
                delete_p_rec["details"]["deleted_path"], str(prompt_path)
            )
            self.assertFalse(prompt_path.exists())

            # delete-report — reuse the seeded report
            delete_r = _run_cli(
                "delete-report", str(report_path), home=home,
            )
            self.assertEqual(delete_r.returncode, 0, msg=delete_r.stderr)
            delete_r_rec = json.loads(delete_r.stdout.strip())
            self.assertEqual(delete_r_rec["action"], "delete-report")
            self.assertEqual(
                delete_r_rec["details"]["deleted_path"], str(report_path)
            )
            self.assertFalse(report_path.exists())

            # unregister-project — tear down
            unregister = _run_cli(
                "unregister-project", "smoke-demo", home=home,
            )
            self.assertEqual(unregister.returncode, 0, msg=unregister.stderr)
            unreg_rec = json.loads(unregister.stdout.strip())
            self.assertEqual(unreg_rec["action"], "unregister-project")
            self.assertEqual(unreg_rec["details"]["id"], "smoke-demo")

            # discover-projects after teardown — empty stdout, exit 0
            after = _run_cli("discover-projects", home=home)
            self.assertEqual(after.returncode, 0, msg=after.stderr)
            self.assertEqual(after.stdout, "")


if __name__ == "__main__":
    unittest.main()
