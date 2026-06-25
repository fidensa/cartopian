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


# --- PM-containment wrapper class -------------------------------------------
# The contained Claude Code PM wrapper (`cartopian-claude-pm`) is NOT a blanket
# Cartopian launcher. It exposes ONLY the fixed Cartopian MCP toolset and
# DELIBERATELY OMITS the two blanket-wrapper properties asserted by
# WrappersStaticCoverageTest below:
#   * it has NO `CARTOPIAN_*_UNRESTRICTED` bypass — a bypass is a containment
#     hole, forbidden by the floor; and
#   * it launches from an isolated, content-free `pm-surface` cwd, NOT the
#     project root via prompts-dir detection — the product repo and work roots
#     must stay unreachable.
# Those omissions are correct, so the blanket assertions EXCLUDE this class. To
# stop the exclusion from silently masking a regression, PmContainmentWrapper-
# StaticCoverageTest turns it into an enforced *opposite* contract.
# See decisions/DEC-001-pm-containment-claude-code-go.md.

PM_CONTAINMENT_MARKER = "PM containment"  # in-file self-identification


def _is_pm_containment_wrapper(path: Path) -> bool:
    """True for the PM-containment wrapper class.

    Recognized by BOTH a `-pm` filename suffix (`cartopian-*-pm`, optionally
    `.ps1`) AND an in-file containment marker, so a plain launcher cannot be
    excluded by name alone and the contained wrapper cannot shed its identity
    silently.
    """
    stem = path.name[:-4] if path.name.endswith(".ps1") else path.name
    if not stem.endswith("-pm"):
        return False
    return PM_CONTAINMENT_MARKER in path.read_text(encoding="utf-8")


