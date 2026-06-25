"""Residual-pattern grep for the source-of-truth rewrite.

Asserts that the rewrite (``protocol/CONVENTIONS.md``
and ``templates/*.md``) carries none of the retired-model vocabulary, and
that the renamed ``Work root:`` field is present in ``templates/TASK.md``.

Scope: ``protocol/CONVENTIONS.md`` plus every ``templates/*.md`` file.
``protocol/CHANGELOG.md`` is explicitly excluded — its breakage
descriptions and migration steps must spell the retired terms verbatim.

Retired patterns:
- ``parent-of-workspace-root`` / ``parent of the workspace root`` —
  the old launch-cwd rule retired in favor of the cartopian project root.
- ``Repo subpath:`` — the retired task-file header renamed to
  ``Work root:``.
- The legacy ``projects/`` directory-scan project-selection model
  retired in favor of registry-only selection.
  Tells: a session-startup step that lists or scans child directories
  under ``projects/``, or a working-directory rule that resolves a
  project by being inside ``projects/<project-id>/``.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONVENTIONS = REPO_ROOT / "protocol" / "CONVENTIONS.md"
TEMPLATES_DIR = REPO_ROOT / "templates"


def _in_scope_files() -> list[Path]:
    files = [CONVENTIONS]
    files.extend(sorted(p for p in TEMPLATES_DIR.glob("*.md")))
    return files


# Retired-model patterns. Each pattern is a compiled regex; matching is
# multiline so anchors apply per line. Patterns are intentionally narrow:
# they target the retired model's distinctive wording so legitimate
# discussion of unrelated terms is not flagged.
_RETIRED_PATTERNS: dict[str, re.Pattern[str]] = {
    "parent-of-workspace-root": re.compile(
        r"parent[- ]of[- ]the[- ]workspace[- ]root|parent-of-workspace-root",
        re.IGNORECASE,
    ),
    "Repo subpath header": re.compile(r"^[-\s]*Repo subpath:", re.MULTILINE),
    "Repo subpath inline": re.compile(r"`Repo subpath:`"),
    "projects/<project-id> path rule": re.compile(r"projects/<project-id>"),
    "projects/ directory scan": re.compile(
        r"(?:list|scan|enumerate)[^.\n]{0,80}?(?:child\s+)?directories?\s+(?:under|in|inside)\s+`?projects/`?",
        re.IGNORECASE,
    ),
}


class ResidualPatternGrepTest(unittest.TestCase):
    """No retired vocabulary survives in the rewrite scope."""

    def test_no_retired_patterns_in_scope(self) -> None:
        hits: list[tuple[str, Path, int, str]] = []
        for path in _in_scope_files():
            text = path.read_text(encoding="utf-8")
            for label, pattern in _RETIRED_PATTERNS.items():
                for match in pattern.finditer(text):
                    line_no = text.count("\n", 0, match.start()) + 1
                    line = text.splitlines()[line_no - 1]
                    hits.append((label, path, line_no, line))
        if hits:
            rendered = "\n".join(
                f"  {label} @ {path.relative_to(REPO_ROOT)}:{line_no}: {line}"
                for label, path, line_no, line in hits
            )
            self.fail(
                "Residual retired patterns found in rewrite scope:\n"
                + rendered
            )

    def test_changelog_is_excluded_from_scope(self) -> None:
        scope = _in_scope_files()
        changelog = REPO_ROOT / "protocol" / "CHANGELOG.md"
        self.assertNotIn(changelog, scope)


class WorkRootVocabularyTest(unittest.TestCase):
    """``Work root:`` is the canonical field name."""

    def test_task_template_uses_work_root_header(self) -> None:
        text = (TEMPLATES_DIR / "TASK.md").read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"(?m)^Work root:",
            msg="templates/TASK.md must declare `Work root:` as a task header",
        )

    def test_task_template_describes_work_root_section(self) -> None:
        text = (TEMPLATES_DIR / "TASK.md").read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"(?m)^##\s+Work root\b",
            msg="templates/TASK.md must carry a `## Work root` section",
        )


if __name__ == "__main__":
    unittest.main()
