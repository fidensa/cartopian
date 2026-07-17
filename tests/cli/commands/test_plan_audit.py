"""Tests for `cartopian plan-audit` command."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

_MINIMAL_TOML = (
    '[project]\n'
    'id = "test"\n'
    'name = "Test"\n'
    'protocol_version = "v0.5.0"\n'
)

_REVIEW_TOML = (
    '\n'
    '[roles]\n'
    'reviewer = "Reviews completed work."\n'
    '\n'
    '[reviews]\n'
    'planning = "required"\n'
    'planning_role = "reviewer"\n'
    'task_closure = "required"\n'
    'task_role = "reviewer"\n'
)

_REVIEW_OFF_TOML = (
    '\n[reviews]\n'
    'planning = "off"\n'
    'task_closure = "off"\n'
)


def _run(*cli_args, home, cwd=None):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "plan-audit", *cli_args],
        cwd=str(cwd) if cwd is not None else str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _make_project(tmp: Path) -> Path:
    project = tmp / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "cartopian.toml").write_text(
        _MINIMAL_TOML + _REVIEW_TOML, encoding="utf-8"
    )
    for sub in ("tasks/open", "tasks/in-progress", "tasks/in-review", "tasks/done",
                "phases", "prompts", "reports", "reviews"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    return project


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestPlanAuditHelp(unittest.TestCase):
    def test_help_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(ENTRYPOINT), "plan-audit", "--help"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                env={"HOME": tmp, "PATH": os.environ.get("PATH", "")},
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("project_path", proc.stdout)


class TestPlanAuditUsage(unittest.TestCase):
    def test_relative_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run("projects/my-project", home=Path(tmp))
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)

    def test_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run("/nonexistent/path/that/does/not/exist", home=Path(tmp))
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)

    def test_directory_without_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run(tmp, home=Path(tmp))
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)
            self.assertIn("cartopian.toml", proc.stderr)

    def test_defaults_only_cartopian_toml_is_not_a_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir()
            (project / "cartopian.toml").write_text(
                '[defaults]\ngit_versioning = false\n',
                encoding="utf-8",
            )
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, "")
            self.assertEqual(
                proc.stderr.rstrip("\n"),
                f"[guard] {project / 'cartopian.toml'} is a Cartopian workspace config, "
                "not a project config. "
                "Run `cartopian discover-projects` (or call the `discover_projects` MCP tool) "
                "to list registered projects, then pass a project id or absolute path to this command.",
            )


class TestPlanAuditClean(unittest.TestCase):
    def test_empty_project_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertEqual(record["action"], "plan-audit")
            self.assertTrue(record["clean"])
            self.assertEqual(record["blockers"], [])
            self.assertEqual(record["warnings"], [])
            self.assertEqual(record["attributions"], [])

    def test_in_progress_task_with_prompt_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-progress" / "TASK-01-003-do-thing.md", "# task\n")
            _write(project / "prompts" / "PROMPT-01-003.md", "# prompt\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])
            self.assertEqual(record["warnings"], [])

    def test_in_review_task_with_review_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-review" / "TASK-01-004-review-me.md", "# task\n")
            _write(project / "reviews" / "REVIEW-01-004.md",
                   "# REVIEW-01-004\n\nVerdict: approve\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])
            self.assertEqual(record["warnings"], [])

    def test_non_canonical_task_names_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            # Non-canonical name — no prompt required
            _write(project / "tasks" / "in-progress" / "TASK-admin-only.md", "# task\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])
            self.assertEqual(record["warnings"], [])


class TestPlanAuditArtifactChainBlockers(unittest.TestCase):
    def test_in_progress_missing_prompt_is_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-progress" / "TASK-01-003-do-thing.md", "# task\n")
            # no prompt
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            record = json.loads(proc.stdout.strip())
            self.assertFalse(record["clean"])
            self.assertEqual(len(record["blockers"]), 1)
            b = record["blockers"][0]
            self.assertEqual(b["kind"], "missing-prompt")
            self.assertEqual(b["task_id"], "TASK-01-003")
            self.assertIn("PROMPT-01-003.md", b["expected"])

    def test_in_review_missing_review_artifact_is_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-review" / "TASK-02-005-thing.md", "# task\n")
            # no review file
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            record = json.loads(proc.stdout.strip())
            self.assertFalse(record["clean"])
            b = record["blockers"][0]
            self.assertEqual(b["kind"], "missing-review-artifact")
            self.assertEqual(b["task_id"], "TASK-02-005")

    def test_in_review_review_missing_verdict_is_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-review" / "TASK-02-005-thing.md", "# task\n")
            # review file exists but has no Verdict: field
            _write(project / "reviews" / "REVIEW-02-005.md",
                   "# REVIEW-02-005\n\nNo verdict here.\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            record = json.loads(proc.stdout.strip())
            b = record["blockers"][0]
            self.assertEqual(b["kind"], "review-missing-verdict")

    def test_multiple_blockers_all_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-progress" / "TASK-01-001-a.md", "# task\n")
            _write(project / "tasks" / "in-progress" / "TASK-01-002-b.md", "# task\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            record = json.loads(proc.stdout.strip())
            self.assertEqual(len(record["blockers"]), 2)
            kinds = {b["kind"] for b in record["blockers"]}
            self.assertEqual(kinds, {"missing-prompt"})

    def test_done_tasks_not_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            # Done task with no artifacts — should not trigger blockers
            _write(project / "tasks" / "done" / "TASK-01-001-finished.md", "# task\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])


class TestPlanAuditReviewOff(unittest.TestCase):
    def _make_review_off_project(self, root: Path) -> Path:
        project = _make_project(root)
        (project / "cartopian.toml").write_text(
            _MINIMAL_TOML + _REVIEW_OFF_TOML, encoding="utf-8"
        )
        return project

    def test_in_review_missing_artifact_is_advisory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._make_review_off_project(root)
            _write(project / "tasks/in-review/TASK-02-005-thing.md", "# task\n")
            result = _run(str(project), home=root)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["blockers"], [])
        self.assertEqual(record["warnings"][0]["kind"], "missing-review-artifact")
        self.assertFalse(record["clean"])

    def test_done_missing_deliverable_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._make_review_off_project(root)
            _write(
                project / "tasks/done/TASK-02-006-output.md",
                "# task\n\nDeliverable: project:outputs/result.md\n",
            )
            result = _run(str(project), home=root)
        self.assertEqual(result.returncode, 1)
        record = json.loads(result.stdout)
        self.assertEqual(record["blockers"][0]["kind"], "missing-deliverable")

    def test_open_tasks_not_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "open" / "TASK-01-001-waiting.md", "# task\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])


class TestPlanAuditOutput(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_dirty_work_root_is_warning_when_pm_owns_product_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            (project / "cartopian.toml").write_text(
                _MINIMAL_TOML
                + 'work_roots = ["tool-repo"]\n'
                + '\n[git]\npm_owns_product_branches = true\n'
                + _REVIEW_TOML,
                encoding="utf-8",
            )
            work_root = tmp_path / "tool-repo"
            work_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init"],
                cwd=str(work_root),
                capture_output=True,
                text=True,
                check=True,
            )
            (project / "cartopian.local.toml").write_text(
                f"[work_roots]\ntool-repo = \"{work_root}\"\n",
                encoding="utf-8",
            )
            _write(work_root / "scratch.txt", "local changes\n")

            proc = _run(str(project), home=tmp_path)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertFalse(record["clean"])
            self.assertEqual(record["blockers"], [])
            self.assertEqual(len(record["warnings"]), 1)
            self.assertEqual(record["warnings"][0]["kind"], "unattributed-work-root-changes")
            self.assertEqual(record.get("attributions", []), [])
            self.assertIn("[warning]", proc.stderr)

    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_dirty_work_root_attributed_when_pm_does_not_own_product_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            (project / "cartopian.toml").write_text(
                _MINIMAL_TOML + 'work_roots = ["tool-repo"]\n' + _REVIEW_TOML,
                encoding="utf-8",
            )
            work_root = tmp_path / "tool-repo"
            work_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init"],
                cwd=str(work_root),
                capture_output=True,
                text=True,
                check=True,
            )
            (project / "cartopian.local.toml").write_text(
                f"[work_roots]\ntool-repo = \"{work_root}\"\n",
                encoding="utf-8",
            )
            _write(work_root / "scratch.txt", "local changes\n")
            _write(
                project / "tasks" / "done" / "TASK-01-001-build.md",
                "# task\n\nWork root: tool-repo\nAssignee: coder\n",
            )

            proc = _run(str(project), home=tmp_path)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])
            self.assertEqual(record["blockers"], [])
            self.assertEqual(record["warnings"], [])
            self.assertEqual(len(record["attributions"]), 1)
            attr = record["attributions"][0]
            self.assertEqual(attr["kind"], "work-root-attribution")
            self.assertEqual(attr["work_root"], "tool-repo")
            self.assertEqual(attr["assignee"], "coder")
            self.assertEqual(attr["task_id"], "TASK-01-001")
            self.assertNotIn("[warning]", proc.stderr)
            self.assertIn("[info]", proc.stderr)

    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_dirty_work_root_attribution_unknown_when_no_prior_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            (project / "cartopian.toml").write_text(
                _MINIMAL_TOML + 'work_roots = ["tool-repo"]\n' + _REVIEW_TOML,
                encoding="utf-8",
            )
            work_root = tmp_path / "tool-repo"
            work_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init"],
                cwd=str(work_root),
                capture_output=True,
                text=True,
                check=True,
            )
            (project / "cartopian.local.toml").write_text(
                f"[work_roots]\ntool-repo = \"{work_root}\"\n",
                encoding="utf-8",
            )
            _write(work_root / "scratch.txt", "local changes\n")

            proc = _run(str(project), home=tmp_path)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])
            self.assertEqual(record["warnings"], [])
            self.assertEqual(len(record["attributions"]), 1)
            attr = record["attributions"][0]
            self.assertEqual(attr["kind"], "work-root-attribution")
            self.assertNotIn("assignee", attr)
            self.assertIn("attribution is unknown", attr["detail"])

    def test_output_is_single_ndjson_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            proc = _run(str(project), home=tmp_path)
            lines = [l for l in proc.stdout.splitlines() if l.strip()]
            self.assertEqual(len(lines), 1, msg=f"expected one NDJSON line, got: {proc.stdout!r}")
            record = json.loads(lines[0])
            self.assertEqual(record["action"], "plan-audit")
            self.assertIn("project_path", record)
            self.assertIn("clean", record)
            self.assertIn("blockers", record)
            self.assertIn("warnings", record)
            self.assertIn("attributions", record)

    def test_blockers_emit_audit_stderr_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-progress" / "TASK-01-001-a.md", "# task\n")
            proc = _run(str(project), home=tmp_path)
            self.assertIn("[audit]", proc.stderr)


@unittest.skipUnless(shutil.which("git"), "git required")
class TestPlanAuditInfraArtifacts(unittest.TestCase):
    """Assignee-created `.github`/CI/infra artifacts in a work root
    emit an `unauthorized-infra-artifacts` warning unless a task naming that
    work root explicitly authorizes them. A warning, never a blocker."""

    def _project_with_work_root(self, tmp_path: Path):
        project = _make_project(tmp_path)
        (project / "cartopian.toml").write_text(
            _MINIMAL_TOML + 'work_roots = ["tool-repo"]\n' + _REVIEW_TOML,
            encoding="utf-8",
        )
        work_root = tmp_path / "tool-repo"
        work_root.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init"], cwd=str(work_root),
            capture_output=True, text=True, check=True,
        )
        (project / "cartopian.local.toml").write_text(
            f"[work_roots]\ntool-repo = \"{work_root}\"\n",
            encoding="utf-8",
        )
        return project, work_root

    def test_unauthorized_github_artifact_warns_but_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project, work_root = self._project_with_work_root(tmp_path)
            _write(
                project / "tasks" / "done" / "TASK-01-001-build.md",
                "# task\n\nWork root: tool-repo\nAssignee: coder\n\nDo the thing.\n",
            )
            _write(work_root / ".github" / "workflows" / "ci.yml", "name: ci\n")

            proc = _run(str(project), home=tmp_path)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)  # warning, not blocker
            record = json.loads(proc.stdout.strip())
            self.assertFalse(record["clean"])
            self.assertEqual(record["blockers"], [])
            infra = [w for w in record["warnings"]
                     if w["kind"] == "unauthorized-infra-artifacts"]
            self.assertEqual(len(infra), 1, record["warnings"])
            self.assertEqual(infra[0]["marker"], ".github")
            self.assertEqual(infra[0]["work_root"], "tool-repo")
            self.assertIn(".github/workflows/ci.yml", infra[0]["files"])
            self.assertIn("TASK-01-001", infra[0]["tasks_checked"])
            self.assertIn("[warning]", proc.stderr)

    def test_explicit_infra_authorized_field_suppresses_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project, work_root = self._project_with_work_root(tmp_path)
            _write(
                project / "tasks" / "done" / "TASK-01-001-build.md",
                "# task\n\nWork root: tool-repo\nAssignee: coder\n"
                "Infra authorized: yes\n",
            )
            _write(work_root / ".github" / "workflows" / "ci.yml", "name: ci\n")

            proc = _run(str(project), home=tmp_path)

            record = json.loads(proc.stdout.strip())
            infra = [w for w in record["warnings"]
                     if w["kind"] == "unauthorized-infra-artifacts"]
            self.assertEqual(infra, [], record["warnings"])

    def test_marker_scoped_field_authorizes_that_marker_only(self):
        """`Infra authorized: .github` authorizes .github artifacts — and ONLY
        .github: an unrelated Jenkinsfile still warns (review finding: the
        blanket form over-authorized across markers)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project, work_root = self._project_with_work_root(tmp_path)
            _write(
                project / "tasks" / "in-progress" / "TASK-01-002-ci.md",
                "# task\n\nWork root: tool-repo\nAssignee: coder\n"
                "Infra authorized: .github\n\n"
                "Add the release workflow under .github/workflows/.\n",
            )
            _write(project / "prompts" / "PROMPT-01-002.md", "prompt\n")
            _write(work_root / ".github" / "workflows" / "release.yml", "name: r\n")
            _write(work_root / "Jenkinsfile", "pipeline {}\n")

            proc = _run(str(project), home=tmp_path)

            record = json.loads(proc.stdout.strip())
            infra = [w for w in record["warnings"]
                     if w["kind"] == "unauthorized-infra-artifacts"]
            self.assertEqual(len(infra), 1, record["warnings"])
            self.assertEqual(infra[0]["marker"], "Jenkinsfile")

    def test_prose_mention_of_marker_does_not_authorize(self):
        """Authorization is the explicit field only: an incidental mention
        (`myapp.github.io`) or even a prohibition (`do NOT touch .github`)
        must not suppress the warning (review finding: substring matching
        failed open)."""
        for prose in (
            "Deployed docs to myapp.github.io for preview.",
            "Scope boundary: do NOT touch .github or CI config.",
        ):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                project, work_root = self._project_with_work_root(tmp_path)
                _write(
                    project / "tasks" / "done" / "TASK-01-001-build.md",
                    f"# task\n\nWork root: tool-repo\nAssignee: coder\n\n{prose}\n",
                )
                _write(work_root / ".github" / "workflows" / "ci.yml", "name: ci\n")

                proc = _run(str(project), home=tmp_path)

                record = json.loads(proc.stdout.strip())
                infra = [w for w in record["warnings"]
                         if w["kind"] == "unauthorized-infra-artifacts"]
                self.assertEqual(
                    len(infra), 1,
                    f"prose {prose!r} must not authorize: {record['warnings']}",
                )
                self.assertEqual(infra[0]["marker"], ".github")

    def test_top_level_jenkinsfile_warns_nested_path_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project, work_root = self._project_with_work_root(tmp_path)
            _write(work_root / "Jenkinsfile", "pipeline {}\n")
            _write(work_root / "src" / "Jenkinsfile", "nested — not a repo entrypoint\n")

            proc = _run(str(project), home=tmp_path)

            record = json.loads(proc.stdout.strip())
            infra = [w for w in record["warnings"]
                     if w["kind"] == "unauthorized-infra-artifacts"]
            self.assertEqual(len(infra), 1, record["warnings"])
            self.assertEqual(infra[0]["marker"], "Jenkinsfile")
            self.assertEqual(infra[0]["files"], ["Jenkinsfile"])

    def test_non_infra_dirty_files_emit_no_infra_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project, work_root = self._project_with_work_root(tmp_path)
            _write(work_root / "src" / "main.py", "print('x')\n")

            proc = _run(str(project), home=tmp_path)

            record = json.loads(proc.stdout.strip())
            infra = [w for w in record["warnings"]
                     if w["kind"] == "unauthorized-infra-artifacts"]
            self.assertEqual(infra, [], record["warnings"])


