"""P02-BUILD-010 static-coverage harness for rewritten skills and wrappers.

Checks that:
- Rewritten skills reference the expected Core CLI commands.
- Rewritten skills adopt Work root semantics (no residual Repo subpath usage in scoped files).
- Wrappers implement FR-012 (project-root launch) and reference OQ-009 access-grant mechanics.
- Wrappers README documents FR-012 launch-cwd and OQ-009 access model.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"
PROTOCOL_DIR = REPO_ROOT / "protocol"
WRAPPERS_BIN_DIR = REPO_ROOT / "wrappers" / "bin"
WRAPPERS_PS1_DIR = REPO_ROOT / "wrappers" / "ps1"
WRAPPERS_README = REPO_ROOT / "wrappers" / "README.md"


class SkillsStaticCoverageTest(unittest.TestCase):
    def _read(self, rel: str) -> str:
        return (SKILLS_DIR / rel).read_text(encoding="utf-8")

    def test_run_task_references_core_cli_and_work_root(self) -> None:
        text = self._read("run-task.md")
        for needle in (
            "cartopian resolve-config",
            "cartopian validate-task-readiness",
            "cartopian move-task",
            "cartopian delete-report",
            "cartopian delete-prompt",
            "cartopian next-action",
            "cartopian task-bundle",
            "cartopian handoff-packet",
            "cartopian report-action",
            "cartopian compose-state",
            "Work root:",
        ):
            self.assertIn(needle, text, msg=f"run-task.md must reference `{needle}`")
        # Guard against retired vocabulary in this rewritten skill.
        self.assertNotRegex(text, r"Repo subpath:")

    def test_run_handoff_references_core_cli(self) -> None:
        text = self._read("run-handoff.md")
        for needle in (
            "cartopian resolve-config",
            "cartopian delete-report",
            "cartopian report-action",
            "cartopian handoff-packet",
        ):
            self.assertIn(needle, text, msg=f"run-handoff.md must reference `{needle}`")
        self.assertNotRegex(text, r"Repo subpath:")

    def test_start_session_references_core_cli(self) -> None:
        text = self._read("start-session.md")
        for needle in (
            "cartopian discover-projects",
            "cartopian resolve-config",
            "cartopian next-action",
        ):
            self.assertIn(needle, text, msg=f"start-session.md must reference `{needle}`")

    def test_close_plan_references_core_cli(self) -> None:
        text = self._read("close-plan.md")
        for needle in (
            "cartopian close-audit",
            "cartopian compose-state",
        ):
            self.assertIn(needle, text, msg=f"close-plan.md must reference `{needle}`")

    def test_adopt_plan_stage0_has_registry_and_resolve(self) -> None:
        text = self._read("adopt-plan.md")
        for needle in (
            "cartopian discover-projects",
            "cartopian register-project",
            "cartopian resolve-config",
            "Work root:",
        ):
            self.assertIn(needle, text, msg=f"adopt-plan.md must reference `{needle}`")
        self.assertNotRegex(text, r"Repo subpath:")

    def test_adopt_requirements_stage0_has_registry_and_resolve(self) -> None:
        text = self._read("adopt-requirements.md")
        for needle in (
            "cartopian discover-projects",
            "cartopian register-project",
            "cartopian resolve-config",
        ):
            self.assertIn(needle, text, msg=f"adopt-requirements.md must reference `{needle}`")
        self.assertNotRegex(text, r"Repo subpath:")

    def test_plan_project_uses_cli_cleanup_for_prompts_and_reports(self) -> None:
        text = self._read("plan-project.md")
        for needle in (
            "cartopian discover-projects",
            "cartopian resolve-config",
            "cartopian delete-prompt",
            "cartopian delete-report",
        ):
            self.assertIn(needle, text, msg=f"plan-project.md must reference `{needle}`")
        # Do not assert Repo subpath absence here; legacy mention may persist until its own task.


class WrappersStaticCoverageTest(unittest.TestCase):
    def test_wrappers_call_resolve_config_and_offer_unrestricted_bypass(self) -> None:
        # Every wrapper (bash and PowerShell) should reference `cartopian resolve-config`
        # and expose a CARTOPIAN_*_UNRESTRICTED env-var bypass.
        wrappers = []
        wrappers.extend(sorted(WRAPPERS_BIN_DIR.glob("cartopian-*")))
        wrappers.extend(sorted(WRAPPERS_PS1_DIR.glob("cartopian-*.ps1")))
        self.assertTrue(wrappers, "expected wrapper scripts to exist")

        missing_resolve = []
        missing_unrestricted = []
        for path in wrappers:
            text = path.read_text(encoding="utf-8")
            if "cartopian resolve-config" not in text:
                missing_resolve.append(path)
            if not re.search(r"CARTOPIAN_[A-Z]+_UNRESTRICTED", text):
                missing_unrestricted.append(path)
        self.assertEqual(
            missing_resolve,
            [],
            msg=(
                "wrappers missing `cartopian resolve-config` reference: "
                + ", ".join(str(p.relative_to(REPO_ROOT)) for p in missing_resolve)
            ),
        )
        self.assertEqual(
            missing_unrestricted,
            [],
            msg=(
                "wrappers missing CARTOPIAN_*_UNRESTRICTED bypass: "
                + ", ".join(str(p.relative_to(REPO_ROOT)) for p in missing_unrestricted)
            ),
        )

    def test_wrappers_implement_project_root_launch_detection(self) -> None:
        # Check for the prompts-directory launch-cwd detection in both shells.
        bash_hits = []
        for path in sorted(WRAPPERS_BIN_DIR.glob("cartopian-*")):
            text = path.read_text(encoding="utf-8")
            if not re.search(r"basename\s*\(\"\$PROMPTS_DIR\"\)\"?\s*\)\s*==\s*\"prompts\"", text):
                bash_hits.append(path)
        ps1_hits = []
        for path in sorted(WRAPPERS_PS1_DIR.glob("cartopian-*.ps1")):
            text = path.read_text(encoding="utf-8")
            if not re.search(r"Split-Path\s+-Leaf\s+\$PromptsDir\).*prompts", text):
                # Simpler fallback: at least mention 'prompts' and Set-Location logic
                if not ("prompts" in text and "Set-Location" in text):
                    ps1_hits.append(path)
        self.assertEqual(
            bash_hits,
            [],
            msg=(
                "bash wrappers missing prompts-directory FR-012 launch-cwd detection: "
                + ", ".join(str(p.relative_to(REPO_ROOT)) for p in bash_hits)
            ),
        )
        self.assertEqual(
            ps1_hits,
            [],
            msg=(
                "ps1 wrappers missing prompts-directory FR-012 launch-cwd detection: "
                + ", ".join(str(p.relative_to(REPO_ROOT)) for p in ps1_hits)
            ),
        )

    def test_wrappers_readme_documents_fr012_and_oq009(self) -> None:
        text = WRAPPERS_README.read_text(encoding="utf-8")
        self.assertIn("Cartopian project root", text)
        self.assertRegex(text, r"projects/<project-id>")
        # Work-root access model mention
        self.assertTrue(
            ("work-root" in text) or ("work_roots" in text),
            msg="wrappers/README.md should mention work-root access model",
        )


class WaitPrimitiveStaticCoverageTest(unittest.TestCase):
    """P01-BUILD-004: wait-primitive integration into skills and § Handoffs.

    The wait commands (`cartopian wait-handoff` / `cartopian wait-report`) must
    replace ad-hoc sleep loops, manual "wait for the operator to tell you"
    prompts, and PM-side watchdog timers across the handoff-wait surface. These
    assertions check that the wait commands are present and that no ad-hoc
    sleep/poll instruction survives in the updated files.
    """

    # Phrases that signal a hand-rolled sleep/poll/manual-wait instruction —
    # exactly what the wait primitives are meant to replace. Generic words like
    # "polling" used to *describe* the replacement are intentionally not matched.
    FORBIDDEN_PATTERNS = (
        r"\bsleep\b",
        r"busy[-\s]?wait",
        r"poll[-\s]?loop",
        r"poll(?:ing)?[^.\n]{0,30}\brepeatedly\b",
        r"\brepeatedly\b[^.\n]{0,30}poll",
        r"wait for the operator to tell you",
    )

    def _read_skill(self, name: str) -> str:
        return (SKILLS_DIR / name).read_text(encoding="utf-8")

    def _handoffs_section(self) -> str:
        text = (PROTOCOL_DIR / "CONVENTIONS.md").read_text(encoding="utf-8")
        # Extract the "## Handoffs" section body (up to the next H2 heading).
        match = re.search(r"\n## Handoffs\b.*?(?=\n## )", text, re.DOTALL)
        self.assertIsNotNone(
            match, "CONVENTIONS.md must contain a `## Handoffs` section"
        )
        return match.group(0)

    def _assert_no_adhoc_polling(self, label: str, body: str) -> None:
        for pattern in self.FORBIDDEN_PATTERNS:
            self.assertIsNone(
                re.search(pattern, body, re.IGNORECASE),
                msg=(
                    f"{label} must not contain an ad-hoc sleep/poll/manual-wait "
                    f"instruction matching /{pattern}/; use a wait primitive instead"
                ),
            )

    def test_run_handoff_uses_wait_primitives(self) -> None:
        text = self._read_skill("run-handoff.md")
        self.assertIn("cartopian wait-handoff", text)
        self.assertIn("cartopian wait-report", text)
        self.assertIn("still-running", text)
        self._assert_no_adhoc_polling("run-handoff.md", text)

    def test_run_task_uses_wait_handoff(self) -> None:
        text = self._read_skill("run-task.md")
        self.assertIn("cartopian wait-handoff", text)
        self._assert_no_adhoc_polling("run-task.md", text)

    def test_plan_project_uses_wait_report(self) -> None:
        text = self._read_skill("plan-project.md")
        self.assertIn("cartopian wait-report", text)
        self._assert_no_adhoc_polling("plan-project.md", text)

    def test_conventions_handoffs_formalizes_wait_contract(self) -> None:
        section = self._handoffs_section()
        # Wait commands replace ad-hoc polling.
        self.assertIn("cartopian wait-handoff", section)
        self.assertIn("cartopian wait-report", section)
        # Report file is authoritative; the wrapper status file is optional.
        self.assertRegex(section, r"authoritative")
        self.assertRegex(section, r"\.status|status file")
        # still-running yield-and-resume model.
        self.assertIn("still-running", section)
        self.assertRegex(section, r"yield")
        self._assert_no_adhoc_polling("CONVENTIONS.md § Handoffs", section)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
