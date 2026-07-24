"""`cartopian plan-audit <project-path>` — lifecycle and provenance audit."""
import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli.commands import delete_backlog, write_backlog
from cli.commands.resolve_config import (
    _CliError,
    _load_project_config,
    _require_startup_project_keys,
    _resolve_deliverable,
    resolve_review_policy,
)
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from cli.protocol_gate import (
    GATE_BLOCKED,
    GATE_MIGRATE,
    classify_protocol_version,
    read_shipped_protocol_version,
)
from cli.provenance import audit_provenance, scan_pm_identifiers

_TASK_ID_RE = re.compile(r"^TASK-(\d{2}-\d{3})")
_STATUS_DIRS = ("in-progress", "in-review")
_ALL_TASK_DIRS = ("open", "in-progress", "in-review", "done")
# Deliverable existence is checked once a document-deliverable task has reached
# review (the reviewer must have the artifact) and at done (the durable record
# the task promised must persist).
_DELIVERABLE_STATUS_DIRS = ("in-review", "done")
_WORK_ROOT_RE = re.compile(r"^Work root:\s*(.+)$", re.MULTILINE)
_DELIVERABLE_RE = re.compile(r"^Deliverable:\s*(.+)$", re.MULTILINE)
_ASSIGNEE_RE = re.compile(r"^Assignee:\s*(.+)$", re.MULTILINE)
_VERDICT_RE = re.compile(r"\bVerdict:\s*(approve|request-changes|reject)\b(?!\s*\|)")
_STATUS_RE = re.compile(r"^Status:\s*(.+)$", re.MULTILINE)

# --- Infrastructure-artifact scope guard ------------------------------------
# Assignees must not add `.github`, CI, or other infrastructure artifacts to a
# work root unless the task explicitly authorizes them (CONVENTIONS § PM Scope
# / Lifecycle CLI Guards). Detection is by changed-file path: a path equal to
# or under one of these top-level infra markers counts. The list names the
# common per-repo CI/infra entrypoints; it is a closed, documented set —
# mechanism over policy prose.
_INFRA_MARKERS: Tuple[str, ...] = (
    ".github",
    ".gitlab",
    ".gitlab-ci.yml",
    ".circleci",
    ".buildkite",
    ".travis.yml",
    ".drone.yml",
    "azure-pipelines.yml",
    "bitbucket-pipelines.yml",
    "Jenkinsfile",
)
# A task authorizes infra artifacts ONLY via the explicit opt-in task-file
# field `Infra authorized: <value>`. The value is either a comma-separated
# list of the markers the task authorizes (e.g. `Infra authorized: .github`)
# or the blanket `yes`/`all`. Prose mentions are deliberately NOT
# authorization: substring matching would fail open on incidental mentions
# ("deployed to myapp.github.io") and even prohibitions ("do NOT touch
# .github"), defeating the guard. Prefer the marker-scoped form — the blanket
# `yes` authorizes every marker for that work root and should be rare.
_INFRA_AUTHORIZED_RE = re.compile(r"^Infra authorized:\s*(.+)$", re.MULTILINE | re.IGNORECASE)


def _infra_hits(changed: List[str]) -> Dict[str, List[str]]:
    """Map infra marker -> changed file paths under it (empty when none)."""
    hits: Dict[str, List[str]] = {}
    for path in changed:
        normalized = path.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        top = normalized.split("/", 1)[0]
        for marker in _INFRA_MARKERS:
            if top == marker:
                hits.setdefault(marker, []).append(path)
                break
    return hits


def _task_authorizes_infra(task_content: str, marker: str) -> bool:
    """True iff the task's `Infra authorized:` field covers `marker`.

    Explicit-field-only by design (no prose/substring inference). The field
    value is `yes`/`all` (blanket) or a comma-separated marker list checked
    against the specific marker.
    """
    for m in _INFRA_AUTHORIZED_RE.finditer(task_content):
        value = m.group(1).strip()
        if value.lower() in ("yes", "all"):
            return True
        tokens = [t.strip() for t in value.split(",") if t.strip()]
        if marker in tokens:
            return True
    return False


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_path",
        help="Absolute path to the project root",
    )


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def _read_work_root_names(task_content: str) -> List[str]:
    m = _WORK_ROOT_RE.search(task_content)
    if not m:
        return []
    raw = m.group(1).strip()
    if not raw or raw == "n/a":
        return []
    return [v.strip() for v in raw.split(",") if v.strip()]


