"""Static regression tests for the initiation/selection separation.

Guards the v0.4.0 semantic contract in protocol and skill prose:

- Deterministic selection answers *which task would run next*; it does not
  authorize execution. Execution begins only from an operator execution
  directive or from `[automation] initiation = "auto"`.
- Operator requests are classified by intent (execution directive /
  informational request / scoped directive), and a question never acquires
  side effects.
- Explicit stop/pause language always overrides configuration.

These are text-surface checks: if a rewrite drops one of these guardrails,
the regression that motivated v0.4.0 (a ready queue silently treated as
permission to run it) can reappear without any code-level test failing.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONVENTIONS = REPO_ROOT / "protocol" / "CONVENTIONS.md"
CHANGELOG = REPO_ROOT / "protocol" / "CHANGELOG.md"
SKILLS_DIR = REPO_ROOT / "skills"
GLOBAL_TOML_TEMPLATE = REPO_ROOT / "templates" / "global.cartopian.toml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ConventionsIntentClassificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = _read(CONVENTIONS)

    def test_request_intent_section_exists(self) -> None:
        self.assertIn("## Request Intent", self.text)

    def test_critical_semantic_statement_present(self) -> None:
        # The load-bearing sentence: selection and initiation are different
        # authorities.
        self.assertIn("**Selection does not authorize execution.**", self.text)
        self.assertIn(
            'Execution begins only from an operator execution directive or '
            'from `[automation] initiation = "auto"`',
            self.text,
        )

    def test_execution_directives_enumerated(self) -> None:
        m = re.search(r"- \*\*Execution directives\*\* — (.+)", self.text)
        self.assertIsNotNone(m, msg="Execution directives bullet missing")
        bullet = m.group(1)
        for phrase in ('"continue"', '"resume"', '"start working"', '"run the next task"'):
            self.assertIn(phrase, bullet, msg=f"execution directive example missing: {phrase}")

    def test_informational_requests_are_read_only(self) -> None:
        m = re.search(r"- \*\*Informational requests\*\* — (.+)", self.text)
        self.assertIsNotNone(m, msg="Informational requests bullet missing")
        bullet = m.group(1)
        for phrase in ("\"what's next?\"", '"check `STATE.md`"'):
            self.assertIn(phrase, bullet, msg=f"informational example missing: {phrase}")
        # A question must never acquire side effects, even under auto.
        self.assertIn("never initiates execution", bullet)
        self.assertIn('initiation = "auto"', bullet)

    def test_scoped_directives_authorize_only_the_named_operation(self) -> None:
        m = re.search(r"- \*\*Scoped directives\*\* — (.+)", self.text)
        self.assertIsNotNone(m, msg="Scoped directives bullet missing")
        self.assertIn("authorize exactly the named operation", m.group(1))
        # The directive-scope rule also lives in Task Execution Order.
        self.assertIn("**Directive scope.**", self.text)
        self.assertIn("never rolls into execution on its own", self.text)

    def test_stop_language_overrides_configuration(self) -> None:
        self.assertIn(
            'An explicit "stop", "pause", or "don\'t execute" always overrides configuration',
            self.text,
        )

    def test_automation_initiation_key_documented(self) -> None:
        self.assertIn('initiation = "operator"', self.text)
        self.assertIn("Supported `initiation` values are:", self.text)
        self.assertIn(
            "`operator`: execution begins only from an operator execution directive",
            self.text,
        )
        self.assertIn("`auto`: the PM may initiate a run without a directive", self.text)
        # Defaults sentence names the fail-safe resolution rule.
        self.assertIn('Defaults are `initiation = "operator"`', self.text)

    def test_confirmation_does_not_authorize_initiation(self) -> None:
        self.assertIn(
            "`until-blocked` describes how far an initiated run chains, not whether one starts",
            self.text,
        )

    def test_auto_start_never_initiates_a_run(self) -> None:
        self.assertIn("it never initiates a run", self.text)


class SkillsIntentClassificationTest(unittest.TestCase):
    def _read_skill(self, rel: str) -> str:
        return _read(SKILLS_DIR / rel)

    def test_start_session_gates_execution_on_intent(self) -> None:
        text = self._read_skill("start-session.md")
        self.assertIn("selection does not authorize execution", text)
        self.assertIn("Classify the request first", text)
        self.assertIn("Never initiate execution from an informational request", text)
        self.assertIn("automatic initiation included", text)
        # The next-action record now carries the resolved policy the gate reads.
        self.assertIn("`automation`", text)

    def test_use_cartopian_names_the_initiation_gate(self) -> None:
        text = self._read_skill("use-cartopian.md")
        self.assertIn("selection does not authorize execution", text)
        self.assertIn("Request Intent", text)

    def test_task_generation_is_a_scoped_directive(self) -> None:
        for skill in ("plan-project.md", "adopt-plan.md"):
            text = self._read_skill(skill)
            self.assertIn(
                "scoped directive",
                text,
                msg=f"{skill} must name task generation a scoped directive",
            )
            self.assertIn(
                "does not authorize running it",
                text,
                msg=f"{skill} must state that filling the queue does not authorize running it",
            )

    def test_init_skills_offer_initiation_presets(self) -> None:
        for skill in ("init-workspace.md", "init-project.md"):
            text = self._read_skill(skill)
            self.assertIn("Wait for me to start work", text, msg=f"{skill} missing attended preset")
            self.assertIn(
                "Automatically start ready work", text, msg=f"{skill} missing unattended preset"
            )

    def test_skills_readme_does_not_reintroduce_ask_to_begin(self) -> None:
        text = self._read_skill("README.md")
        self.assertNotIn("asks whether to begin", text)
        self.assertIn("Request Intent", text)


class ConfigSurfaceTest(unittest.TestCase):
    def test_global_template_documents_initiation_and_recipe(self) -> None:
        text = _read(GLOBAL_TOML_TEMPLATE)
        self.assertIn('# initiation = "operator"', text)
        # The full unattended recipe is shown as explicit stacked opt-ins.
        self.assertIn('# initiation = "auto"', text)
        self.assertIn('# confirmation = "until-blocked"', text)
        self.assertIn("never initiates a run", text)

    def test_changelog_preserves_initiation_migration_below_current_entry(self) -> None:
        text = _read(CHANGELOG)
        _, _, body = text.partition("\n## Entries\n")
        m = re.search(r"^###\s+(v\d+\.\d+\.\d+)\b", body, flags=re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "v0.6.0")
        self.assertIn("### v0.4.0", body)
        self.assertIn("never choose silently", body)
        self.assertIn('initiation = "auto"', body)

    def test_startup_slice_carries_request_intent(self) -> None:
        from mcp_server import server

        self.assertIn("Request Intent", server.STARTUP_SECTIONS)
        # Fail-closed parity: every curated heading must exist in the doc.
        sections = server._split_h2_sections(_read(CONVENTIONS))
        slugs = set(sections.keys())
        for heading in server.STARTUP_SECTIONS:
            self.assertIn(
                server._section_slug(heading),
                slugs,
                msg=f"STARTUP_SECTIONS heading missing from CONVENTIONS.md: {heading}",
            )


if __name__ == "__main__":
    unittest.main()
