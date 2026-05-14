"""Per-test scaffold helper for static checks and skill walkthroughs.

Creates an ephemeral, on-disk Cartopian project layout in a fresh temp
directory so acceptance walkthroughs do not depend on a committed
fixture project (e.g. ``projects/sample-project/``).

Standard-library only (``tempfile``, ``pathlib``, ``shutil``). Cleanup
runs automatically on context-manager exit, or via ``cleanup()`` —
which integrates with ``unittest.TestCase.addCleanup``.

Example::

    from tests.scaffold import project_scaffold

    with project_scaffold() as scaffold:
        scaffold.write("tasks/open/TASK-99-001-demo.md", "...")
        assert scaffold.tasks_open.is_dir()

    # or, in unittest:
    scaffold = project_scaffold()
    self.addCleanup(scaffold.cleanup)
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


TASK_STATUS_DIRS: tuple[str, ...] = ("open", "in-progress", "in-review", "done")

DEFAULT_SUBDIRS: tuple[str, ...] = (
    "decisions",
    "phases",
    "prompts",
    "reports",
    "reviews",
    "specs",
    *(f"tasks/{status}" for status in TASK_STATUS_DIRS),
)


@dataclass
class ProjectScaffold:
    """A live, on-disk scaffold shaped like a Cartopian project."""

    root: Path
    project_root: Path
    _cleaned: bool = field(default=False, init=False, repr=False)

    @property
    def tasks_open(self) -> Path:
        return self.project_root / "tasks" / "open"

    @property
    def tasks_in_progress(self) -> Path:
        return self.project_root / "tasks" / "in-progress"

    @property
    def tasks_in_review(self) -> Path:
        return self.project_root / "tasks" / "in-review"

    @property
    def tasks_done(self) -> Path:
        return self.project_root / "tasks" / "done"

    @property
    def prompts(self) -> Path:
        return self.project_root / "prompts"

    @property
    def reports(self) -> Path:
        return self.project_root / "reports"

    @property
    def reviews(self) -> Path:
        return self.project_root / "reviews"

    @property
    def specs(self) -> Path:
        return self.project_root / "specs"

    @property
    def phases(self) -> Path:
        return self.project_root / "phases"

    @property
    def decisions(self) -> Path:
        return self.project_root / "decisions"

    @property
    def config(self) -> Path:
        return self.project_root / "cartopian.toml"

    @property
    def state(self) -> Path:
        return self.project_root / "STATE.md"

    def write(self, relative: str, contents: str) -> Path:
        """Write ``contents`` to ``project_root / relative`` and return the absolute path."""
        target = self.project_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        return target

    def cleanup(self) -> None:
        """Remove the scaffold's temp directory. Idempotent and leak-safe.

        ``ignore_errors=True`` keeps test failures (which may leave files
        open or read-only on some platforms) from masking the original
        assertion error with a cleanup-time exception.
        """
        if self._cleaned:
            return
        self._cleaned = True
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "ProjectScaffold":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()


def project_scaffold(
    *,
    project_name: str = "scaffold-project",
    extra_dirs: Sequence[str] = (),
    cartopian_toml: str | None = None,
    state_md: str | None = None,
) -> ProjectScaffold:
    """Create a per-test temp directory shaped like a Cartopian project.

    Layout under ``project_root``:

    - ``tasks/{open,in-progress,in-review,done}/``
    - ``decisions/``, ``phases/``, ``prompts/``, ``reports/``,
      ``reviews/``, ``specs/``
    - ``cartopian.toml`` (minimal stub unless ``cartopian_toml`` is set)
    - ``STATE.md`` (minimal stub unless ``state_md`` is set)

    Pass ``extra_dirs`` to create additional relative subdirectories.
    Pass ``cartopian_toml=""`` or ``state_md=""`` to write empty files.
    """
    root = Path(tempfile.mkdtemp(prefix="cartopian-scaffold-"))
    project_root = root / project_name
    project_root.mkdir(parents=True)

    for sub in (*DEFAULT_SUBDIRS, *extra_dirs):
        (project_root / sub).mkdir(parents=True, exist_ok=True)

    toml_text = (
        cartopian_toml
        if cartopian_toml is not None
        else (
            "[project]\n"
            f'name = "{project_name}"\n'
            'protocol_version = "v0.3.0"\n'
        )
    )
    (project_root / "cartopian.toml").write_text(toml_text, encoding="utf-8")

    state_text = (
        state_md
        if state_md is not None
        else f"# {project_name} — State\n\n## Current phase\n\n_n/a_\n"
    )
    (project_root / "STATE.md").write_text(state_text, encoding="utf-8")

    return ProjectScaffold(root=root, project_root=project_root)


__all__ = ["DEFAULT_SUBDIRS", "ProjectScaffold", "TASK_STATUS_DIRS", "project_scaffold"]