def _git_changed_files(work_root_path: str) -> Optional[List[str]]:
    """Return list of changed (uncommitted) file paths under work_root, or None if git unavailable."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=work_root_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    # Each porcelain line: XY path  (first two chars are status flags)
    return [l[3:].strip() for l in lines if len(l) > 3]


def _resolve_pm_owns_product_branches(project_path: Path) -> bool:
    """Return effective git.pm_owns_product_branches.

    Resolution order (project > global > protocol default):
      1. <project>/cartopian.toml `[git].pm_owns_product_branches`
      2. ~/.cartopian/cartopian.toml `[git].pm_owns_product_branches`
      3. protocol default: False
    """
    project_toml = project_path / "cartopian.toml"
    if project_toml.exists():
        try:
            with project_toml.open("rb") as fh:
                project_cfg = tomllib.load(fh)
            p_git = project_cfg.get("git", {}) or {}
            if "pm_owns_product_branches" in p_git:
                return bool(p_git["pm_owns_product_branches"])
        except (OSError, tomllib.TOMLDecodeError):
            pass

    global_toml = Path.home() / ".cartopian" / "cartopian.toml"
    if global_toml.exists():
        try:
            with global_toml.open("rb") as fh:
                global_cfg = tomllib.load(fh)
            g_git = global_cfg.get("git", {}) or {}
            if "pm_owns_product_branches" in g_git:
                return bool(g_git["pm_owns_product_branches"])
        except (OSError, tomllib.TOMLDecodeError):
            pass

    return False


def _read_assignee(task_content: str) -> Optional[str]:
    m = _ASSIGNEE_RE.search(task_content)
    if not m:
        return None
    value = m.group(1).strip()
    if not value or value.lower() == "n/a":
        return None
    return value


def _load_task_index(project_path: Path) -> List[Dict[str, Any]]:
    """Read every canonical task file ONCE and return an in-memory index.

    Each entry: ``{task_id, status, mtime, content, work_roots}``. The three
    audit passes that need task contents (provenance attribution, last-assignee
    lookup, infra authorization) all consume this single index instead of each
    re-walking the task tree and re-reading every file.
    """
    index: List[Dict[str, Any]] = []
    for status_dir in _ALL_TASK_DIRS:
        tasks_dir = project_path / "tasks" / status_dir
        if not tasks_dir.is_dir():
            continue
        for task_file in tasks_dir.iterdir():
            if not task_file.is_file() or task_file.suffix != ".md":
                continue
            m = _TASK_ID_RE.match(task_file.stem)
            if not m:
                continue
            try:
                content = task_file.read_text(encoding="utf-8")
            except OSError:
                continue
            try:
                mtime = task_file.stat().st_mtime
            except OSError:
                mtime = 0.0
            index.append({
                "task_id": f"TASK-{m.group(1)}",
                "status": status_dir,
                "mtime": mtime,
                "content": content,
                "work_roots": _read_work_root_names(content),
            })
    return index


def _find_last_assignee(
    task_index: List[Dict[str, Any]], work_root_name: str
) -> Optional[Dict[str, str]]:
    """Find the most-recently-modified task naming this work_root and return
    {task_id, assignee, status} or None if no attribution can be made."""
    candidates: List[Tuple[float, str, str, str]] = []
    for entry in task_index:
        if work_root_name not in entry["work_roots"]:
            continue
        assignee = _read_assignee(entry["content"])
        if not assignee:
            continue
        candidates.append(
            (entry["mtime"], entry["task_id"], assignee, entry["status"])
        )
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, task_id, assignee, status = candidates[0]
    return {"task_id": task_id, "assignee": assignee, "task_status": status}


def _load_work_roots(project_path: Path) -> Dict[str, str]:
    """Return {name: abs_path} from cartopian.toml + cartopian.local.toml."""
    project_toml = project_path / "cartopian.toml"
    if not project_toml.exists():
        return {}
    try:
        with project_toml.open("rb") as fh:
            project_cfg = tomllib.load(fh)
    except Exception:
        return {}

    names = (project_cfg.get("project", {}) or {}).get("work_roots", []) or []
    if not names:
        return {}

    local_toml = project_path / "cartopian.local.toml"
    if not local_toml.exists():
        return {}
    try:
        with local_toml.open("rb") as fh:
            local_cfg = tomllib.load(fh)
    except Exception:
        return {}

    local_roots = local_cfg.get("work_roots", {}) or {}
    resolved: Dict[str, str] = {}
    for name in names:
        if name in local_roots:
            p = Path(str(local_roots[name]))
            if p.is_absolute():
                resolved[name] = str(p)
    return resolved


def _check_artifact_chains(
    project_path: Path, task_review_required: bool = True
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Check that active tasks have their required artifacts in place."""
    blockers: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    for status_dir in _STATUS_DIRS:
        tasks_dir = project_path / "tasks" / status_dir
        if not tasks_dir.is_dir():
            continue
        for task_file in sorted(tasks_dir.iterdir()):
            if not task_file.is_file() or task_file.suffix != ".md":
                continue
            m = _TASK_ID_RE.match(task_file.stem)
            if not m:
                continue
            nn_nnn = m.group(1)
            task_id = f"TASK-{nn_nnn}"

            if status_dir == "in-progress":
                prompt = project_path / "prompts" / f"PROMPT-{nn_nnn}.md"
                if not prompt.is_file():
                    blockers.append({
                        "kind": "missing-prompt",
                        "task_id": task_id,
                        "task_path": str(task_file),
                        "expected": str(prompt),
                        "detail": f"{task_id} is in-progress but has no prompt at {prompt}",
                    })

            elif status_dir == "in-review":
                review = project_path / "reviews" / f"REVIEW-{nn_nnn}.md"
                if not review.is_file():
                    finding = {
                        "kind": "missing-review-artifact",
                        "task_id": task_id,
                        "task_path": str(task_file),
                        "expected": str(review),
                        "detail": f"{task_id} is in-review but has no review artifact at {review}",
                    }
                    if task_review_required:
                        blockers.append(finding)
                    else:
                        finding["detail"] += " (advisory: task-closure review is off)"
                        warnings.append(finding)
                else:
                    try:
                        content = review.read_text(encoding="utf-8")
                    except OSError:
                        content = ""
                    vm = _VERDICT_RE.search(content)
                    if not vm:
                        finding = {
                            "kind": "review-missing-verdict",
                            "task_id": task_id,
                            "review_path": str(review),
                            "detail": f"review artifact for {task_id} has no Verdict: field",
                        }
                        if task_review_required:
                            blockers.append(finding)
                        else:
                            finding["detail"] += " (advisory: task-closure review is off)"
                            warnings.append(finding)

    return blockers, warnings


