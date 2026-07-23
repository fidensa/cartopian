"""Static-coverage harness for rewritten skills and wrappers.

Checks that:
- Rewritten skills reference the expected Core CLI commands.
- Rewritten skills adopt Work root semantics (no residual Repo subpath usage in scoped files).
- Wrappers implement project-root launch and reference work-root access-grant mechanics.
- Wrappers README documents launch-cwd and access model.
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
            "cartopian write-state",
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
    def test_wrappers_are_neutral_launchers(self) -> None:
        # The wrappers are neutral launchers: they translate env -> CLI flags,
        # set cwd, run the agent autonomously, enforce the timeout, and emit the
        # status signal. They must NOT carry role-assumption / gating machinery —
        # that belongs to the harness (capability-based), not the launcher.
        # This locks the wrappers (and their shared helpers) against a regression
        # that re-introduces work-root scoping, reviewer recapture, the
        # *_UNRESTRICTED bypass, or the contained-PM signal.
        #
        # Widening is NOT scoping: a wrapper whose agent CLI imposes its own
        # filesystem sandbox rooted at the launch cwd (codex `--sandbox
        # workspace-write`; claude's permission modes) must widen it with the
        # CARTOPIAN_WORK_ROOTS grant dispatch exports (codex writable_roots,
        # claude --add-dir) — otherwise declared work roots are unwritable
        # (tests/wrappers/test_work_roots_grant.py). Only confinement
        # machinery is forbidden here.
        forbidden = [
            "CARTOPIAN_SCOPE_DIRS",
            "CARTOPIAN_REPORT_DIR",
            "CARTOPIAN_REVIEW_RECAPTURE",
            "_UNRESTRICTED",
            "enforce_work_roots",
            "tool_scope_union",
            "Get-CartopianScopeArgs",
            "CARTOPIAN_PM_CONTAINED",
            "--include-directories",
        ]
        paths = (
            sorted(WRAPPERS_BIN_DIR.glob("cartopian-*"))
            + [WRAPPERS_BIN_DIR / "_cartopian-status.sh"]
            + sorted(WRAPPERS_PS1_DIR.glob("cartopian-*.ps1"))
            + [WRAPPERS_PS1_DIR / "CartopianStatus.ps1"]
        )
        offenders = []
        for path in paths:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                if needle in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle}")
        self.assertEqual(
            offenders, [],
            msg="wrappers must stay neutral launchers (no scope/recapture/"
                "containment machinery):\n" + "\n".join(offenders),
        )

    def test_wrappers_implement_project_root_launch_detection(self) -> None:
        # The neutral launchers set cwd from the prompt path: when the prompt
        # lives under <project>/prompts/, cwd becomes the project root (or the
        # CARTOPIAN_LAUNCH_CWD override). Check that prompts-directory launch-cwd
        # detection is present in both shells.
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
                "bash wrappers missing prompts-directory launch-cwd detection: "
                + ", ".join(str(p.relative_to(REPO_ROOT)) for p in bash_hits)
            ),
        )
        self.assertEqual(
            ps1_hits,
            [],
            msg=(
                "ps1 wrappers missing prompts-directory launch-cwd detection: "
                + ", ".join(str(p.relative_to(REPO_ROOT)) for p in ps1_hits)
            ),
        )

    def test_wrappers_readme_documents_launch_contract(self) -> None:
        text = WRAPPERS_README.read_text(encoding="utf-8")
        self.assertIn("Cartopian project root", text)
        self.assertRegex(text, r"projects/<project-id>")
        # The README documents the neutral-launcher model: gating is the
        # harness's job, not the wrapper's.
        self.assertIn("neutral launcher", text)


class WaitPrimitiveStaticCoverageTest(unittest.TestCase):
    """Wait-primitive integration into skills and § Handoffs.

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

    # A bounded observation slice is internal to an already-initiated run.  It
    # must never be documented as a reason to hand control to the operator.
    # Automatic host wake/resume is allowed, but operator confirmation is not.
    FORBIDDEN_NONTERMINAL_YIELD_PATTERNS = (
        r"on [`']?still[-_]running[`']?[^.\n]*yield control back to the operator",
        r"yield control back to the operator[^.\n]*re-call",
        r"operator continuation[^.\n]*still[-_]running",
    )

    # Routine nonterminal slices are silent.  In particular, changing the old
    # operator-yield instruction into a per-slice commentary mandate is still
    # a contract violation because it accumulates user-visible context while
    # no material handoff state has changed.
    FORBIDDEN_ROUTINE_SLICE_OUTPUT_PATTERNS = (
        r"\bprovide(?:s)?\s+(?:concise\s+)?progress commentary\b",
        r"\b(?:must|should)\s+(?:provide|emit|send|write)\b[^.\n]*"
        r"(?:commentary|message|output|state)\b",
        r"\b(?:emit|send|write)\s+(?:a\s+)?(?:concise\s+)?progress "
        r"(?:commentary|message|update)\b",
    )

    SILENT_REWAIT_NEEDLES = (
        "silent and context-neutral",
        "without user-facing text",
        "terminal result",
        "blocker",
        "timeout/failure",
        "meaningful new progress evidence",
        "deliberately throttled long-running threshold",
        "automatic host wake/resume",
        "must not itself emit a user-visible message",
    )

    # The wait primitives are terminal/event-driven by default; a nonterminal
    # observation slice exists only when `--max-block` is explicitly requested
    # (a host-constraint opt-in), so no doc may present slicing as the default
    # calling convention.
    TERMINAL_DEFAULT_NEEDLES = (
        "terminal and event-driven by default",
        "explicit opt-in",
    )

    # A fenced wait command (the command template at line start) must not
    # carry `--max-block` — the documented default call is the terminal form;
    # the slice flag appears only in the opt-in prose and in bracketed
    # optional-argument synopses.
    FORBIDDEN_DEFAULT_SLICE_PATTERNS = (
        r"(?m)^\s*cartopian wait-(?:handoff|report)\b[^\n]*--max-block",
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

    def _assert_no_nonterminal_operator_yield(self, label: str, body: str) -> None:
        for pattern in self.FORBIDDEN_NONTERMINAL_YIELD_PATTERNS:
            self.assertIsNone(
                re.search(pattern, body, re.IGNORECASE),
                msg=(
                    f"{label} must keep an initiated run active across a "
                    f"nonterminal wait slice; stale operator-yield wording "
                    f"matched /{pattern}/"
                ),
            )

    def _assert_silent_rewait_contract(self, label: str, body: str) -> None:
        for pattern in self.FORBIDDEN_ROUTINE_SLICE_OUTPUT_PATTERNS:
            self.assertIsNone(
                re.search(pattern, body, re.IGNORECASE),
                msg=(
                    f"{label} must not require user-visible output for a "
                    f"routine nonterminal slice; matched /{pattern}/"
                ),
            )
        for needle in self.SILENT_REWAIT_NEEDLES:
            self.assertIn(
                needle,
                body,
                msg=f"{label} must pin the silent re-wait contract: {needle!r}",
            )

    def _assert_terminal_default_contract(self, label: str, body: str) -> None:
        for needle in self.TERMINAL_DEFAULT_NEEDLES:
            self.assertIn(
                needle,
                body,
                msg=(
                    f"{label} must pin the terminal-by-default wait contract: "
                    f"{needle!r}"
                ),
            )
        for pattern in self.FORBIDDEN_DEFAULT_SLICE_PATTERNS:
            self.assertIsNone(
                re.search(pattern, body),
                msg=(
                    f"{label} must document the terminal wait call as the "
                    f"default (no --max-block in the command template); "
                    f"matched /{pattern}/"
                ),
            )

    def test_run_handoff_uses_wait_primitives(self) -> None:
        text = self._read_skill("run-handoff.md")
        self.assertIn("cartopian wait-handoff", text)
        self.assertIn("cartopian wait-report", text)
        self.assertIn("still-running", text)
        self._assert_no_adhoc_polling("run-handoff.md", text)
        self._assert_no_nonterminal_operator_yield("run-handoff.md", text)
        self._assert_silent_rewait_contract("run-handoff.md", text)
        self._assert_terminal_default_contract("run-handoff.md", text)
        for needle in (
            "internal observation boundary",
            "same canonical wait primitive",
            "does not launch",
            "Only launches consume",
            "automatic host wake/resume",
        ):
            self.assertIn(needle, text)

    def test_run_task_uses_wait_handoff(self) -> None:
        text = self._read_skill("run-task.md")
        self.assertIn("cartopian wait-handoff", text)
        self._assert_no_adhoc_polling("run-task.md", text)
        self._assert_no_nonterminal_operator_yield("run-task.md", text)
        self._assert_silent_rewait_contract("run-task.md", text)
        self._assert_terminal_default_contract("run-task.md", text)
        self.assertIn("same canonical wait primitive", text)
        self.assertIn("does not consume", text)

    def test_plan_project_uses_wait_report(self) -> None:
        text = self._read_skill("plan-project.md")
        self.assertIn("cartopian wait-report", text)
        self._assert_no_adhoc_polling("plan-project.md", text)
        self._assert_no_nonterminal_operator_yield("plan-project.md", text)
        self._assert_silent_rewait_contract("plan-project.md", text)
        self._assert_terminal_default_contract("plan-project.md", text)
        self.assertIn("same canonical wait primitive", text)
        self.assertIn("does not consume", text)

    def test_conventions_handoffs_formalizes_wait_contract(self) -> None:
        section = self._handoffs_section()
        # Wait commands replace ad-hoc polling.
        self.assertIn("cartopian wait-handoff", section)
        self.assertIn("cartopian wait-report", section)
        # Report file is authoritative; the wrapper status file is optional.
        self.assertRegex(section, r"authoritative")
        self.assertRegex(section, r"\.status|status file")
        # still-running is an internal observation boundary, not an operator
        # confirmation boundary.
        self.assertIn("still-running", section)
        for needle in (
            "internal observation boundary",
            "same canonical wait primitive",
            "Only launches consume",
            "automatic host wake/resume",
        ):
            self.assertIn(needle, section)
        self.assertRegex(section, r"each-handoff.*terminal result.*processed")
        self.assertRegex(section, r"until-blocked.*remains active")
        self._assert_no_adhoc_polling("CONVENTIONS.md § Handoffs", section)
        self._assert_no_nonterminal_operator_yield(
            "CONVENTIONS.md § Handoffs", section
        )
        self._assert_silent_rewait_contract("CONVENTIONS.md § Handoffs", section)
        self._assert_terminal_default_contract("CONVENTIONS.md § Handoffs", section)
        # The host-constraint escape hatch is a wake mechanism, not skill
        # wording: a host that cannot sustain the terminal call must supply a
        # filesystem-change wake (or equivalent) to re-invoke the wait.
        self.assertIn("filesystem-change wake", section)


class SectionUriStaticCoverageTest(unittest.TestCase):
    """Benchmark-critical skills read section-scoped CONVENTIONS URIs.

    The MCP server exposes `cartopian://protocol/CONVENTIONS/<section-slug>`
    (one H2 section per resource) plus the curated `/startup` slice so the PM
    loads only the protocol slice a given moment needs. These assertions pin
    the narrower reads in the benchmark-critical lifecycle skills:

    - each skill names the section URIs its stages depend on;
    - the startup path uses the curated startup slice;
    - no whole-doc protocol read sneaks back in (the only allowed whole-doc
      mention is the "remains the authoritative contract" disclaimer);
    - every referenced section slug resolves to a real H2 section of
      CONVENTIONS.md, so a protocol heading rename cannot silently break a
      skill's reference.
    """

    # Skills in the benchmark-critical lifecycle loop and the section URIs
    # each must reference.
    REQUIRED_SECTION_URIS = {
        "run-task.md": (
            "cartopian://protocol/CONVENTIONS/status-through-directory",
            "cartopian://protocol/CONVENTIONS/lifecycle-authority",
            "cartopian://protocol/CONVENTIONS/lifecycle-cli-guards",
            "cartopian://protocol/CONVENTIONS/handoffs",
            "cartopian://protocol/CONVENTIONS/evidence-gate-discipline",
            "cartopian://protocol/CONVENTIONS/git",
        ),
        "run-handoff.md": (
            "cartopian://protocol/CONVENTIONS/handoffs",
            "cartopian://protocol/CONVENTIONS/roles",
        ),
        "plan-project.md": (
            "cartopian://protocol/CONVENTIONS/roles",
            "cartopian://protocol/CONVENTIONS/reviews",
            "cartopian://protocol/CONVENTIONS/plan-lifecycle",
            "cartopian://protocol/CONVENTIONS/session-state",
        ),
        "close-plan.md": (
            "cartopian://protocol/CONVENTIONS/plan-lifecycle",
            "cartopian://protocol/CONVENTIONS/plan-archives",
            "cartopian://protocol/CONVENTIONS/session-state",
            "cartopian://protocol/CONVENTIONS/git",
        ),
    }

    # The startup path reads the curated slice, not the whole document.
    STARTUP_SKILLS = ("use-cartopian.md", "start-session.md")
    STARTUP_URI = "cartopian://protocol/CONVENTIONS/startup"

    # A concrete section reference: URI followed by a literal slug (the
    # `<section-slug>` placeholder in prose intentionally does not match).
    _SECTION_REF_RE = re.compile(r"cartopian://protocol/CONVENTIONS/([a-z0-9-]+)")
    # A whole-doc mention: the bare URI not followed by a section path, or the
    # raw file-path spelling.
    _WHOLE_DOC_RE = re.compile(
        r"cartopian://protocol/CONVENTIONS(?![/a-z0-9-])|protocol/CONVENTIONS\.md"
    )

    def _read_skill(self, name: str) -> str:
        return (SKILLS_DIR / name).read_text(encoding="utf-8")

    @staticmethod
    def _conventions_slugs() -> set:
        """H2 section slugs of CONVENTIONS.md, slugified as the server does."""
        text = (PROTOCOL_DIR / "CONVENTIONS.md").read_text(encoding="utf-8")
        slugs = set()
        for match in re.finditer(r"^## (.+?)\s*$", text, re.MULTILINE):
            slugs.add(re.sub(r"[^a-z0-9]+", "-", match.group(1).lower()).strip("-"))
        return slugs

    def test_lifecycle_skills_reference_required_section_uris(self) -> None:
        for skill, uris in self.REQUIRED_SECTION_URIS.items():
            text = self._read_skill(skill)
            for uri in uris:
                self.assertIn(
                    uri, text,
                    msg=f"{skill} must reference the section-scoped read `{uri}`",
                )

    def test_startup_path_uses_startup_slice(self) -> None:
        for skill in self.STARTUP_SKILLS:
            self.assertIn(
                self.STARTUP_URI,
                self._read_skill(skill),
                msg=f"{skill} must read the curated startup slice `{self.STARTUP_URI}`",
            )

    def test_no_whole_doc_protocol_reads_in_benchmark_skills(self) -> None:
        # A whole-doc CONVENTIONS mention is allowed only as the standing
        # "remains the authoritative contract" disclaimer — never as a read
        # instruction. Any other whole-doc mention is a token-burn backslide.
        for skill in self.REQUIRED_SECTION_URIS:
            text = self._read_skill(skill)
            offending = [
                line.strip()
                for line in text.splitlines()
                if self._WHOLE_DOC_RE.search(line)
                and "authoritative" not in line.lower()
            ]
            self.assertEqual(
                offending,
                [],
                msg=(
                    f"{skill} re-introduces a whole-doc CONVENTIONS read; cite a "
                    f"section via cartopian://protocol/CONVENTIONS/<section-slug> "
                    f"instead: {offending}"
                ),
            )

    def test_referenced_section_slugs_resolve(self) -> None:
        # Every concrete section slug cited by any skill must be a real H2
        # section of CONVENTIONS.md (or the reserved `startup` slice), so a
        # protocol heading rename cannot silently orphan a skill reference.
        valid = self._conventions_slugs() | {"startup"}
        for path in sorted(SKILLS_DIR.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            unknown = sorted(
                {
                    slug
                    for slug in self._SECTION_REF_RE.findall(text)
                    if slug not in valid
                }
            )
            self.assertEqual(
                unknown,
                [],
                msg=(
                    f"{path.name} references CONVENTIONS section slugs that do "
                    f"not resolve to an H2 heading: {unknown}"
                ),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
