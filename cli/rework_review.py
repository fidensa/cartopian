"""Resolve retained closure findings as an exact, machine-bound assignee input."""
from pathlib import Path
from typing import Any, Dict, Optional

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
