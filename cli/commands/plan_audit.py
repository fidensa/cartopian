"""`cartopian plan-audit <project-path>` — lifecycle and provenance audit."""
import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE

_TASK_ID_RE = re.compile(r"^TASK-(\d{2}-\d{3})")
_STATUS_DIRS = ("in-progress", "in-review")
_WORK_ROOT_RE = re.compile(r"^Work root:\s*(.+)$", re.MULTILINE)
_VERDICT_RE = re.compile(r"^Verdict:\s*(.+)$", re.MULTILINE)
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


def _load_work_roots(project_path: Path) -> Dict[str, str]:
    """Return {name: abs_path} from cartopian.toml + cartopian.local.toml."""
    try:
        import tomllib
    except ImportError:
        return {}

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
) -> List[Dict[str, Any]]:
    """Collect non-blocking warnings for dirty work_roots with ambiguous task provenance."""
    if not work_roots:
        return []

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
    for name, abs_path in work_roots.items():
        changed = _git_changed_files(abs_path)
        if changed is None or not changed:
            continue
        assigned_tasks = active_assignments.get(name, [])
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
            has_prompt = any(
                (project_path / "prompts" / f"PROMPT-{tid.replace('TASK-', '')}.md").is_file()
                for tid in assigned_tasks
            )
            if not has_prompt:
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

    return warnings


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

    blockers: List[Dict[str, Any]] = []
    blockers.extend(_check_artifact_chains(project_path))

    work_roots = _load_work_roots(project_path)
    warnings = _check_work_root_provenance(project_path, work_roots)

    clean = len(blockers) == 0 and len(warnings) == 0
    record: Dict[str, Any] = {
        "action": "plan-audit",
        "project_path": str(project_path),
        "clean": clean,
        "blockers": blockers,
        "warnings": warnings,
    }
    emit_record(record)

    if blockers:
        for b in blockers:
            _stderr("audit", b["detail"])
        return EXIT_FAIL
    for w in warnings:
        _stderr("warning", w["detail"])
    return EXIT_OK
