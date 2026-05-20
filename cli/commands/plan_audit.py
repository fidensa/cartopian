"""`cartopian plan-audit <project-path>` — lifecycle and provenance audit."""
import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli.commands.resolve_config import _CliError, _load_project_config, _require_project_keys
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE

_TASK_ID_RE = re.compile(r"^TASK-(\d{2}-\d{3})")
_STATUS_DIRS = ("in-progress", "in-review")
_ALL_TASK_DIRS = ("open", "in-progress", "in-review", "done")
_WORK_ROOT_RE = re.compile(r"^Work root:\s*(.+)$", re.MULTILINE)
_ASSIGNEE_RE = re.compile(r"^Assignee:\s*(.+)$", re.MULTILINE)
_VERDICT_RE = re.compile(r"\bVerdict:\s*(approve|request-changes|reject)\b(?!\s*\|)")
_STATUS_RE = re.compile(r"^Status:\s*(.+)$", re.MULTILINE)


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


def _find_last_assignee(project_path: Path, work_root_name: str) -> Optional[Dict[str, str]]:
    """Find the most-recently-modified task naming this work_root and return
    {task_id, assignee, status} or None if no attribution can be made."""
    candidates: List[Tuple[float, str, str, str]] = []
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
            if work_root_name not in _read_work_root_names(content):
                continue
            assignee = _read_assignee(content)
            if not assignee:
                continue
            try:
                mtime = task_file.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, f"TASK-{m.group(1)}", assignee, status_dir))
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


def _check_artifact_chains(project_path: Path) -> List[Dict[str, Any]]:
    """Check that active tasks have their required artifacts in place."""
    blockers: List[Dict[str, Any]] = []

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
                    blockers.append({
                        "kind": "missing-review-artifact",
                        "task_id": task_id,
                        "task_path": str(task_file),
                        "expected": str(review),
                        "detail": f"{task_id} is in-review but has no review artifact at {review}",
                    })
                else:
                    try:
                        content = review.read_text(encoding="utf-8")
                    except OSError:
                        content = ""
                    vm = _VERDICT_RE.search(content)
                    if not vm:
                        blockers.append({
                            "kind": "review-missing-verdict",
                            "task_id": task_id,
                            "review_path": str(review),
                            "detail": f"review artifact for {task_id} has no Verdict: field",
                        })

    return blockers


def _check_work_root_provenance(
    project_path: Path,
    work_roots: Dict[str, str],
    pm_owns_product_branches: bool,
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
    active_assignments: Dict[str, List[str]] = {name: [] for name in work_roots}
    for status_dir in _STATUS_DIRS:
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
            for root_name in _read_work_root_names(content):
                if root_name in active_assignments:
                    active_assignments[root_name].append(f"TASK-{m.group(1)}")

    warnings: List[Dict[str, Any]] = []
    attributions: List[Dict[str, Any]] = []
    for name, abs_path in work_roots.items():
        changed = _git_changed_files(abs_path)
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
            attribution = _find_last_assignee(project_path, name)
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
        _require_project_keys(project_cfg, project_path / "cartopian.toml")
    except _CliError as err:
        _stderr(err.prefix, err.message)
        return err.exit_code

    blockers: List[Dict[str, Any]] = []
    blockers.extend(_check_artifact_chains(project_path))

    work_roots = _load_work_roots(project_path)
    pm_owns_product_branches = _resolve_pm_owns_product_branches(project_path)
    warnings, attributions = _check_work_root_provenance(
        project_path, work_roots, pm_owns_product_branches
    )

    clean = len(blockers) == 0 and len(warnings) == 0
    record: Dict[str, Any] = {
        "action": "plan-audit",
        "project_path": str(project_path),
        "clean": clean,
        "blockers": blockers,
        "warnings": warnings,
        "attributions": attributions,
    }
    emit_record(record)

    if blockers:
        for b in blockers:
            _stderr("audit", b["detail"])
        return EXIT_FAIL
    for w in warnings:
        _stderr("warning", w["detail"])
    for a in attributions:
        _stderr("info", a["detail"])
    return EXIT_OK
