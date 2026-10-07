"""Resolve retained closure findings as an exact, machine-bound assignee input."""
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cli import artifact_paths, assignment_inputs, checkpoint_identity, provenance


def applies(task_path: Path) -> bool:
    """Whether retained findings are an input for this task's assignment.

    Retained findings direct rework, so they bind to the coder assignment
    only. A task in review is assigned for closure review, which judges the
    delivered work and never receives the rework payload. The composer,
    writer, and preflight all consult this predicate, so they agree on the
    allowed inputs for each assignment purpose.
    """
    return Path(task_path).parent.name != "in-review"


def resolve(project_root: Path, task_id: str) -> Optional[Dict[str, Any]]:
    path = project_root / "reviews" / f"REVIEW-{task_id.removeprefix('TASK-')}.md"
    if not path.exists() and not path.is_symlink():
        return None
    try:
        _, content = artifact_paths.review(project_root, path)
    except artifact_paths.ArtifactRefusal as exc:
        raise ValueError(str(exc)) from exc
    if checkpoint_identity.header(content, "Verdict") not in ("request-changes", "reject"):
        return None
    target = checkpoint_identity.header(content, "Target")
    if target != task_id:
        raise ValueError("retained closure review targets a different task")
    # Preserve the full retained record, including verbatim finding rows and
    # their context. Hashes and management identities live in the typed input.
    return {**assignment_inputs.payload_binding(
        assignment_inputs.CHANNEL_REWORK, path.relative_to(project_root).as_posix(), content
    ), "content": content}


def preserve(project_root: Path, review_path: Path) -> None:
    if review_path.exists() or review_path.is_symlink():
        try:
            _, content = artifact_paths.review(project_root, review_path)
        except artifact_paths.ArtifactRefusal as exc:
            raise ValueError(str(exc)) from exc
        if not provenance.record_review(project_root, review_path, content):
            raise ValueError("cannot preserve prior retained review in provenance")


_TASK_REVIEW_REF_RE = re.compile(r"(?<![A-Za-z0-9-])(REVIEW-\d{2}-\d{3})(?![0-9A-Za-z-])")
_LIVE_STATUSES = ("open", "in-progress", "in-review")


def follow_up_refusal(
    project_root: Path, task_id: str, content: str, previous: str = ""
) -> Optional[Tuple[str, str]]:
    """Refuse a task that spawns follow-up work from a failed review.

    A ``request-changes`` or ``reject`` verdict returns its target task to
    rework, and that task stays the unit of work, so the failed review's
    findings never become a replacement or follow-up task. This refuses a
    task write that newly cites a retained failed review of a *different*
    task that is still live (not done). Citations already in the task, the
    target task's own writes, approved reviews, and reviews of done tasks
    are unaffected. Returns ``(rule, detail)`` or ``None``.
    """
    cited = set(_TASK_REVIEW_REF_RE.findall(content)) - set(
        _TASK_REVIEW_REF_RE.findall(previous)
    )
    for review_id in sorted(cited):
        path = project_root / "reviews" / f"{review_id}.md"
        if not path.exists() and not path.is_symlink():
            continue
        try:
            _, review = artifact_paths.review(project_root, path)
        except (artifact_paths.ArtifactRefusal, OSError, UnicodeError):
            continue
        verdict = checkpoint_identity.header(review, "Verdict")
        target = checkpoint_identity.header(review, "Target") or ""
        if verdict not in ("request-changes", "reject") or target == task_id:
            continue
        if not re.fullmatch(r"TASK-\d{2}-\d{3}", target):
            continue
        status = next(
            (
                name for name in _LIVE_STATUSES
                if (project_root / "tasks" / name / f"{target}.md").is_file()
            ),
            None,
        )
        if status is None:
            continue
        return (
            "review-follow-up-task",
            f"{task_id} cites {review_id}, a {verdict} review of {target}, "
            f"which is still in tasks/{status}/. A failed review returns its "
            "task for rework; it does not spawn replacement or follow-up "
            f"tasks — remedy the findings in {target} itself (its retained "
            "review is the rework input), and record any non-blocking "
            "follow-up note with write-backlog",
        )
    return None
