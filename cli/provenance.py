"""Universal raw-edit detection floor.

Cartopian's own tooling must detect any change to a *governed artifact* that did
not pass through a mediated writer — on every harness, with **zero harness
cooperation**. This module is that portable floor. It has two halves:

1. **Provenance recording.** Every successful mediated write appends one line to
   an append-only NDJSON *write log* (:data:`LOG_RELPATH`) recording the
   destination's project-relative path and the SHA-256 of the bytes that were
   written. The mediated-write chokepoint
   (:func:`cli.mediated_write.mediated_write`) calls :func:`record_write`, as
   does the one content-preserving lifecycle relocation that does not go through
   that chokepoint (``move-task``). The PM tool surface never touches the log
   directly; it is metadata, not a governed artifact, and lives under the
   ``.cartopian/`` dot-directory that the mediated writer's dotfile guard
   refuses as a write destination.

2. **Drift detection.** :func:`audit_provenance` enumerates the governed
   artifacts on disk, hashes each, and compares against the write log:

   - **tracked path, hash matches the *latest* logged hash for that path** →
     mediated, silent. Only the most recent mediated write is the artifact's
     current authorized state; superseded historical hashes are not accepted.
   - **tracked path, hash differs from the latest logged hash for that path** →
     ``guard``: a raw edit to a governed artifact since its last mediated write.
     This includes a raw *revert* to an earlier mediated version — restoring a
     superseded state out of band is itself an unmediated change, and a
     legitimate revert must go through a writer (which appends a fresh latest
     entry). This is the acceptance-critical detection.
   - **untracked path (no log entry under that path)** → ``advisory``: a
     governed artifact whose provenance cannot be attributed to a mediated write
     of *that path* (e.g. created out of band, or a pre-adoption brownfield
     file). Provenance is **path-bound**: a content match against some *other*
     path's logged hash does not clear it, because a raw-created artifact can
     trivially copy the bytes of any prior mediated write. Honest notice, not a
     hard violation. Content-preserving relocations stay silent because the one
     such path that does not go through the mediated-write chokepoint
     (``move-task``) appends a fresh log entry for its destination path.

   When no write log exists at all the baseline is *unestablished*: drift cannot
   be proven, so a single ``advisory`` is emitted instead of flagging every
   file (NF-004: already-adopted projects are unaffected; the floor only
   tightens once a baseline exists).

Detection runs inside ``plan-audit`` — an ordinary CLI command that reads files
and the log. No PreToolUse hook, no harness interception, no privileged daemon.
That is what makes this the *portable* floor: it stands alone on a harness with
no native interception point.

Stdlib-only. The governed-artifact set is the closed, documented set
(plans, phases, tasks, decisions, specs, STATE, backlog); it is extended only
by editing :data:`GOVERNED_ROOT_FILES` / :data:`GOVERNED_DIRS`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Union

# ---------------------------------------------------------------------------
# Write-log location.
#
# Project-relative path of the append-only provenance log. It sits under the
# ``.cartopian/`` dot-directory: the mediated writer refuses dotfiles as write
# destinations, so the log can never itself be authored through the PM tool
# surface, and the governed-artifact enumeration below never descends into it.
# ---------------------------------------------------------------------------
PROVENANCE_DIRNAME = ".cartopian"
LOG_BASENAME = "provenance.log"
LOG_RELPATH = f"{PROVENANCE_DIRNAME}/{LOG_BASENAME}"

_HASH_PREFIX = "sha256:"

# ---------------------------------------------------------------------------
# The governed-artifact set (plans, phases, tasks, decisions, specs, STATE,
# backlog).
#
# Root files: a fixed set of project-root basenames. Directory artifacts: every
# ``*.md`` at any depth under the named top-level directory (tasks/ has the
# open|in-progress|in-review|done sub-tree). Both are closed sets; nothing
# outside them is treated as governed by this floor.
# ---------------------------------------------------------------------------
GOVERNED_ROOT_FILES: Set[str] = {
    "IMPLEMENTATION_PLAN.md",  # plans
    "STATE.md",                # STATE
    "BACKLOG.md",              # backlog
}
GOVERNED_DIRS: Set[str] = {
    "phases",     # phases
    "tasks",      # tasks (recursive: open/in-progress/in-review/done)
    "decisions",  # decisions
    "specs",      # specs
}


def hash_bytes(data: bytes) -> str:
    """Return the prefixed SHA-256 digest (``sha256:<hex>``) of ``data``."""
    return _HASH_PREFIX + hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Management-identifier scan.
#
# The always-on hygiene rule: product code must never carry planning
# identifiers — a requirement, decision, task, backlog, open-question, review,
# phase, prompt, report, or spec id, or a plan-build reference. Assignees were
# storing these in code comments as if annotating a ticket — leakage of the
# managing project plus token burn. This is a deterministic fail-safe behind the
# structural launch-scope fix and the injected coding directive: a single regex
# over the bytes of changed work-root files, with no model round-trip.
# ---------------------------------------------------------------------------
# The optional ``(?:[A-Z]+-)?`` segment catches word-segment id forms the
# project actually uses — ``PROMPT-PLAN-NNN``, ``REVIEW-PLAN-NNN`` — which a
# plain ``PREFIX-<digits>`` pattern would miss. Digit segments stay two-to-three
# wide so a four-digit year-like suffix does not false-match.
PM_IDENTIFIER_RE = re.compile(
    r"\b(?:FR|DEC|TASK|BL|OQ|REVIEW|PHASE|PROMPT|REPORT|SPEC)-(?:[A-Z]+-)?\d{2,3}(?:-\d{2,3})?\b"
    r"|\bP\d{2}-(?:[A-Z]+-)?\d{2,3}\b"
)


def scan_pm_identifiers(
    paths: List[Union[str, os.PathLike]],
) -> List[Dict[str, object]]:
    """Flag managing-project planning identifiers in the given product files.

    Pure and cheap: reads each path and matches :data:`PM_IDENTIFIER_RE` line by
    line, returning a list of ``{path, line, match, text}`` hits. ``paths`` is
    the set of changed work-root files a caller (a review pass, a CI step, or a
    work-root-scoped audit) supplies; this function neither resolves work roots
    nor shells out to git, so it stays trivially testable and side-effect free.
    Unreadable or non-text files are skipped, never raised on.
    """
    hits: List[Dict[str, object]] = []
    for raw in paths:
        path = Path(raw)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in PM_IDENTIFIER_RE.finditer(line):
                hits.append({
                    "path": str(path),
                    "line": lineno,
                    "match": match.group(0),
                    "text": line.strip(),
                })
    return hits


def _relpath_in_root(project_root: Path, abs_path: Path) -> Optional[str]:
    """Project-relative POSIX path for ``abs_path``, or None if it escapes root.

    Both operands are canonicalized so a symlinked or ``..``-laden input cannot
    masquerade as in-root. The log key is always forward-slash separated so it
    is stable across platforms.
    """
    real_root = os.path.realpath(os.fspath(project_root))
    real_path = os.path.realpath(os.fspath(abs_path))
    if real_path == real_root:
        return None
    if not real_path.startswith(real_root + os.sep):
        return None
    rel = os.path.relpath(real_path, real_root)
    return rel.replace(os.sep, "/")


def record_write(
    project_root: Union[str, os.PathLike],
    abs_path: Union[str, os.PathLike],
    data: bytes,
    *,
    action: str = "mediated-write",
) -> bool:
    """Append a provenance record for a mediated write of ``abs_path``.

    Records the project-relative destination and the SHA-256 of ``data`` (the
    bytes the writer landed) to the append-only log. Returns ``True`` on a
    recorded line, ``False`` if the destination is outside the project root or
    the log could not be written.

    Best-effort and fail-open *for the write*: a recording failure must never
    turn a legitimate mediated write into an error. A missed record degrades to
    an ``advisory`` (untracked) at audit time, never a false ``guard``.
    """
    root = Path(project_root)
    rel = _relpath_in_root(root, Path(abs_path))
    if rel is None:
        return False
    record = {
        "relpath": rel,
        "hash": hash_bytes(data),
        "action": action,
        "ts": time.time(),
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    log_dir = Path(os.path.realpath(os.fspath(root))) / PROVENANCE_DIRNAME
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        # Append mode + a single small write: the record lands atomically with
        # respect to other appenders on every POSIX filesystem and on Windows.
        with open(log_dir / LOG_BASENAME, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        return False
    return True


def _read_log(project_root: Path) -> Optional[List[Dict[str, str]]]:
    """Return the parsed write-log records, or None if no log exists.

    Malformed or non-object lines are skipped (a corrupt tail must not crash an
    audit). An existing-but-empty log returns ``[]`` — the baseline *is*
    established, just with nothing tracked yet.
    """
    log_path = Path(os.path.realpath(os.fspath(project_root))) / PROVENANCE_DIRNAME / LOG_BASENAME
    if not log_path.is_file():
        return None
    records: List[Dict[str, str]] = []
    try:
        raw = log_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("relpath"), str) and isinstance(obj.get("hash"), str):
            records.append(obj)
    return records


def governed_files(project_root: Union[str, os.PathLike]) -> List[Path]:
    """Return every governed artifact present on disk under ``project_root``.

    Root files are the fixed :data:`GOVERNED_ROOT_FILES` basenames; directory
    artifacts are every ``*.md`` at any depth under :data:`GOVERNED_DIRS`.
    Returned paths are absolute and sorted for deterministic output.
    """
    root = Path(os.path.realpath(os.fspath(project_root)))
    found: List[Path] = []
    for basename in GOVERNED_ROOT_FILES:
        candidate = root / basename
        if candidate.is_file():
            found.append(candidate)
    for dirname in GOVERNED_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*.md"):
            if path.is_file():
                found.append(path)
    return sorted(found)


def audit_provenance(project_root: Union[str, os.PathLike]) -> Dict[str, object]:
    """Detect raw edits to governed artifacts. See module docstring for rules.

    Returns a structured result:

        {
          "baseline": "established" | "absent",
          "tracked_paths": <int>,
          "governed_files": <int>,
          "guard": [ {kind, relpath, path, current_hash, detail}, ... ],
          "advisory": [ {kind, relpath, path, detail}, ... ],
        }

    ``guard`` entries are raw edits to a previously-mediated artifact (the
    detection-floor positive). ``advisory`` entries are governed artifacts whose
    provenance cannot be established. Never raises on file I/O — an unreadable
    governed file becomes an ``advisory`` rather than aborting the audit.
    """
    root = Path(os.path.realpath(os.fspath(project_root)))
    files = governed_files(root)
    log = _read_log(root)

    guard: List[Dict[str, object]] = []
    advisory: List[Dict[str, object]] = []

    if log is None:
        # No baseline: drift is unprovable. Emit one honest advisory rather than
        # flagging every governed artifact on a pre-adoption project.
        if files:
            advisory.append({
                "kind": "no-provenance-baseline",
                "count": len(files),
                "detail": (
                    f"no mediated-write provenance log at {LOG_RELPATH}; "
                    f"{len(files)} governed artifact(s) cannot be verified. "
                    f"Drift detection activates once artifacts are written "
                    f"through a mediated writer."
                ),
            })
        return {
            "baseline": "absent",
            "tracked_paths": 0,
            "governed_files": len(files),
            "guard": guard,
            "advisory": advisory,
        }

    # The log is append-only and read in append order, so the *last* record for
    # a path is its current mediated state. Keep only that latest hash per path:
    # a match against a superseded historical hash (a raw revert) must not be
    # accepted as clean. Provenance is strictly path-bound — there is no
    # cross-path content tolerance, because a raw-created governed artifact can
    # copy the bytes of any prior mediated write of any file and would otherwise
    # masquerade as clean. The one content-preserving relocation that bypasses
    # the mediated-write chokepoint (``move-task``) logs its own destination
    # path, so it is tracked, not content-matched.
    latest_by_path: Dict[str, str] = {}
    for rec in log:
        latest_by_path[rec["relpath"]] = rec["hash"]

    for path in files:
        rel = _relpath_in_root(root, path)
        if rel is None:
            continue
        try:
            current = hash_bytes(path.read_bytes())
        except OSError as exc:
            advisory.append({
                "kind": "unreadable-governed-artifact",
                "relpath": rel,
                "path": str(path),
                "detail": f"governed artifact could not be read: {exc.strerror or exc}",
            })
            continue

        latest_for_path = latest_by_path.get(rel)
        if latest_for_path is not None:
            if current == latest_for_path:
                continue  # mediated: current content is the latest logged write
            guard.append({
                "kind": "raw-edit",
                "relpath": rel,
                "path": str(path),
                "current_hash": current,
                "detail": (
                    f"governed artifact '{rel}' was modified out of band: its "
                    f"current content does not match the latest mediated write "
                    f"recorded in {LOG_RELPATH} (a raw revert to a superseded "
                    f"mediated version is itself an unmediated change)"
                ),
            })
            continue

        # Untracked path: no log entry exists under this exact path. Provenance
        # is path-bound, so a content match against some other path's logged
        # hash does NOT clear it — that loophole would let a raw-created artifact
        # launder itself by copying the bytes of any prior mediated write.
        advisory.append({
            "kind": "untracked-governed-artifact",
            "relpath": rel,
            "path": str(path),
            "detail": (
                f"governed artifact '{rel}' has no mediated-write provenance in "
                f"{LOG_RELPATH}; it was not authored through a mediated writer"
            ),
        })

    return {
        "baseline": "established",
        "tracked_paths": len(latest_by_path),
        "governed_files": len(files),
        "guard": guard,
        "advisory": advisory,
    }