class TestPlanAuditPmIdentifierLeaks(unittest.TestCase):
    """The identifier-leak detection floor: a management planning identifier
    leaked into a changed work-root file fires a `pm-identifier-leak` warning
    through `plan-audit` — a pure regex pass over the changed files, no model
    round-trip. A warning, never a blocker."""

    def _project_with_work_root(self, tmp_path: Path):
        project = _make_project(tmp_path)
        (project / "cartopian.toml").write_text(
            _MINIMAL_TOML + 'work_roots = ["tool-repo"]\n' + _REVIEW_TOML,
            encoding="utf-8",
        )
        work_root = tmp_path / "tool-repo"
        work_root.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init"], cwd=str(work_root),
            capture_output=True, text=True, check=True,
        )
        (project / "cartopian.local.toml").write_text(
            f"[work_roots]\ntool-repo = \"{work_root}\"\n",
            encoding="utf-8",
        )
        return project, work_root

    def test_leaked_identifier_in_changed_file_warns_but_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project, work_root = self._project_with_work_root(tmp_path)
            # Seed the leak: an assignee annotating product code with a
            # management-bookkeeping reference, ticket-comment style.
            _write(
                work_root / "src" / "feature.py",
                "def f():\n"
                "    # acceptance per TASK-01-002\n"
                "    return 1\n",
            )

            proc = _run(str(project), home=tmp_path)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)  # warning, not blocker
            record = json.loads(proc.stdout.strip())
            self.assertFalse(record["clean"])
            self.assertEqual(record["blockers"], [])
            leaks = [w for w in record["warnings"]
                     if w["kind"] == "pm-identifier-leak"]
            self.assertEqual(len(leaks), 1, record["warnings"])
            leak = leaks[0]
            self.assertEqual(leak["work_root"], "tool-repo")
            self.assertIn("src/feature.py", leak["files"])
            self.assertTrue(
                any(h["path"] == "src/feature.py" and h["line"] == 2
                    and h["match"] == "TASK-01-002" for h in leak["hits"]),
                leak["hits"],
            )
            self.assertIn("[warning]", proc.stderr)

    def test_clean_changed_files_emit_no_leak_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project, work_root = self._project_with_work_root(tmp_path)
            _write(work_root / "src" / "main.py",
                   "TIMEOUT = 60  # seconds\nresult = total - 1\n")

            proc = _run(str(project), home=tmp_path)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            leaks = [w for w in record["warnings"]
                     if w["kind"] == "pm-identifier-leak"]
            self.assertEqual(leaks, [], record["warnings"])