def _check_deliverables(
    project_path: Path,
    project_cfg: Dict[str, Any],
    task_review_required: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Blocker when a document-deliverable task's durable file is missing.

    A task in ``in-review`` or ``done`` that declares a ``Deliverable:`` must
    have that file on disk — at ``in-review`` the reviewer reviews it; at
    ``done`` it is the durable record the task promised (CONVENTIONS § Document
    Deliverables). Resolution reuses the aggregators' rule
    (:func:`_resolve_deliverable`). When the path cannot be resolved on this
    machine — a work-root name unmapped in ``cartopian.local.toml`` — the check
    is skipped rather than firing a cross-machine false positive; work-root
    mapping is validated on its own path. ``project``-mode deliverables always
    resolve (relative to the project root), so they are always checked; one
    that escapes ``resources/`` (a legacy placement predating CONVENTIONS
    § Project Resources) additionally emits a ``deliverable-outside-resources``
    warning.
    """
    blockers: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    for status_dir in _DELIVERABLE_STATUS_DIRS:
        tasks_dir = project_path / "tasks" / status_dir
        if not tasks_dir.is_dir():
            continue
        for task_file in sorted(tasks_dir.iterdir()):
            if not task_file.is_file() or task_file.suffix != ".md":
                continue
            m = _TASK_ID_RE.match(task_file.stem)
            if not m:
                continue
            task_id = f"TASK-{m.group(1)}"
            try:
                content = task_file.read_text(encoding="utf-8")
            except OSError:
                continue
            dm = _DELIVERABLE_RE.search(content)
            if not dm:
                continue
            deliverable = _resolve_deliverable(project_cfg, project_path, dm.group(1))
            if deliverable is None:
                continue
            if deliverable["mode"] == "project" and not deliverable["in_resources"]:
                # Legacy placement predating CONVENTIONS § Project Resources;
                # new tasks are blocked at validate-task-readiness instead.
                warnings.append({
                    "kind": "deliverable-outside-resources",
                    "task_id": task_id,
                    "task_path": str(task_file),
                    "task_status": status_dir,
                    "detail": (
                        f"{task_id} declares project-mode deliverable "
                        f"'{deliverable['logical']}' outside resources/; "
                        "supporting artifacts live under resources/ "
                        "(project:resources/<path>)"
                    ),
                })
            absolute = deliverable["absolute_path"]
            if absolute is None:
                # Work-root name unmapped on this machine — cannot verify; skip.
                continue
            if Path(absolute).is_file():
                continue
            finding = {
                "kind": "missing-deliverable",
                "task_id": task_id,
                "task_path": str(task_file),
                "task_status": status_dir,
                "expected": absolute,
                "detail": (
                    f"{task_id} is {status_dir} and declares deliverable "
                    f"'{deliverable['logical']}' but no file exists at {absolute}"
                ),
            }
            if status_dir == "in-review" and not task_review_required:
                finding["detail"] += " (advisory: task-closure review is off)"
                warnings.append(finding)
            else:
                blockers.append(finding)
    return blockers, warnings


def _check_work_root_provenance(
    project_path: Path,
    work_roots: Dict[str, str],
    pm_owns_product_branches: bool,
    task_index: List[Dict[str, Any]],
    changed_by_root: Dict[str, Optional[List[str]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Inspect dirty work_roots and return (warnings, attributions).

    When `pm_owns_product_branches` is True, the PM owns product-repo plumbing,
    so dirty state without an active prompted task is anomalous and emits an
    `unattributed-work-root-changes` warning (legacy behavior).

    When False (the protocol default), product-repo state belongs to the
    assignee, not the PM. Dirty state is expected and does not warrant a
    warning. Instead, the audit emits an informational `work-root-attribution`
    entry naming the most-recently-modified task that targeted this work_root
    and its assignee, so attribution is preserved without blocking the PM.
    """
    if not work_roots:
        return [], []

    # Build a map: work_root_name -> list of task_ids currently assigned
    # (from the shared task index — no second task-tree walk).
    active_assignments: Dict[str, List[str]] = {name: [] for name in work_roots}
    for entry in task_index:
        if entry["status"] not in _STATUS_DIRS:
            continue
        for root_name in entry["work_roots"]:
            if root_name in active_assignments:
                active_assignments[root_name].append(entry["task_id"])

    warnings: List[Dict[str, Any]] = []
    attributions: List[Dict[str, Any]] = []
    for name, abs_path in work_roots.items():
        changed = changed_by_root.get(name)
        if changed is None or not changed:
            continue
        assigned_tasks = active_assignments.get(name, [])
        has_prompt = bool(assigned_tasks) and any(
            (project_path / "prompts" / f"PROMPT-{tid.replace('TASK-', '')}.md").is_file()
            for tid in assigned_tasks
        )
        unattributed = not assigned_tasks or not has_prompt

        if not unattributed:
            continue

        if not pm_owns_product_branches:
            attribution = _find_last_assignee(task_index, name)
            entry: Dict[str, Any] = {
                "kind": "work-root-attribution",
                "work_root": name,
                "work_root_path": abs_path,
                "changed_files": changed,
            }
            if attribution:
                entry.update(attribution)
                entry["detail"] = (
                    f"uncommitted changes under '{name}' ({abs_path}) "
                    f"attributed to {attribution['assignee']} via "
                    f"{attribution['task_id']} ({attribution['task_status']}); "
                    f"PM does not own product-repo branches"
                )
            else:
                entry["detail"] = (
                    f"uncommitted changes under '{name}' ({abs_path}); "
                    f"no prior task names this work root, so attribution is unknown. "
                    f"PM does not own product-repo branches"
                )
            attributions.append(entry)
            continue

        if not assigned_tasks:
            warnings.append({
                "kind": "unattributed-work-root-changes",
                "work_root": name,
                "work_root_path": abs_path,
                "changed_files": changed,
                "detail": (
                    f"uncommitted changes exist under '{name}' ({abs_path}) "
                    f"but Cartopian cannot attribute them to an active prompted task"
                ),
            })
        else:
            warnings.append({
                "kind": "unattributed-work-root-changes",
                "work_root": name,
                "work_root_path": abs_path,
                "changed_files": changed,
                "assigned_tasks": assigned_tasks,
                "detail": (
                    f"uncommitted changes exist under '{name}' ({abs_path}); "
                    f"task(s) {', '.join(assigned_tasks)} are assigned but no active prompt exists"
                ),
            })

    return warnings, attributions


def _check_infra_artifacts(
    work_roots: Dict[str, str],
    task_index: List[Dict[str, Any]],
    changed_by_root: Dict[str, Optional[List[str]]],
) -> List[Dict[str, Any]]:
    """Surface assignee-created infrastructure artifacts.

    For every dirty work root, changed files under a top-level infra marker
    (``.github``, CI configs — see :data:`_INFRA_MARKERS`) emit an
    ``unauthorized-infra-artifacts`` warning unless some task naming that work
    root explicitly authorizes them via the ``Infra authorized:`` field
    (marker-scoped list, or the blanket ``yes``/``all``). Prose mentions are
    not authorization, and attribution alone is NOT authorization: an assignee
    staying in scope never needs infra artifacts the task did not call for. A
    warning, not a blocker — the operator decides; lifecycle movement is not
    blocked by itself (CONVENTIONS § Lifecycle CLI Guards).
    """
    warnings: List[Dict[str, Any]] = []
    for name, abs_path in work_roots.items():
        changed = changed_by_root.get(name)
        if not changed:
            continue
        hits = _infra_hits(changed)
        if not hits:
            continue

        # Candidate authorizing tasks: every task (any status) naming this
        # work root — from the shared task index, no re-read. A completed task
        # that authorized the artifact keeps authorizing it while the change
        # sits uncommitted.
        task_texts: Dict[str, str] = {
            entry["task_id"]: entry["content"]
            for entry in task_index
            if name in entry["work_roots"]
        }

        for marker, files in sorted(hits.items()):
            if any(
                _task_authorizes_infra(text, marker)
                for _tid, text in sorted(task_texts.items())
            ):
                continue
            shown = ", ".join(files[:5]) + ("..." if len(files) > 5 else "")
            warnings.append({
                "kind": "unauthorized-infra-artifacts",
                "work_root": name,
                "work_root_path": abs_path,
                "marker": marker,
                "files": files,
                "tasks_checked": sorted(task_texts),
                "detail": (
                    f"infrastructure artifact(s) under '{marker}' changed in "
                    f"work root '{name}' ({abs_path}) with no task "
                    f"authorization (no task naming this work root carries "
                    f"`Infra authorized: {marker}` or `Infra authorized: yes`): "
                    f"{shown}"
                ),
            })
    return warnings


def _check_pm_identifier_leaks(
    work_roots: Dict[str, str],
    changed_by_root: Dict[str, Optional[List[str]]],
) -> List[Dict[str, Any]]:
    """Identifier-leak detection floor over changed work-root files.

    Product code must never carry managing-project planning identifiers
    (requirement, decision, task, backlog, review, phase, prompt, report, spec
    ids). For every dirty work root, the changed files are run through
    :func:`cli.provenance.scan_pm_identifiers` — a pure regex pass, no model
    round-trip — and any hit emits a ``pm-identifier-leak`` warning naming the
    file, line, and matched identifier. A warning, not a blocker: the leak is
    an output-hygiene violation for the operator to route back to the
    assignee; lifecycle movement is not blocked by itself (CONVENTIONS §
    Lifecycle CLI Guards).
    """
    warnings: List[Dict[str, Any]] = []
    for name, abs_path in work_roots.items():
        changed = changed_by_root.get(name)
        if not changed:
            continue
        # Map absolute candidate path -> the git-relative path for reporting.
        candidates = {str(Path(abs_path) / rel): rel for rel in changed}
        hits = [
            {
                "path": candidates.get(h["path"], h["path"]),
                "line": h["line"],
                "match": h["match"],
                "text": h["text"],
            }
            for h in scan_pm_identifiers(list(candidates))
        ]
        if not hits:
            continue
        files = sorted({h["path"] for h in hits})
        shown = ", ".join(
            f"{h['path']}:{h['line']} ({h['match']})" for h in hits[:5]
        ) + ("..." if len(hits) > 5 else "")
        warnings.append({
            "kind": "pm-identifier-leak",
            "work_root": name,
            "work_root_path": abs_path,
            "files": files,
            "hits": hits,
            "detail": (
                f"management planning identifier(s) leaked into changed "
                f"file(s) in work root '{name}' ({abs_path}): {shown}. "
                f"Product code must not carry management-bookkeeping "
                f"references"
            ),
        })
    return warnings


def _check_backlog_invariants(
    project_path: Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Assert the backlog id invariant and flag stalled promotions.

    - Blocker: ``Highest id issued:`` sits below a live entry's id — only a raw
      hand-edit can regress the mark, and reusing an id below it would collide.
    - Warning: a live entry already carries a ``Source: BL-NNN`` stamp in the
      durable surface — the benign duplicate a stamp-then-delete crash leaves;
      finish the promotion by deleting the entry.
    """
    blockers: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    backlog_path = project_path / "BACKLOG.md"
    if not backlog_path.is_file():
        return blockers, warnings
    try:
        text = backlog_path.read_text(encoding="utf-8")
    except OSError:
        return blockers, warnings

    preamble, sections = write_backlog._split_sections(text)
    live = write_backlog._live_ids(sections)
    max_live = max(live) if live else 0
    mark = write_backlog._read_mark(preamble)
    if mark is not None and mark < max_live:
        blockers.append({
            "kind": "backlog-mark-regressed",
            "detail": (
                f"BACKLOG.md `Highest id issued:` is {write_backlog._format_id(mark)} "
                f"but a live entry is {write_backlog._format_id(max_live)}; the mark "
                "was hand-edited below a live id"
            ),
        })

    for sid, _text in sections:
        stamp = delete_backlog.find_source_stamp(project_path, sid)
        if stamp is not None:
            warnings.append({
                "kind": "backlog-promotion-unfinished",
                "detail": (
                    f"live backlog entry {sid} is already stamped as Source in "
                    f"{stamp}; finish the promotion by deleting {sid}"
                ),
            })
    return blockers, warnings


def _check_situation_notes(project_path: Path) -> List[Dict[str, Any]]:
    """Block while STATE.md carries undelivered-mail Situation notes.

    A Situation note has a one-delivery TTL: it exists to survive exactly one
    gap between sessions, and its mere presence at audit time means the
    current session has not yet consumed it. Each note is a blocker so none
    can be skimmed past — act on it, promote it (``write-backlog``,
    ``write-decision``), or drop it, then refresh ``STATE.md`` via
    ``write-state`` (which always composes with zero notes).
    """
    # Lazy import: write_state imports compose_state, which shares config
    # helpers with this module; importing here keeps startup one-directional.
    from cli.commands.write_state import existing_notes

    blockers: List[Dict[str, Any]] = []
    for note in existing_notes(project_path):
        blockers.append({
            "kind": "unresolved-situation-note",
            "detail": (
                f"STATE.md situation note awaits resolution: {note!r} — act on "
                "it, promote it (write-backlog / write-decision) if durable, "
                "then refresh STATE.md via write-state"
            ),
        })
    return blockers


def handler(args: argparse.Namespace) -> int:
    raw_path = args.project_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"project_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    project_path = Path(raw_path)
    if not project_path.is_dir():
        _stderr("error", f"project path not found: {raw_path}")
        return EXIT_FAIL

    if not (project_path / "cartopian.toml").is_file():
        _stderr("error", f"no cartopian.toml found at: {project_path}")
        return EXIT_FAIL

    try:
        project_cfg = _load_project_config(project_path)
        _, _, declared_protocol_version = _require_startup_project_keys(
            project_cfg, project_path / "cartopian.toml"
        )
        review_policy = resolve_review_policy(project_path)
    except _CliError as err:
        _stderr(err.prefix, err.message)
        return err.exit_code

    # Config-schema migration gate: classify the config's declared
    # [project].protocol_version against the shipped protocol version.
    # Older-but-migratable → warning naming the required migration;
    # unknown/newer → blocker (audit fails closed with the named residual).
    # The gate never edits cartopian.toml.
    try:
        shipped_protocol_version = read_shipped_protocol_version()
    except (OSError, RuntimeError) as exc:
        _stderr("error", str(exc))
        return EXIT_FAIL
    protocol_gate = classify_protocol_version(
        declared_protocol_version, shipped_protocol_version
    )

    blockers: List[Dict[str, Any]] = []
    if protocol_gate["status"] == GATE_BLOCKED:
        blockers.append({
            "kind": "protocol-version-unverifiable",
            "detected_version": protocol_gate["detected_version"],
            "shipped_version": protocol_gate["shipped_version"],
            "detail": protocol_gate["detail"],
        })
    blockers.extend(_check_situation_notes(project_path))
    task_review_required = review_policy["task_closure"]["mode"] == "required"
    artifact_blockers, review_warnings = _check_artifact_chains(
        project_path, task_review_required
    )
    deliverable_blockers, deliverable_warnings = _check_deliverables(
        project_path, project_cfg, task_review_required
    )
    blockers.extend(artifact_blockers)
    blockers.extend(deliverable_blockers)
    backlog_blockers, backlog_warnings = _check_backlog_invariants(project_path)
    blockers.extend(backlog_blockers)

    work_roots = _load_work_roots(project_path)
    pm_owns_product_branches = _resolve_pm_owns_product_branches(project_path)
    # Shared inputs, computed ONCE per audit run: the git changed-files map
    # (one `git status` subprocess per work root) and the task-content index
    # (one read per task file) feed both the provenance and infra checks.
    changed_by_root: Dict[str, Optional[List[str]]] = {
        name: _git_changed_files(abs_path) for name, abs_path in work_roots.items()
    }
    task_index = _load_task_index(project_path) if work_roots else []
    warnings, attributions = _check_work_root_provenance(
        project_path, work_roots, pm_owns_product_branches, task_index, changed_by_root
    )
    if protocol_gate["status"] == GATE_MIGRATE:
        warnings.insert(0, {
            "kind": "protocol-version-migration",
            "detected_version": protocol_gate["detected_version"],
            "shipped_version": protocol_gate["shipped_version"],
            "detail": protocol_gate["detail"],
        })
    # Assignee scope boundary — infra artifacts require explicit task
    # authorization regardless of attribution.
    warnings.extend(_check_infra_artifacts(work_roots, task_index, changed_by_root))
    # Assignee output hygiene — planning identifiers leaked into changed
    # work-root files, caught by the pure-regex detection floor.
    warnings.extend(_check_pm_identifier_leaks(work_roots, changed_by_root))
    warnings.extend(review_warnings)
    warnings.extend(deliverable_warnings)
    warnings.extend(backlog_warnings)

    # Universal raw-edit detection floor. Runs as part of this ordinary CLI
    # command — no harness interception — so it is the portable floor: it
    # stands alone on any harness. `guard` entries are detected raw edits to
    # governed artifacts (a hard detection that fails the audit); `advisory`
    # entries are governed artifacts whose provenance cannot be established
    # (an honest notice that does not fail).
    provenance = audit_provenance(project_path)
    prov_guards: List[Dict[str, Any]] = provenance["guard"]
    prov_advisories: List[Dict[str, Any]] = provenance["advisory"]

    clean = (
        len(blockers) == 0
        and len(warnings) == 0
        and len(prov_guards) == 0
    )
    record: Dict[str, Any] = {
        "action": "plan-audit",
        "project_path": str(project_path),
        "clean": clean,
        "blockers": blockers,
        "warnings": warnings,
        "attributions": attributions,
        "provenance": provenance,
    }
    emit_record(record)

    # Provenance findings surface on their own machine-contract prefixes
    # (STANDARDS § Code Standards: `[guard]` for a detected violation,
    # `[advisory]` for an honest-tier notice) regardless of the lifecycle
    # blocker outcome below.
    for g in prov_guards:
        _stderr("guard", g["detail"])
    for adv in prov_advisories:
        _stderr("advisory", adv["detail"])

    if blockers:
        for b in blockers:
            _stderr("audit", b["detail"])
        return EXIT_FAIL
    for w in warnings:
        _stderr("warning", w["detail"])
    for a in attributions:
        _stderr("info", a["detail"])
    # A detected raw edit is a fail-closed detection: exit non-zero so the floor
    # is assertable on a no-interception path even when no lifecycle blocker fired.
    if prov_guards:
        return EXIT_FAIL
    return EXIT_OK
