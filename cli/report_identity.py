"""Authoritative task/report identity model.

Single source of truth for deriving task-scoped report paths. Every consumer
(prompt generation, dispatch, waits, aggregation, task bundles, cleanup,
CLI/MCP schemas) derives report identities here instead of constructing
filenames independently (CONVENTIONS.md § Reports).

The contract:

- Task completion keeps the compatibility filename ``REPORT-NN-NNN.md``.
- Task-review completion publishes independently to
  ``REPORT-NN-NNN-review.md``. The completion report is preserved, unmodified,
  for the reviewer to read directly; neither artifact can satisfy the other's
  completion signal.
- Planning-checkpoint reviews use ``REPORT-PLAN-NNN.md``.

Standard library only (NF-001).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

TASK_COMPLETION_VARIANT = "task"
TASK_REVIEW_VARIANT = "review"
PLANNING_REVIEW_VARIANT = "planning-review"

# Filename marker distinguishing the task-review completion report from the
# preserved task-completion report that shares the same NN-NNN identity.
REVIEW_REPORT_MARKER = "-review"

NN_NNN_RE = re.compile(r"^\d{2}-\d{3}$")
TASK_COMPLETION_REPORT_RE = re.compile(r"^REPORT-(\d{2}-\d{3})\.md$")
TASK_REVIEW_REPORT_RE = re.compile(r"^REPORT-(\d{2}-\d{3})-review\.md$")
PLANNING_REVIEW_REPORT_RE = re.compile(
    r"^REPORT-(PLAN-\d{3})\.md$"
)

# The complete report-filename grammar (cleanup and validation surfaces).
REPORT_FILENAME_RE = re.compile(
    r"^REPORT-(?:\d{2}-\d{3}(?:-review)?|PLAN-\d{3})\.md$"
)


def task_nn_nnn(task_id: str) -> Optional[str]:
    """Return the ``NN-NNN`` identity from ``TASK-NN-NNN`` or ``NN-NNN``."""
    candidate = task_id.removeprefix("TASK-")
    return candidate if NN_NNN_RE.fullmatch(candidate) else None


def completion_report_name(nn_nnn: str) -> str:
    return f"REPORT-{nn_nnn}.md"


def review_report_name(nn_nnn: str) -> str:
    return f"REPORT-{nn_nnn}{REVIEW_REPORT_MARKER}.md"


def completion_report_path(project_root: Path, nn_nnn: str) -> Path:
    """The task-completion report slot."""
    return Path(project_root) / "reports" / completion_report_name(nn_nnn)


def review_report_path(project_root: Path, nn_nnn: str) -> Path:
    """The independent task-review completion report slot."""
    return Path(project_root) / "reports" / review_report_name(nn_nnn)


def planning_report_path(project_root: Path, checkpoint_id: str) -> Path:
    """The planning-checkpoint report slot (unchanged naming)."""
    return Path(project_root) / "reports" / f"REPORT-{checkpoint_id}.md"


def report_path_for_variant(
    project_root: Path, nn_nnn: str, variant: str
) -> Path:
    """Resolve the expected task-scoped report path for a work type."""
    if variant == TASK_REVIEW_VARIANT:
        return review_report_path(project_root, nn_nnn)
    return completion_report_path(project_root, nn_nnn)


def variant_for_report_name(name: str) -> str:
    """The report variant a filename implies.

    ``REPORT-NN-NNN-review.md`` is always the task-review variant and
    ``REPORT-PLAN-...`` the planning-review variant. Any other name defaults
    to ``task``: the unmarked slot is the completion slot, so stale bytes of
    another shape cannot satisfy it.
    """
    if TASK_REVIEW_REPORT_RE.match(name):
        return TASK_REVIEW_VARIANT
    if name.startswith("REPORT-PLAN-"):
        return PLANNING_REVIEW_VARIANT
    return TASK_COMPLETION_VARIANT


def filename_contract_variant(name: str) -> Optional[str]:
    """The variant a grammar-matching filename mandates, or None outside it.

    Task-scoped filenames are authoritative: ``REPORT-NN-NNN.md`` can carry
    only task completion, ``REPORT-NN-NNN-review.md`` only task-review
    completion, and ``REPORT-PLAN-...`` only planning review. Neither report
    content nor an explicit variant override may bypass this contract; content
    of another shape at a mandated path is a path/variant mismatch. Names
    outside the grammar mandate nothing (legacy content inference applies).
    """
    if TASK_REVIEW_REPORT_RE.match(name):
        return TASK_REVIEW_VARIANT
    if TASK_COMPLETION_REPORT_RE.match(name):
        return TASK_COMPLETION_VARIANT
    if name.startswith("REPORT-PLAN-"):
        return PLANNING_REVIEW_VARIANT
    return None


def nn_nnn_for_report_name(name: str) -> Optional[str]:
    """The task ``NN-NNN`` identity a task-scoped report filename carries."""
    for pattern in (TASK_REVIEW_REPORT_RE, TASK_COMPLETION_REPORT_RE):
        match = pattern.match(name)
        if match:
            return match.group(1)
    return None