class TestPlanAuditBacklogInvariants(unittest.TestCase):
    def test_healthy_backlog_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "BACKLOG.md",
                   "# Backlog\n\nHighest id issued: BL-002\n\n"
                   "## BL-001 — One\n\nA.\n\n## BL-002 — Two\n\nB.\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])

    def test_regressed_mark_is_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            # Mark below a live id — only a raw hand-edit can produce this.
            _write(project / "BACKLOG.md",
                   "# Backlog\n\nHighest id issued: BL-001\n\n## BL-004 — Live\n\nB.\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            record = json.loads(proc.stdout.strip())
            self.assertFalse(record["clean"])
            kinds = [b["kind"] for b in record["blockers"]]
            self.assertIn("backlog-mark-regressed", kinds)

    def test_stamped_live_entry_warns_unfinished_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "BACKLOG.md",
                   "# Backlog\n\nHighest id issued: BL-001\n\n## BL-001 — One\n\nA.\n")
            # A durable artifact already stamps BL-001 but the entry is still
            # live: the benign stamp-then-delete crash-window duplicate.
            _write(project / "tasks" / "open" / "TASK-01-001-x.md",
                   "# TASK-01-001: x\n\nPlan ref: P01-BUILD-001\nSource: BL-001\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            kinds = [w["kind"] for w in record["warnings"]]
            self.assertIn("backlog-promotion-unfinished", kinds)


class TestPlanAuditSituationNotes(unittest.TestCase):
    def test_undelivered_note_is_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "STATE.md",
                   "# Test - State\n\n## Current phase\n\nPhase 01\n\n"
                   "## Situation\n\n"
                   "- coder deploy failed; operator restarting the machine\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            record = json.loads(proc.stdout.strip())
            self.assertFalse(record["clean"])
            notes = [b for b in record["blockers"]
                     if b["kind"] == "unresolved-situation-note"]
            self.assertEqual(len(notes), 1, msg=f"got: {record['blockers']}")
            self.assertIn("coder deploy failed", notes[0]["detail"])
            self.assertIn("write-state", notes[0]["detail"])

    def test_state_without_situation_section_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "STATE.md",
                   "# Test - State\n\n## Current phase\n\nPhase 01\n\n"
                   "## What to do next\n\nContinue.\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])


if __name__ == "__main__":
    unittest.main()