class WrappersStaticCoverageTest(unittest.TestCase):
    def test_wrappers_call_resolve_config_and_offer_unrestricted_bypass(self) -> None:
        # Every blanket wrapper (bash and PowerShell) must wire up the work-root
        # access guard (`cartopian resolve-config`) and expose a
        # CARTOPIAN_*_UNRESTRICTED env-var bypass. The bash wrappers factor the
        # resolve-config + fail-closed guard into the shared helper
        # (_cartopian-status.sh :: cartopian_enforce_work_roots) so the guard
        # cannot rot per-wrapper; they wire it by calling that helper. The
        # PowerShell wrappers still inline `cartopian resolve-config`.
        # PM-containment wrappers are excluded: they run MCP-only and must have
        # NO bypass — see PmContainmentWrapperStaticCoverageTest for their
        # inverse contract.
        wrappers = []
        wrappers.extend(sorted(WRAPPERS_BIN_DIR.glob("cartopian-*")))
        wrappers.extend(sorted(WRAPPERS_PS1_DIR.glob("cartopian-*.ps1")))
        wrappers = [p for p in wrappers if not _is_pm_containment_wrapper(p)]
        self.assertTrue(wrappers, "expected wrapper scripts to exist")

        # The factored guard's resolve-config call must genuinely exist in the
        # shared helper each shell sources: bash's _cartopian-status.sh and
        # PowerShell's CartopianStatus.ps1.
        helper_text = (WRAPPERS_BIN_DIR / "_cartopian-status.sh").read_text(encoding="utf-8")
        self.assertIn(
            "cartopian resolve-config", helper_text,
            msg="shared helper _cartopian-status.sh must call `cartopian resolve-config`",
        )
        ps1_helper_text = (WRAPPERS_PS1_DIR / "CartopianStatus.ps1").read_text(encoding="utf-8")
        self.assertIn(
            "cartopian resolve-config", ps1_helper_text,
            msg="shared helper CartopianStatus.ps1 must call `cartopian resolve-config`",
        )

        missing_resolve = []
        missing_unrestricted = []
        for path in wrappers:
            text = path.read_text(encoding="utf-8")
            # A wrapper references the guard either inline (`cartopian
            # resolve-config`, the gemini PS1 wrapper) or by calling the
            # shared-helper entry point that performs it
            # (`cartopian_enforce_work_roots` in bash, `Get-CartopianScopeArgs`
            # in PowerShell).
            if (
                "cartopian resolve-config" not in text
                and "cartopian_enforce_work_roots" not in text
                and "Get-CartopianScopeArgs" not in text
            ):
                missing_resolve.append(path)
            if not re.search(r"CARTOPIAN_[A-Z]+_UNRESTRICTED", text):
                missing_unrestricted.append(path)
        self.assertEqual(
            missing_resolve,
            [],
            msg=(
                "wrappers missing work-root guard wiring (`cartopian resolve-config` "
                "or `cartopian_enforce_work_roots`): "
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
        # PM-containment wrappers are excluded: they launch from an isolated
        # `pm-surface` cwd, NOT the project root — that isolated launch is
        # positively enforced by PmContainmentWrapperStaticCoverageTest.
        bash_hits = []
        for path in sorted(WRAPPERS_BIN_DIR.glob("cartopian-*")):
            if _is_pm_containment_wrapper(path):
                continue
            text = path.read_text(encoding="utf-8")
            if not re.search(r"basename\s*\(\"\$PROMPTS_DIR\"\)\"?\s*\)\s*==\s*\"prompts\"", text):
                bash_hits.append(path)
        ps1_hits = []
        for path in sorted(WRAPPERS_PS1_DIR.glob("cartopian-*.ps1")):
            if _is_pm_containment_wrapper(path):
                continue
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

    def test_wrappers_readme_documents_fr012_and_oq009(self) -> None:
        text = WRAPPERS_README.read_text(encoding="utf-8")
        self.assertIn("Cartopian project root", text)
        self.assertRegex(text, r"projects/<project-id>")
        # Work-root access model mention
        self.assertTrue(
            ("work-root" in text) or ("work_roots" in text),
            msg="wrappers/README.md should mention work-root access model",
        )


class PmContainmentWrapperStaticCoverageTest(unittest.TestCase):
    """Inverse-guarantee contract for the PM-containment wrapper class.

    WrappersStaticCoverageTest EXCLUDES these wrappers from the blanket
    `offer-unrestricted-bypass` and `project-root launch` assertions because, by
    design, a contained PM wrapper intentionally lacks both. This class enforces
    the *opposite* contract so the exclusion can never silently mask a floor
    regression: a PM-containment wrapper MUST NOT grow a bypass or a
    surface-reopening flag (`--add-dir`/`--dangerously-skip-permissions`), and
    MUST launch from its isolated surface rather than the project root.
    """

    # Identifiers whose value is the project root, a work root, or the prompts
    # directory. Deriving or `cd`-ing a launch cwd from ANY of them re-opens the
    # surface the PM-containment floor is meant to keep unreachable. Both the
    # bash (`PROMPTS_DIR`) and PowerShell (`$PromptsDir`) spellings are covered;
    # IGNORECASE folds case but NOT the underscore vs. camelCase split, so both
    # forms are listed explicitly. CARTOPIAN_PM_SURFACE / $PM_SURFACE are NOT in
    # this set — launching from the isolated surface is exactly what's required.
    _LAUNCH_SOURCE_RE = re.compile(
        r"(?<![\w-])"
        r"(?:PROJECT_DIR|PROJECT_ROOT|PROMPTS_DIR|LAUNCH_CWD|CARTOPIAN_LAUNCH_CWD"
        r"|PromptsDir|ProjectDir|ProjectRoot|LaunchCwd)"
        r"(?![\w-])",
        re.IGNORECASE,
    )
    # Prompts-dir launch DETECTION used to choose the cwd, e.g.
    # `[[ $(basename "$PROMPTS_DIR") == "prompts" ]]` (bash) or
    # `(Split-Path -Leaf $PromptsDir) -eq 'prompts'` (PowerShell). This is the
    # Prompts-dir launch-detection trigger that a contained PM must never perform.
    _PROMPTS_DETECT_RE = re.compile(
        r"""(?:==|-eq)\s*["']?prompts["']?""",
        re.IGNORECASE,
    )

    def _pm_wrappers(self) -> list:
        wrappers = []
        wrappers.extend(sorted(WRAPPERS_BIN_DIR.glob("cartopian-*")))
        wrappers.extend(sorted(WRAPPERS_PS1_DIR.glob("cartopian-*.ps1")))
        return [p for p in wrappers if _is_pm_containment_wrapper(p)]

    @classmethod
    def _offending_launch_lines(cls, text: str) -> list:
        """Executable lines that derive/`cd` a launch cwd from the project root,
        a work root, or the prompts dir — i.e. that would re-open the surface.

        Mirrors `_offending_flag_lines`: comments and purely diagnostic /
        refusal / validation lines may *name* these locations (an `echo`/
        `Write-*` message, a comment) without launching from them, and are
        allowed. A line counts as offending only when it actually references a
        project-root/prompts launch-source identifier (`_LAUNCH_SOURCE_RE`) or
        performs prompts-dir launch detection (`_PROMPTS_DETECT_RE`) in an
        executable position — catching ANY project-root/prompts-dir launch
        path, not just the one old static `== "prompts"` pattern.
        """
        # Lines that merely report or refuse: they may name a location without
        # ever changing the launch cwd to it.
        diag_starts = (
            "echo ", "echo\t", "echo>", "printf", "print ",
            "write-host", "write-error", "write-warning", "write-output",
            "write-verbose", "write-debug",
        )
        offending = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue  # documentation / comment
            if line.lower().startswith(diag_starts):
                continue  # diagnostic / refusal message — names, never launches
            if cls._LAUNCH_SOURCE_RE.search(line) or cls._PROMPTS_DETECT_RE.search(line):
                offending.append(line)
        return offending

    @staticmethod
    def _offending_flag_lines(text: str, flag: str) -> list:
        """Lines that PASS `flag` to the launcher (not comments/refusal guard).

        A PM wrapper legitimately *names* `--add-dir` /
        `--dangerously-skip-permissions` in comments and in the refusal guard
        that rejects them. Those occurrences are allowed; an occurrence on an
        executable line that actually hands the flag to `claude` is not.
        """
        offending = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue  # documentation / comment
            if flag not in line:
                continue
            lowered = line.lower()
            # Allowed only where the flag is REFUSED, not launched: the refusal
            # `case` pattern (a `| --…` alternation or a trailing-`\` line) or an
            # echo describing the refusal.
            if "refus" in lowered or "echo" in lowered or "| --" in line or line.endswith("\\"):
                continue
            offending.append(line)
        return offending

    def test_pm_containment_class_is_non_empty(self) -> None:
        # Anti-vacuity guard: if the `-pm` suffix / in-file marker convention
        # ever stops matching, the exclusions in WrappersStaticCoverageTest would
        # silently cover nothing and this inverse contract would pass vacuously.
        self.assertTrue(
            self._pm_wrappers(),
            "expected at least one PM-containment wrapper (cartopian-*-pm); the "
            "blanket-test exclusions would otherwise be vacuous",
        )

    def test_pm_wrappers_have_no_unrestricted_bypass(self) -> None:
        # A CARTOPIAN_*_UNRESTRICTED bypass is a containment hole and
        # must not exist in the contained PM profile.
        offenders = []
        for path in self._pm_wrappers():
            if re.search(r"CARTOPIAN_[A-Z]+_UNRESTRICTED", path.read_text(encoding="utf-8")):
                offenders.append(path)
        self.assertEqual(
            offenders,
            [],
            msg=(
                "PM-containment wrappers must NOT expose a CARTOPIAN_*_UNRESTRICTED "
                "bypass: " + ", ".join(str(p.relative_to(REPO_ROOT)) for p in offenders)
            ),
        )

    def test_pm_wrappers_do_not_add_surface_reopening_flags(self) -> None:
        # --add-dir / --dangerously-skip-permissions re-open the surface.
        # The wrapper may name them only to refuse them.
        for path in self._pm_wrappers():
            text = path.read_text(encoding="utf-8")
            for flag in ("--add-dir", "--dangerously-skip-permissions"):
                offending = self._offending_flag_lines(text, flag)
                self.assertEqual(
                    offending,
                    [],
                    msg=(
                        f"{path.relative_to(REPO_ROOT)} passes `{flag}` to the "
                        f"launcher outside the refusal guard: {offending}"
                    ),
                )

    def test_pm_wrappers_launch_from_isolated_surface(self) -> None:
        # The contained PM launches from an isolated, content-free surface and
        # must NOT derive its launch cwd from the project root, a work root, or
        # the prompts dir. Two halves: (1) positively require the
        # launch-from-$PM_SURFACE path, and (2) reject ANY executable
        # project-root/prompts-dir launch path via `_offending_launch_lines`,
        # not just the single old static `== "prompts"` regex.
        for path in self._pm_wrappers():
            text = path.read_text(encoding="utf-8")
            with self.subTest(wrapper=str(path.relative_to(REPO_ROOT))):
                if path.name.endswith(".ps1"):
                    self.assertRegex(text, r"(?i)surface")
                    self.assertIn("Set-Location", text)
                else:
                    self.assertRegex(
                        text, r'cd\s+"\$PM_SURFACE"',
                        "bash PM wrapper must cd into the isolated $PM_SURFACE",
                    )
                    self.assertRegex(
                        text, r"pm-surface",
                        "bash PM wrapper must default to an isolated pm-surface cwd",
                    )
                # Inverse half: no executable line may derive/cd a launch cwd
                # from PROJECT_DIR/PROJECT_ROOT/a PROMPTS_DIR parent, nor perform
                # prompts-dir launch detection to choose the cwd. This supersedes
                # the old narrow `basename("$PROMPTS_DIR") == "prompts"` regex,
                # which is just one shape this now rejects.
                offending = self._offending_launch_lines(text)
                self.assertEqual(
                    offending,
                    [],
                    msg=(
                        f"{path.relative_to(REPO_ROOT)} derives a launch cwd from "
                        "the project root / work root / prompts dir (the product "
                        "repo must stay unreachable in the contained PM profile): "
                        f"{offending}"
                    ),
                )

    def test_isolated_surface_assertion_bites_project_root_launch(self) -> None:
        # Bite check, parallel to the bypass / --add-dir guards: prove the
        # strengthened inverse assertion actually fails for a realistically
        # shaped project-root launch mutation — including the one the reviewer
        # flagged as slipping past the old narrow regex — and passes once the
        # mutation is reverted. Operates on synthetic text so the live wrappers
        # are never mutated on disk.
        clean = (
            '#!/usr/bin/env bash\n'
            '# PM containment launch profile.\n'
            'PM_SURFACE="${CARTOPIAN_PM_SURFACE:-/var/pm-surface}"\n'
            'cd "$PM_SURFACE"\n'
            'exec claude "${FLOOR[@]}" "$@"\n'
        )
        self.assertEqual(
            self._offending_launch_lines(clean),
            [],
            "the isolated-surface launch profile must be accepted",
        )

        # Each mutation is an executable project-root/prompts-dir launch path
        # that the OLD narrow regex would have missed but the strengthened
        # assertion must catch.
        mutations = {
            # The reviewer's exact example: a later guarded project-root launch.
            "guarded-project-root": (
                'if [[ $(basename "$PROMPTS_DIR") == "prompts" ]]; then cd "$PROJECT_DIR"; fi\n'
            ),
            # Bare cd to the project root.
            "bare-project-root": 'cd "$PROJECT_DIR"\n',
            # Launch-cwd override derivation.
            "launch-cwd-override": 'LAUNCH_CWD="$CARTOPIAN_LAUNCH_CWD"; cd "$LAUNCH_CWD"\n',
            # Deriving the prompts-dir parent (the blanket-launcher pattern).
            "prompts-dir-parent": 'PROJECT_DIR="$(dirname "$PROMPTS_DIR")"\n',
            # PowerShell prompts-dir detection.
            "ps1-prompts-detect": "if ((Split-Path -Leaf $PromptsDir) -eq 'prompts') { Set-Location $ProjectDir }\n",
        }
        for name, mutation in mutations.items():
            with self.subTest(mutation=name):
                self.assertNotEqual(
                    self._offending_launch_lines(clean + mutation),
                    [],
                    f"strengthened assertion must bite the {name} project-root launch",
                )

        # A diagnostic line that merely NAMES the project root (never launches
        # from it) must still be allowed — the wrapper legitimately reports/
        # refuses without re-opening the surface.
        self.assertEqual(
            self._offending_launch_lines(
                clean + 'echo "cartopian-claude-pm: PROJECT_DIR is unreachable" >&2\n'
            ),
            [],
            "a diagnostic that only names the project root must not be flagged",
        )


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
