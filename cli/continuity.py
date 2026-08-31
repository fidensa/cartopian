"""The one authority for the project-root ``CONTINUITY.md`` project summary.

The summary is a **plain UTF-8 Markdown** file a plan close may leave behind so
that a later session can be pointed at it. Exactly two commands touch it:
``write-continuity`` creates or replaces it at plan close, and
``read-continuity`` returns it when the operator explicitly asks for it.

Nothing else opens, stats, or parses the artifact. ``next-action``,
``compose-state``, ``write-state``, startup, status, prompt/state composition,
task assignment, and task execution carry no continuity input or output path,
and no record they emit gains a field, projection, index, or rendered line
derived from it. There is no schema, no row grammar, no compact index, and no
size ceiling beyond what the mediated-write primitive already enforces for
every root artifact: the summary is whatever Markdown the closeout wrote.

Absence is an ordinary successful state. A project with no summary starts,
reports status, composes state, and executes tasks exactly as one that has
never had a plan close, and an explicit retrieval reports the absence without
contributing context and without failing.
"""
from __future__ import annotations

import stat
from pathlib import Path
from typing import Optional

CONTINUITY_BASENAME = "CONTINUITY.md"

# The mediated-write destination kind that resolves to the artifact. Declared
# here so the writer never re-states the allowlist key.
DEST_KIND = "continuity"


class ContinuityRefusal(Exception):
    """A named, fail-closed refusal on the explicit retrieval path.

    Raised only for a summary that is *present and unsafe or undecodable* —
    never for one that is simply absent. Because retrieval runs only when the
    operator asks for it, this refusal can never reach an automatic lifecycle
    surface.
    """

    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(f"{rule}: {detail}")
        self.rule = rule
        self.detail = detail


def artifact_path(project_root) -> Path:
    """The one location the summary is ever resolved from."""
    return Path(project_root) / CONTINUITY_BASENAME


def read_summary(project_root) -> Optional[str]:
    """Return the summary body, or ``None`` when the project has no summary.

    ``None`` is the zero-context success: the caller adds nothing and reports
    nothing. A present artifact that is a symlink or another non-regular file
    is refused rather than followed (containment), as is one that is not valid
    UTF-8, because a summary that cannot be read as text cannot be handed back
    as one.
    """
    path = artifact_path(project_root)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ContinuityRefusal(
            "continuity-unreadable", f"{CONTINUITY_BASENAME}: {exc.strerror or exc}"
        )
    if stat.S_ISLNK(info.st_mode):
        raise ContinuityRefusal(
            "continuity-not-regular",
            f"{CONTINUITY_BASENAME} is a symlink; the summary is never followed "
            "out of the project root",
        )
    if not stat.S_ISREG(info.st_mode):
        raise ContinuityRefusal(
            "continuity-not-regular",
            f"{CONTINUITY_BASENAME} is not a regular file",
        )
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ContinuityRefusal(
            "continuity-not-utf8", f"{CONTINUITY_BASENAME} is not valid UTF-8"
        )
    except OSError as exc:
        raise ContinuityRefusal(
            "continuity-unreadable", f"{CONTINUITY_BASENAME}: {exc.strerror or exc}"
        )
