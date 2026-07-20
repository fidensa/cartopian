"""Residual-raw-op check for the rewired lifecycle skills.

Phase 01 exit criterion: "the full lifecycle runs under containment with no
deadlock." A *contained* PM has no shell and no raw ``Write``/``Edit`` tool,
so every **PM-performed** lifecycle action must route through a mediated
Cartopian command:

- launches → ``cartopian dispatch``;
- artifact authoring → ``cartopian write-*``;
- close-surface reset / reseed → ``cartopian reset-plan``;
- the already-mediated ``move-task`` / ``delete-prompt`` / ``delete-report`` /
  ``compose-state`` family.

This module enforces that contract over the seven lifecycle skills in two
complementary layers:

``MediatedCommandPresenceTest``
    Each skill must *name* the mediated command for every PM-performed action
    it drives. This is the primary red→green signal: before the rewire the
    ``write-*`` / ``dispatch`` / ``reset-plan`` commands are absent from the
    runbooks, so these assertions fail; after the rewire they pass.

``ResidualRawOpTest``
    A line scan asserting that no **PM-performed** step still instructs a raw
    subprocess launch or a raw ``Write``/``Edit`` of a Cartopian artifact. The
    scan is the regression guard: it flags a line only when it pairs a write
    verb with a PM-authored artifact token (or matches a raw-launch pattern)
    *and* does not itself name a mediated command *and* is not directed at the
    non-contained assignee/reviewer (who legitimately writes code, reports, and
    review files with their own tools — PROMPT scope boundary).

Scope notes (deliberate exclusions, mirrored in the scan):
- ``reviews/REVIEW-*`` and ``reports/REPORT-*`` are authored by the
  reviewer/assignee, not the PM, and have no PM ``write-*`` command — they are
  not flagged.
- Product-repository git plumbing remains outside this check. Optional plan
  archival is PM-owned and routes through ``cartopian archive-plan``.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"

# The seven lifecycle skills the PROMPT names for rewire.
LIFECYCLE_SKILLS = (
    "run-handoff.md",
    "run-task.md",
    "plan-project.md",
    "start-session.md",
    "close-plan.md",
    "adopt-plan.md",
    "adopt-requirements.md",
)

# Mediated commands each skill's PM-performed steps must name after the rewire.
# (Existing static-coverage needles live in test_p02_build_010; these are the
# additive launch/authoring/reset commands this task lands in the runbooks.)
REQUIRED_MEDIATED: dict[str, tuple[str, ...]] = {
    "run-handoff.md": (
        "cartopian dispatch",
        "cartopian write-prompt",
    ),
    "run-task.md": (
        "cartopian write-prompt",
        "cartopian write-decision",
        "cartopian write-state",
    ),
    "plan-project.md": (
        "cartopian write-requirements",
        "cartopian write-standards",
        "cartopian write-plan",
        "cartopian write-phase",
        "cartopian write-task",
        "cartopian write-spec",
        "cartopian write-state",
        "cartopian write-prompt",
    ),
    "start-session.md": (
        "cartopian write-state",
    ),
    "close-plan.md": (
        "cartopian archive-plan",
        "cartopian reset-plan",
        "cartopian write-state",
    ),
    "adopt-plan.md": (
        "cartopian write-plan",
        "cartopian write-phase",
        "cartopian write-task",
        "cartopian write-spec",
        "cartopian write-state",
    ),
    "adopt-requirements.md": (
        "cartopian write-requirements",
        "cartopian write-standards",
        "cartopian write-state",
    ),
}


# --- residual-raw-op scan patterns ----------------------------------------

# Cartopian artifacts the PM authors via a `write-*` command. Both the literal
# placeholder spellings the skills use (TASK-NN-NNN, PHASE-NN-slug, PROMPT-PLAN-
# NNN, DEC-NNN) and concrete-id spellings are matched, in path and bare forms.
# reviews/REVIEW-* and reports/REPORT-* are intentionally NOT here: they are
# authored by the non-contained reviewer/assignee, not the PM.
_ARTIFACT = re.compile(
    r"`?(?:"
    r"REQUIREMENTS\.md"
    r"|IMPLEMENTATION_PLAN\.md"
    r"|STANDARDS\.md"
    r"|STATE\.md"
    r"|phases/PHASE|PHASE-(?:NN|\d{2})-"
    r"|tasks/open/TASK|TASK-(?:NN-NNN|\d{2}-\d{3})"
    r"|specs/SPEC|SPEC-(?:NN-NNN|\d{2}-\d{3})"
    r"|prompts/PROMPT|PROMPT-(?:NN-NNN|PLAN|\d{2}-\d{3})"
    r"|decisions/DEC|DEC-(?:NNN|\d{3})"
    r")"
)

# A raw authoring verb in IMPERATIVE position (the start of the instruction,
# after any list marker / leading subordinate clause — see `_LEADING`). Keying
# on imperative position is what separates a PM authoring *instruction*
# ("Write `IMPLEMENTATION_PLAN.md` …", "create `tasks/open/TASK-…`") from mere
# descriptive or conditional prose that happens to mention an artifact and a
# verb ("If `REQUIREMENTS.md` exists, generate the coverage matrix",
# "Do not … rewrite `STATE.md`"). `update`/`remove`/`copy`/`read` are excluded:
# the first is ambiguous, the rest are not raw artifact-authoring.
_RAW_WRITE_VERB_START = re.compile(
    r"^(?:write|create|generate|author|rewrite|replace|produce|populate|reseed)\b",
    re.IGNORECASE,
)

# Strips a leading list/quote marker and any run of leading subordinate clauses
# ("For each phase in the plan, ", "For tasks that need specs, ") so the verb
# that begins the actual instruction lands at position 0. A backtick ends a
# clause run (so "If `REQUIREMENTS.md` exists, …" is NOT treated as a strippable
# lead — the conditional stays and the line is correctly not flagged).
_LEADING = re.compile(r"^(?:[-*>]\s+|\d+\.\s+)?(?:[^,`\n]+,\s+)*")

# A PM-performed raw subprocess launch of an assignee wrapper — the legacy
# `CARTOPIAN_TIMEOUT=<duration> <agent> …` launch contract and its prose.
_RAW_LAUNCH = re.compile(
    r"CARTOPIAN_TIMEOUT=\S+\s+<agent>"
    r"|launch the configured executable"
    r"|launch the handoff as a background subprocess",
    re.IGNORECASE,
)

# A line that names a mediated command is, by construction, the rewired path.
_MEDIATED = re.compile(
    r"cartopian\s+(?:write-[a-z]+|dispatch|archive-plan|reset-plan|move-task"
    r"|delete-prompt|delete-report|compose-state|handoff-packet)"
)

# Steps directed at the NON-contained assignee/reviewer/coder. These agents
# write code, completion reports, and review files with their own tools; the
# PROMPT scope boundary says to leave them unchanged, so they are not flagged.
_ASSIGNEE = re.compile(r"\b(?:assignee|assignees|reviewer|reviewers|coder)\b", re.IGNORECASE)

_OUT_OF_SCOPE_OTHER = re.compile(r"reviews/REVIEW|reports/REPORT|INDEX\.md", re.IGNORECASE)


def _is_heading(line: str) -> bool:
    return bool(re.match(r"\s*#{1,6}\s", line))


class MediatedCommandPresenceTest(unittest.TestCase):
    """Every PM-performed action in a lifecycle skill names its mediated command."""

    def _read(self, name: str) -> str:
        return (SKILLS_DIR / name).read_text(encoding="utf-8")

    def test_required_mediated_commands_present(self) -> None:
        missing: list[str] = []
        for skill, needles in REQUIRED_MEDIATED.items():
            text = self._read(skill)
            for needle in needles:
                if needle not in text:
                    missing.append(f"{skill} must name `{needle}`")
        self.assertEqual(missing, [], msg="missing mediated-command references:\n  " + "\n  ".join(missing))


class ResidualRawOpTest(unittest.TestCase):
    """No PM-performed raw launch or raw artifact Write/Edit survives."""

    def _offending_lines(self, text: str) -> list[tuple[int, str]]:
        offending: list[tuple[int, str]] = []
        for idx, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or _is_heading(raw):
                continue
            if _MEDIATED.search(line):
                continue  # rewired path — names a mediated command
            if _ASSIGNEE.search(line):
                continue  # assignee/reviewer instruction — not PM-performed
            if _OUT_OF_SCOPE_OTHER.search(line):
                continue  # reviewer-or-assignee output / index table
            raw_launch = _RAW_LAUNCH.search(line)
            imperative = _LEADING.sub("", line, count=1)
            raw_write = bool(_RAW_WRITE_VERB_START.match(imperative)) and bool(_ARTIFACT.search(imperative))
            if raw_launch or raw_write:
                offending.append((idx, line))
        return offending

    def test_no_residual_pm_raw_ops(self) -> None:
        hits: list[str] = []
        for skill in LIFECYCLE_SKILLS:
            text = (SKILLS_DIR / skill).read_text(encoding="utf-8")
            for line_no, line in self._offending_lines(text):
                hits.append(f"{skill}:{line_no}: {line}")
        self.assertEqual(
            hits,
            [],
            msg=(
                "PM-performed raw launch / raw artifact Write-Edit instructions "
                "remain (route them through a mediated command):\n  " + "\n  ".join(hits)
            ),
        )

    def test_scan_detects_a_raw_write_and_clears_when_mediated(self) -> None:
        # Bite check: the scan must flag a raw PM write and accept the mediated
        # rewrite of the same step. Operates on synthetic text.
        raw = "Write `IMPLEMENTATION_PLAN.md` in the project directory with the plan body."
        mediated = "Author the plan via `cartopian write-plan <project-path> --content-file <path>`."
        assignee = "The assignee writes `reports/REPORT-NN-NNN.md` as the completion report."
        self.assertNotEqual(self._offending_lines(raw), [], "raw PM write must be flagged")
        self.assertEqual(self._offending_lines(mediated), [], "mediated rewrite must pass")
        self.assertEqual(self._offending_lines(assignee), [], "assignee write must not be flagged")


class ArchiveContainmentBoundaryTest(unittest.TestCase):
    """Close-plan archival stays PM-owned and mediated."""

    def _read(self, name: str) -> str:
        return (SKILLS_DIR / name).read_text(encoding="utf-8")

    def _stage3_region(self, text: str) -> str:
        m = re.search(
            r"^##\s*Stage 3 - Optional Archive\b(.*?)^##\s*Stage 4\b",
            text,
            re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(
            m, "close-plan.md must have a Stage 3 (Optional Archive) followed by a Stage 4"
        )
        return m.group(1)

    def test_archive_stage_is_pm_owned_and_mediated(self) -> None:
        region = self._stage3_region(self._read("close-plan.md")).lower()
        for needle in (
            "pm-performed",
            "cartopian archive-plan",
            "do not hand raw create/copy/index steps to the operator",
        ):
            self.assertIn(
                needle,
                region,
                msg=(
                    "close-plan.md Stage 3 must keep archival PM-owned and mediated "
                    f"(missing: {needle!r})"
                ),
            )

    def test_raw_archive_write_is_flagged(self) -> None:
        scan = ResidualRawOpTest("test_no_residual_pm_raw_ops")
        line = "Write `archive/PLAN-001-slug/STATE.md` as the closeout snapshot of STATE.md."
        self.assertNotEqual(
            scan._offending_lines(line),
            [],
            "a raw PM archive write must be routed through archive-plan",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
